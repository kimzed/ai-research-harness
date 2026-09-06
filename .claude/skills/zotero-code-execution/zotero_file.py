# /// script
# requires-python = ">=3.12"
# dependencies = ["requests"]
# ///
"""zotero_file.py -- Zotero local write-mechanism wrapper for the
`zotero-code-execution` Claude Code skill (CAP-4, AD-2, AD-3, AD-4, AD-8).

Implements story 5's AD-3 decision (approved 2026-09-06 -- see the story's
Spec Change Log): writes go through Zotero desktop's local Connector HTTP
endpoint (`POST /connector/saveItems`, undocumented but functional -- never
Zotero's cloud web API), and every resolve/dup-check read goes through
Better BibTeX's local JSON-RPC endpoint (`/better-bibtex/json-rpc`), which
is the only mechanism that can recover a Better-BibTeX citation key at
all. Both endpoints live on the same local Zotero HTTP server
(127.0.0.1:23119) -- nothing here ever leaves the machine.

Four modes, mutually exclusive:
    --check-target                       Show the collection Zotero would
                                          file into right now (whatever's
                                          selected in the desktop UI).
    --check-duplicate IDENTIFIER_JSON     Live-identifier search only, no
                                          write. IDENTIFIER_JSON is the
                                          shared flat shape
                                          {"doi":..,"title":..,"arxiv_id":..}
                                          that `lit-search` also emits, and
                                          must have at least one non-empty
                                          field -- an empty identifier is
                                          rejected rather than silently
                                          reporting "no duplicate."
    --resolve IDENTIFIER_JSON             Re-resolve an already-filed item
                                          live (citekey/item key/attachments/
                                          fields) -- AD-4's "never cache
                                          across invocations" made concrete.
    --file IDENTIFIER_JSON --item ITEM_JSON --researcher-confirmed
                                          Dup-check, then file a new item,
                                          then resolve + confirm citekey in
                                          the same call. `--researcher-
                                          confirmed` is mandatory and is not
                                          a formality: Claude Code must only
                                          pass it after the researcher has
                                          explicitly confirmed *this*
                                          candidate in conversation -- never
                                          after just showing search results
                                          (PRD "never auto-file", AD-4).
                                          IDENTIFIER_JSON's doi/title must
                                          be consistent with ITEM_JSON's
                                          DOI/title -- a mismatch is
                                          rejected rather than filed.

Optional for --file: `--collection-id C83` / `--collection-name "..."` to
move the newly filed item into a specific collection (via `/connector/
updateSession`) instead of accepting whatever's currently selected in the
Zotero desktop UI (AD-2's default). Both are validated against the live
target list before anything is written. `--override-duplicate-match` lets
the researcher explicitly file anyway after reviewing a *title-only* fuzzy
match that they've confirmed is a different paper -- it never overrides an
exact DOI/arXiv match (that block cannot be bypassed by a flag).

Always prints exactly one JSON object to stdout, never a raw Connector/BBT
response and never a raw Python traceback (AD-8). Status is "ok" (exit 0),
"halt" (exit 2 -- an Ask-First condition, e.g. Zotero not running,
ambiguous collection name, or an un-overridable duplicate), or "error"
(exit 1 -- invalid input or an unexpected HTTP/RPC failure).

This script never checks Required-field completeness itself -- it only
returns the filed/resolved item's actual current fields (Better BibTeX's
CSL-JSON shape, under "resolved_fields") so Claude Code can run the
negotiated checklist from CLAUDE.md's "Citation-format & field contract"
section and drive that spec's external-lookup-then-ask chain on a gap.
See SKILL.md for the CSL-JSON -> checklist field-name mapping.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from typing import Any

import requests

CONNECTOR_BASE = "http://localhost:23119/connector"
BBT_RPC_URL = "http://localhost:23119/better-bibtex/json-rpc"
REQUEST_TIMEOUT_SECONDS = 15

# Bounded read-after-write retry for BBT's index catching up right after a
# saveItems commit -- NOT the "no silent retry loop" Zotero-not-running case
# below, which never retries at all.
RESOLVE_RETRIES = 5
RESOLVE_RETRY_DELAY_SECONDS = 0.8


class ZoteroNotRunningError(RuntimeError):
    """Zotero desktop's local HTTP server (127.0.0.1:23119) is unreachable."""


class ZoteroTimeoutError(RuntimeError):
    """Zotero's local HTTP server didn't respond in time -- not the same as
    it being closed (Zotero may just be busy)."""


class ConnectorError(RuntimeError):
    """The Connector endpoint returned an unexpected non-2xx response."""


class BetterBibTeXError(RuntimeError):
    """The Better BibTeX JSON-RPC endpoint returned an error, or a response
    shaped unlike anything the real API returns."""


class InvalidInputError(RuntimeError):
    """The caller-supplied identifier/item JSON is missing, malformed, or
    internally inconsistent -- rejected before any Zotero call is made."""


# ---------------------------------------------------------------------------
# Small defensive helpers
# ---------------------------------------------------------------------------


def _clean_str(value: Any) -> str:
    """Best-effort string coercion: a non-string value (e.g. a caller
    passed an int where a DOI string belongs) degrades to "" rather than
    crashing `.strip()`."""
    return value.strip() if isinstance(value, str) else ""


def _public_match(match: dict[str, Any]) -> dict[str, Any]:
    """Strip the internal full-CSL-record cache (`_raw`) before a match
    dict is put in output JSON -- callers get the flat summary shape."""
    return {k: v for k, v in match.items() if k != "_raw"}


def _attachment_key(attachment: dict[str, Any]) -> str | None:
    """Best-effort extraction of the attachment's Zotero item key out of
    Better BibTeX's `item.attachments` "path" field (the storage directory
    name, i.e. the second-to-last path segment). Returns None rather than
    raising if the path is missing or too short to contain one."""
    path = attachment.get("path")
    if not isinstance(path, str):
        return None
    segments = path.rstrip("/").split("/")
    if len(segments) < 2:
        return None
    return segments[-2]


def validate_identifier(identifier: Any, label: str) -> dict[str, str]:
    """Validate + normalize the shared flat identifier shape for `label`
    (e.g. "--file"). Raises InvalidInputError rather than letting a
    malformed or empty identifier silently make the duplicate-check a
    no-op (an empty `{}` would otherwise find zero matches every time)."""
    if not isinstance(identifier, dict):
        raise InvalidInputError(
            f"{label} must be a JSON object like "
            '{"doi":"...","title":"...","arxiv_id":null}, got '
            f"{type(identifier).__name__}."
        )
    for key in ("doi", "title", "arxiv_id"):
        value = identifier.get(key)
        if value is not None and not isinstance(value, str):
            raise InvalidInputError(
                f"{label}[{key!r}] must be a string or null, got "
                f"{type(value).__name__}."
            )
    doi = _clean_str(identifier.get("doi"))
    title = _clean_str(identifier.get("title"))
    arxiv_id = _clean_str(identifier.get("arxiv_id"))
    if not doi and not title and not arxiv_id:
        raise InvalidInputError(
            f"{label} has no usable doi/title/arxiv_id -- at least one must "
            "be a non-empty string. An empty identifier would let the "
            "duplicate-check silently find nothing and file blind."
        )
    return {"doi": doi, "title": title, "arxiv_id": arxiv_id}


def validate_item_matches_identifier(identifier: dict[str, str], item: dict[str, Any]) -> None:
    """--file takes IDENTIFIER_JSON (for the dup-check) and ITEM_JSON (the
    write payload) as separate arguments -- nothing else catches them
    silently referring to two different papers. Compares DOI first (when
    the identifier has one), else title."""
    id_doi = identifier.get("doi") or ""
    item_doi = _clean_str(item.get("DOI"))
    if id_doi and item_doi and id_doi.lower() != item_doi.lower():
        raise InvalidInputError(
            f"--file's identifier DOI ({id_doi!r}) does not match --item's "
            f"DOI ({item.get('DOI')!r}) -- they must refer to the same "
            "paper. Fix whichever one is wrong before filing."
        )
    if not id_doi:
        id_title = identifier.get("title") or ""
        item_title = _clean_str(item.get("title"))
        if id_title and item_title and id_title.lower() != item_title.lower():
            raise InvalidInputError(
                f"--file's identifier title ({id_title!r}) does not match "
                f"--item's title ({item.get('title')!r}) -- they must refer "
                "to the same paper. Fix whichever one is wrong before "
                "filing."
            )


# ---------------------------------------------------------------------------
# Low-level HTTP -- single attempt, no retry-on-connection-failure
# (matrix: "Zotero desktop not running" -> HALT, never a silent retry loop)
# ---------------------------------------------------------------------------


def _post(url: str, body: dict[str, Any]) -> requests.Response:
    try:
        return requests.post(
            url,
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"Content-Type": "application/json"},
        )
    except requests.exceptions.Timeout as exc:
        raise ZoteroTimeoutError(
            "Zotero's local HTTP server at localhost:23119 did not respond "
            f"within {REQUEST_TIMEOUT_SECONDS}s. This means Zotero may be "
            "running but busy/unresponsive -- it is NOT the same as Zotero "
            "being closed. Ask the researcher to check Zotero rather than "
            "assuming it needs to be started."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise ZoteroNotRunningError(
            "Could not reach Zotero's local HTTP server at "
            "localhost:23119. Zotero desktop must be running, with "
            "'Allow other applications on this computer to communicate "
            "with Zotero' enabled (Settings -> Advanced). Ask the "
            "researcher to start/check Zotero -- do not retry silently."
        ) from exc


def connector_post(path: str, body: dict[str, Any]) -> tuple[int, Any]:
    response = _post(f"{CONNECTOR_BASE}{path}", body)
    content_type = response.headers.get("Content-Type", "")
    parsed = None
    if response.content and "application/json" in content_type:
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
    return response.status_code, parsed


def bbt_call(method: str, params: list[Any]) -> Any:
    response = _post(BBT_RPC_URL, {"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
    try:
        payload = response.json()
    except ValueError as exc:
        raise BetterBibTeXError(
            f"Better BibTeX JSON-RPC ({method}) returned a non-JSON response "
            f"(HTTP {response.status_code}). Is Better BibTeX installed and "
            "up to date?"
        ) from exc
    if not isinstance(payload, dict):
        raise BetterBibTeXError(
            f"Better BibTeX JSON-RPC ({method}) returned an unexpected "
            f"response shape ({type(payload).__name__}, expected a JSON "
            "object)."
        )
    if "error" in payload:
        raise BetterBibTeXError(
            f"Better BibTeX JSON-RPC ({method}) returned an error: "
            f"{payload['error']}"
        )
    return payload.get("result")


# ---------------------------------------------------------------------------
# Item-key / library parsing out of BBT's CSL-JSON "id" URI
# (e.g. "http://zotero.org/users/6643814/items/34YTRHYI")
# ---------------------------------------------------------------------------


def parse_item_uri(uri: str) -> dict[str, str | None]:
    parts = uri.rstrip("/").split("/")
    # [..., "zotero.org", "users"|"groups", "<lib_id>", "items", "<item_key>"]
    try:
        items_index = parts.index("items")
        item_key = parts[items_index + 1]
        library_type = parts[items_index - 2]
        library_id = parts[items_index - 1]
    except (ValueError, IndexError):
        return {"item_key": None, "library_type": None, "library_id": None}
    return {"item_key": item_key, "library_type": library_type, "library_id": library_id}


# ---------------------------------------------------------------------------
# Live-identifier search (AD-4) -- never by citekey, only DOI/title/arXiv id
# ---------------------------------------------------------------------------


def search_matches(identifier: dict[str, Any]) -> list[dict[str, Any]]:
    """Search the live library across DOI, arXiv id, and title.

    Returns a list of match dicts, each tagged with a "confidence":
    "doi_exact" | "arxiv" | "title_contains" -- the caller decides how to
    treat each tier (see --file's duplicate-blocking rule below). Matches
    are deduplicated by their CSL "id" URI across the three lookups. Each
    match also carries the full raw CSL-JSON record under "_raw" (stripped
    before being put in any output JSON -- see `_public_match`) so a
    caller resolving the item doesn't need a second query to get its
    fields, regardless of which identifier field matched.
    """
    doi = _clean_str(identifier.get("doi"))
    arxiv_id = _clean_str(identifier.get("arxiv_id"))
    title = _clean_str(identifier.get("title"))

    seen_ids: set[str] = set()
    matches: list[dict[str, Any]] = []

    def add(results: Any, confidence: str) -> None:
        if not isinstance(results, list):
            return
        for entry in results:
            if not isinstance(entry, dict):
                continue
            uri = entry.get("id")
            if not isinstance(uri, str) or not uri or uri in seen_ids:
                continue
            seen_ids.add(uri)
            parsed = parse_item_uri(uri)
            matches.append(
                {
                    "confidence": confidence,
                    "citekey": entry.get("citekey") or entry.get("citation-key"),
                    "item_key": parsed["item_key"],
                    "library": entry.get("library"),
                    "title": entry.get("title"),
                    "doi": entry.get("DOI"),
                    "_raw": entry,
                }
            )

    if doi:
        add(bbt_call("item.search", [[["DOI", "is", doi]]]), "doi_exact")
    if arxiv_id:
        add(bbt_call("item.search", [[["extra", "contains", arxiv_id]]]), "arxiv")
    if title:
        add(bbt_call("item.search", [[["title", "contains", title]]]), "title_contains")

    return matches


# ---------------------------------------------------------------------------
# --check-target
# ---------------------------------------------------------------------------


def get_selected_collection() -> dict[str, Any]:
    status, body = connector_post("/getSelectedCollection", {})
    if status != 200 or not isinstance(body, dict):
        raise ConnectorError(
            f"/connector/getSelectedCollection returned unexpected HTTP {status}."
        )
    collection = None
    if body.get("id"):
        collection = {"id": body["id"], "name": body.get("name")}
    return {
        "library": {
            "id": body.get("libraryID"),
            "name": body.get("libraryName"),
            "editable": body.get("libraryEditable"),
        },
        "collection": collection,
        "targets": [
            {"id": t.get("id"), "name": t.get("name"), "level": t.get("level")}
            for t in (body.get("targets") or [])
            if isinstance(t, dict)
        ],
    }


def run_check_target() -> dict[str, Any]:
    target = get_selected_collection()
    return {
        "status": "ok",
        "library": target["library"],
        "collection": target["collection"],
        "message": (
            f"Filing right now would land in "
            f"{target['collection']['name'] if target['collection'] else target['library']['name']}"
            " (whatever's currently selected in the Zotero desktop UI, per "
            "AD-2's default). If that's not clearly right for this paper, "
            "ask the researcher which collection to use rather than "
            "guessing -- pass --collection-id/--collection-name to --file "
            "to target a different one. Full selectable list is in "
            "\"targets\"."
        ),
        "targets": target["targets"],
    }


# ---------------------------------------------------------------------------
# --check-duplicate / --resolve
# ---------------------------------------------------------------------------


def run_check_duplicate(identifier: dict[str, Any]) -> dict[str, Any]:
    matches = search_matches(identifier)
    return {
        "status": "ok",
        "duplicate_found": len(matches) > 0,
        "matches": [_public_match(m) for m in matches],
        "message": (
            "No existing item found for this identifier."
            if not matches
            else (
                f"{len(matches)} existing match(es) found. Any \"doi_exact\" "
                "or \"arxiv\" match is authoritative -- do not file. A "
                "\"title_contains\" match is heuristic (substring search) -- "
                "show it to the researcher and let them judge before filing "
                "either way."
            )
        ),
    }


def resolve_item(identifier: dict[str, Any], retries: int = 1) -> dict[str, Any] | None:
    """Resolve the single best (highest-confidence) live match, retrying
    briefly to absorb BBT's index catching up right after a write.

    Right after `saveItems` commits, the item is immediately findable by
    identifier, but Better BibTeX assigns its citation key via a slightly
    later event -- so the first search(es) after a write can return a real
    match whose "citekey" is still null. Retrying on "no match with a
    citekey yet" (not merely "no match yet") is what actually absorbs that
    race; confirmed live during the story 5 spike.

    Returns None only when literally nothing matched. When something
    matched but never got a citekey within the retry budget, still returns
    a dict (with "citekey": None) -- callers that require a citekey (i.e.
    --file's success path) must check for that explicitly rather than
    treating "truthy return" as "safe to report success."
    """
    priority = {"doi_exact": 0, "arxiv": 1, "title_contains": 2}
    last_matches: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    for attempt in range(retries):
        last_matches = search_matches(identifier)
        with_citekey = [m for m in last_matches if m.get("citekey")]
        if with_citekey:
            best = sorted(with_citekey, key=lambda m: priority.get(m["confidence"], 9))[0]
            break
        if attempt < retries - 1:
            time.sleep(RESOLVE_RETRY_DELAY_SECONDS)
    if best is None:
        if not last_matches:
            return None
        # Matches exist but none ever got a citekey within the retry budget.
        best = sorted(last_matches, key=lambda m: priority.get(m["confidence"], 9))[0]

    citekey = best.get("citekey")
    if not citekey:
        return {
            **_public_match(best),
            "resolved_fields": None,
            "fields_lookup_failed": True,
            "item_type_csl": None,
            "attachment_keys": [],
            "collections": {},
        }

    # The match's own "_raw" is already the full CSL-JSON record from
    # whichever search found it (DOI/arXiv/title) -- no second query needed,
    # and this works even when only an arXiv id matched (no DOI/title to
    # re-query by), unlike an approach that only re-fetches by DOI/title.
    raw = best.get("_raw")
    fields = raw if isinstance(raw, dict) else None

    attachments_result = bbt_call("item.attachments", [citekey])
    attachments = attachments_result if isinstance(attachments_result, list) else []

    collections_result = bbt_call("item.collections", [[citekey]])
    collections_result = collections_result if isinstance(collections_result, dict) else {}

    return {
        **_public_match(best),
        "resolved_fields": fields,
        "fields_lookup_failed": fields is None,
        "item_type_csl": (fields or {}).get("type"),
        "attachment_keys": [
            key for key in (_attachment_key(a) for a in attachments if isinstance(a, dict)) if key
        ],
        "collections": collections_result.get(citekey, []),
    }


def run_resolve(identifier: dict[str, Any]) -> dict[str, Any]:
    resolved = resolve_item(identifier, retries=1)
    if not resolved:
        return {
            "status": "ok",
            "found": False,
            "message": "No existing item found for this identifier.",
        }
    return {"status": "ok", "found": True, **resolved}


# ---------------------------------------------------------------------------
# --file
# ---------------------------------------------------------------------------


def resolve_collection_target(args: argparse.Namespace) -> tuple[str | None, dict[str, Any] | None]:
    """Returns (treeViewID or None, halt_payload or None).

    Both --collection-id and --collection-name are validated against the
    live target list before anything is written -- an unchecked
    --collection-id could otherwise silently target a stale/nonexistent
    id with no feedback. --collection-name matching is case-insensitive
    and whitespace-trimmed.
    """
    if not args.collection_id and not args.collection_name:
        return None, None

    target = get_selected_collection()

    if args.collection_id:
        valid_ids = {t["id"] for t in target["targets"]}
        if args.collection_id not in valid_ids:
            return None, {
                "status": "halt",
                "reason": "collection_not_found",
                "message": (
                    f"No collection with id {args.collection_id!r} was found "
                    "among the current filing targets. Ask the researcher "
                    "which collection they mean rather than guessing."
                ),
                "targets": target["targets"],
            }
        return args.collection_id, None

    wanted = args.collection_name.strip().lower()
    matches = [
        t for t in target["targets"] if (t.get("name") or "").strip().lower() == wanted
    ]
    if not matches:
        return None, {
            "status": "halt",
            "reason": "collection_not_found",
            "message": (
                f"No collection named {args.collection_name!r} was found "
                "in the current filing targets. Ask the researcher which "
                "collection they mean rather than guessing."
            ),
            "targets": target["targets"],
        }
    if len(matches) > 1:
        return None, {
            "status": "halt",
            "reason": "ambiguous_collection_name",
            "message": (
                f"{len(matches)} collections are named {args.collection_name!r} "
                "(this library has same-named collections nested under "
                "different parents). Ask the researcher which one they mean "
                "-- pass --collection-id with the specific id below instead "
                "of --collection-name."
            ),
            "candidates": matches,
        }
    return matches[0]["id"], None


def run_file(args: argparse.Namespace, identifier: dict[str, str], item: dict[str, Any]) -> dict[str, Any]:
    # 0. IDENTIFIER_JSON and ITEM_JSON must refer to the same paper --
    #    nothing else catches the two arguments silently disagreeing.
    validate_item_matches_identifier(identifier, item)

    # 1. Live-identifier dup-check first (AD-4) -- filing proceeds only when
    #    none is found, unless the researcher explicitly overrides a
    #    title-only heuristic match (never an exact DOI/arXiv one).
    matches = search_matches(identifier)
    public_matches = [_public_match(m) for m in matches]
    blocking = [m for m in matches if m["confidence"] in ("doi_exact", "arxiv")]
    fuzzy_only = matches and not blocking

    if blocking:
        labels = sorted(
            {(m.get("citekey") or f"item_key:{m.get('item_key')}") for m in blocking}
        )
        return {
            "status": "ok",
            "filed": False,
            "duplicate_found": True,
            "matches": public_matches,
            "message": (
                "An existing item already matches this identifier by DOI/arXiv "
                "id -- no new item was filed. Existing citekey(s)/item key(s): "
                + ", ".join(labels)
                + ". This cannot be overridden by --override-duplicate-match."
            ),
        }
    if fuzzy_only and not args.override_duplicate_match:
        return {
            "status": "ok",
            "filed": False,
            "duplicate_found": True,
            "matches": public_matches,
            "message": (
                "Only a title-substring match was found (no DOI/arXiv id "
                "confirms it), so this is not certain. Show these matches to "
                "the researcher: if they confirm this is a different paper, "
                "re-run with --override-duplicate-match; otherwise treat the "
                "existing citekey above as the paper already filed."
            ),
        }

    # 2. Resolve the current filing target (or the researcher's override) --
    #    validated against the live target list before any write happens.
    target_id, halt = resolve_collection_target(args)
    if halt:
        return halt

    # 3. Write via the Connector endpoint -- AD-3's local write mechanism.
    #    zotero_file.py always assigns its own client-side item id (needed
    #    to move the item via updateSession in step 3b) -- any caller
    #    -supplied "id" in --item is intentionally overwritten; documented
    #    in SKILL.md.
    session_id = uuid.uuid4().hex
    client_item_id = uuid.uuid4().hex[:12]
    payload = {**item, "id": client_item_id}
    save_status, _ = connector_post(
        "/saveItems",
        {
            "sessionID": session_id,
            "items": [payload],
            "uri": item.get("url") or "https://zotero-code-execution.local/filed",
        },
    )
    if save_status == 500:
        raise ConnectorError(
            "/connector/saveItems returned 500 -- the current library may "
            "not be editable. Ask the researcher to check their Zotero "
            "selection/permissions."
        )
    if not (200 <= save_status < 300):
        raise ConnectorError(f"/connector/saveItems returned unexpected HTTP {save_status}.")

    if target_id:
        move_status, _ = connector_post(
            "/updateSession", {"sessionID": session_id, "target": target_id}
        )
        if not (200 <= move_status < 300):
            # The item already exists (the write above succeeded) -- surface
            # whatever we can recover so the researcher isn't left thinking
            # nothing happened and doesn't risk re-filing a duplicate.
            partial = resolve_item(identifier, retries=RESOLVE_RETRIES) or {}
            return {
                "status": "error",
                "reason": "move_to_collection_failed",
                "filed": True,
                "message": (
                    f"The item was written to Zotero successfully (HTTP "
                    f"{save_status}), but moving it into the requested "
                    f"collection failed (HTTP {move_status}). The item "
                    "already exists -- do NOT file it again. Report its "
                    "current citekey/location below to the researcher; they "
                    "may need to move it manually in the Zotero desktop UI."
                ),
                "citekey": partial.get("citekey"),
                "item_key": partial.get("item_key"),
                "library": partial.get("library"),
                "collections": partial.get("collections"),
            }

    # 4. Re-resolve live to recover citekey + item key + attachment key,
    #    confirmed back to the researcher in this same call (AD-3/AD-4).
    #    A match with no citekey yet counts the same as no match at all --
    #    never report "filed": true / "status": "ok" without a citekey.
    resolved = resolve_item(identifier, retries=RESOLVE_RETRIES)
    if not resolved or not resolved.get("citekey"):
        return {
            "status": "error",
            "reason": "resolve_after_write_failed",
            "message": (
                "The item was written to Zotero (HTTP "
                f"{save_status}) but a citekey could not be recovered "
                "afterward -- Better BibTeX may still be indexing/assigning "
                "it. Do NOT report a citekey or \"filed\": true to the "
                "researcher. The item already exists -- re-run --resolve "
                "for this identifier in a moment rather than filing again."
            ),
            "item_key": (resolved or {}).get("item_key"),
            "library": (resolved or {}).get("library"),
        }

    fields_lookup_failed = resolved.get("fields_lookup_failed", False)
    return {
        "status": "ok",
        "filed": True,
        "duplicate_found": False,
        "citekey": resolved.get("citekey"),
        "item_key": resolved.get("item_key"),
        "library": resolved.get("library"),
        "collections": resolved.get("collections"),
        "attachment_keys": resolved.get("attachment_keys", []),
        "item_type_csl": resolved.get("item_type_csl"),
        "resolved_fields": resolved.get("resolved_fields"),
        "fields_lookup_failed": fields_lookup_failed,
        "message": (
            f"Filed and confirmed: citekey {resolved.get('citekey')!r} "
            f"(item key {resolved.get('item_key')}) in "
            f"{resolved.get('library')}. "
            + (
                "WARNING: its fields could not be re-fetched, so the "
                "Required-field checklist cannot run yet -- re-run "
                "--resolve for this identifier before telling the "
                "researcher this citekey is ready to cite."
                if fields_lookup_failed
                else "Now run the Required-field checklist (CLAUDE.md "
                "citation-format & field contract) against "
                "\"resolved_fields\" before telling the researcher this "
                "citekey is ready to cite."
            )
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="zotero_file: local Zotero filing/dup-check/resolve via the Connector + Better BibTeX endpoints."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-target", action="store_true", help="Show the collection filing would land in right now.")
    mode.add_argument("--check-duplicate", metavar="IDENTIFIER_JSON", help='Live-identifier search only, e.g. \'{"doi":"10.x/y","title":"...","arxiv_id":null}\'.')
    mode.add_argument("--resolve", metavar="IDENTIFIER_JSON", help="Re-resolve an already-filed item live by identifier.")
    mode.add_argument("--file", metavar="IDENTIFIER_JSON", help="File a new item after dup-check, then resolve + confirm citekey.")

    parser.add_argument("--item", metavar="ITEM_JSON", help='Zotero item payload for --file, e.g. \'{"itemType":"journalArticle","title":"...","creators":[...],"DOI":"...","date":"..."}\'.')
    parser.add_argument("--researcher-confirmed", action="store_true", help="Required for --file. Only pass this after the researcher has explicitly confirmed this exact candidate in conversation -- never after just showing search results.")
    parser.add_argument("--collection-id", metavar="TREE_VIEW_ID", help='Move the filed item into this collection (e.g. "C83") instead of whatever is currently selected in Zotero. Validated against the live target list.')
    parser.add_argument("--collection-name", metavar="NAME", help="Same as --collection-id but by name (case-insensitive, trimmed); halts if the name is ambiguous or not found.")
    parser.add_argument("--override-duplicate-match", action="store_true", help="File anyway after the researcher reviews a title-only fuzzy match and confirms it is a different paper. Never overrides an exact DOI/arXiv match.")
    return parser


def parse_json_arg(raw: str, label: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "error", "reason": "invalid_json", "message": f"{label} is not valid JSON: {exc}"}, indent=2))
        sys.exit(1)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    file_only_flags_set = (
        args.item is not None
        or args.researcher_confirmed
        or args.collection_id is not None
        or args.collection_name is not None
        or args.override_duplicate_match
    )
    if args.file is not None:
        if not args.researcher_confirmed:
            parser.error("--file requires --researcher-confirmed -- only pass it after the researcher has explicitly confirmed this candidate.")
        if not args.item:
            parser.error("--file requires --item with the Zotero item payload to write.")
        if args.collection_id and args.collection_name:
            parser.error("Pass at most one of --collection-id / --collection-name.")
    elif file_only_flags_set:
        parser.error("--item/--researcher-confirmed/--collection-id/--collection-name/--override-duplicate-match only apply to --file.")

    try:
        if args.check_target:
            result = run_check_target()
        elif args.check_duplicate is not None:
            identifier = validate_identifier(parse_json_arg(args.check_duplicate, "--check-duplicate"), "--check-duplicate")
            result = run_check_duplicate(identifier)
        elif args.resolve is not None:
            identifier = validate_identifier(parse_json_arg(args.resolve, "--resolve"), "--resolve")
            result = run_resolve(identifier)
        else:
            identifier = validate_identifier(parse_json_arg(args.file, "--file"), "--file")
            item = parse_json_arg(args.item, "--item")
            if not isinstance(item, dict):
                raise InvalidInputError(f"--item must be a JSON object, got {type(item).__name__}.")
            result = run_file(args, identifier, item)
    except ZoteroTimeoutError as exc:
        print(json.dumps({"status": "halt", "reason": "zotero_timeout", "message": str(exc)}, indent=2))
        return 2
    except ZoteroNotRunningError as exc:
        print(json.dumps({"status": "halt", "reason": "zotero_not_running", "message": str(exc)}, indent=2))
        return 2
    except InvalidInputError as exc:
        print(json.dumps({"status": "error", "reason": "invalid_input", "message": str(exc)}, indent=2))
        return 1
    except (ConnectorError, BetterBibTeXError) as exc:
        print(json.dumps({"status": "error", "reason": "zotero_api_error", "message": str(exc)}, indent=2))
        return 1
    except requests.RequestException as exc:
        print(json.dumps({"status": "error", "reason": "http_error", "message": str(exc)}, indent=2))
        return 1
    except Exception as exc:  # last-resort safety net -- AD-8: always one JSON object, never a raw traceback.
        print(json.dumps({"status": "error", "reason": "unexpected_error", "message": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1

    print(json.dumps(result, indent=2))
    if result.get("status") == "halt":
        return 2
    if result.get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
