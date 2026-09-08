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

Six modes, mutually exclusive:
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
    --get-content IDENTIFIER_JSON         Pull a resolved item's content
                                          into context (CAP-5, AD-5): tries
                                          each attachment's `.zotero-ft-
                                          cache` sidecar first (across every
                                          attachment key), then each
                                          attachment's local PDF path
                                          (across every key again -- if an
                                          attachment folder somehow has more
                                          than one `.pdf`, the alphabetically
                                          -first one wins), then falls back
                                          to the CSL `abstract`. Read-only
                                          (never writes to `~/Zotero/
                                          storage/`, only reads from it) and
                                          resolves live like --resolve.
    --list-collection COLLECTION_REF      Browse -- list every paper already
                                          filed in a collection (by "C<id>"
                                          from --check-target's "targets", or
                                          by name), recursing into sub-
                                          collections by default. Read-only;
                                          see "Browsing" below for how this
                                          differs mechanically from the other
                                          modes.
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

Browsing (--list-collection): the Connector and Better BibTeX endpoints
above have no "list everything in collection X" call -- BBT's item.search
does accept a "collection" condition in principle, but every value shape
this Zotero/BBT version accepts for it either returns nothing or throws an
internal error (tried live: collection key, collectionID as string, as
int -- verified not worth building on). So --list-collection instead opens
the local zotero.sqlite file directly, read-only. This is the ONE mode in
this script that isn't Connector/BBT-HTTP-based; it never writes, and (like
the other modes) nothing it reads ever leaves the machine. Zotero desktop
holds a lock on the file for its entire run, not just during writes -- a
plain read-only SQLite connection gets refused the whole time, confirmed
live -- so this uses SQLite's `immutable=1` open mode, which bypasses
locking rather than negotiating it. That's a deliberate, accepted trade-off
for a browse-only convenience query: a single fast SELECT can at worst
return a momentarily-stale snapshot if it races an in-progress write, never
corruption, and this mode never feeds a write decision by itself -- filing
still always re-checks live via --check-duplicate/--file. Set
ZOTERO_SQLITE_PATH if the researcher's Zotero data directory isn't the
default `~/Zotero/zotero.sqlite`.

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

This script never runs the field-completeness check itself -- it only
returns the filed/resolved item's actual current fields (Better BibTeX's
CSL-JSON shape, under "resolved_fields") so Claude Code can run the
negotiated checklist from the repo root's citation-contract.md (generated by
the onboarding skill) and drive the external-lookup-then-ask chain on a gap.
See SKILL.md's "Field-completeness check" for the CSL-JSON -> checklist
field-name mapping, and for what to do when that contract is missing or still
un-negotiated (HALT and point at the onboarding skill).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
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

# --list-collection reads zotero.sqlite directly (see module docstring's
# "Browsing" section for why). A plain read-only connection is retried a
# few times first (it can occasionally succeed between Zotero's own writes)
# before falling back to the immutable=1 open mode that bypasses locking
# entirely.
ZOTERO_SQLITE_ENV_VAR = "ZOTERO_SQLITE_PATH"
DEFAULT_ZOTERO_SQLITE_PATH = os.path.expanduser("~/Zotero/zotero.sqlite")
SQLITE_LOCKED_RETRIES = 3
SQLITE_LOCKED_RETRY_DELAY_SECONDS = 0.3
NON_PAPER_ITEM_TYPES = {"attachment", "note", "annotation"}

# --get-content (CAP-5, AD-5) reads each attachment's storage directory
# directly off disk -- confirmed live as zotero.sqlite's sibling dir. Set
# ZOTERO_STORAGE_PATH if the researcher's Zotero data directory isn't the
# default `~/Zotero/storage`. Mirrors ZOTERO_SQLITE_ENV_VAR above.
ZOTERO_STORAGE_ENV_VAR = "ZOTERO_STORAGE_PATH"
DEFAULT_ZOTERO_STORAGE_PATH = os.path.expanduser("~/Zotero/storage")


class ZoteroNotRunningError(RuntimeError):
    """Zotero desktop's local HTTP server (127.0.0.1:23119) is unreachable."""


class ZoteroDatabaseError(RuntimeError):
    """The local zotero.sqlite file (--list-collection only) couldn't be
    opened or read."""


class ZoteroStorageError(RuntimeError):
    """Zotero's local attachment storage directory (--get-content only)
    couldn't be found -- distinct from "this item genuinely has no
    content": a missing/misconfigured ZOTERO_STORAGE_PATH would otherwise
    make every attachment lookup silently fail and look identical to a
    real no-content-available result."""


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
    # SKILL.md's "Field-completeness check" is a standing check on every
    # touch, and a --resolve IS a touch -- carry the same reminder --file and
    # --get-content already carry, so the documented contract holds for all
    # three modes rather than only two.
    return {
        "status": "ok",
        "found": True,
        **resolved,
        "note": _FIELD_CHECKLIST_REMINDER,
    }


# ---------------------------------------------------------------------------
# --get-content -- CAP-5, AD-5: fulltext-cache -> local PDF -> CSL abstract.
# Reuses resolve_item() (live per AD-4, no caching across invocations) for
# the item + its attachment_keys, then reads each attachment's storage
# directory directly off disk -- confirmed live: `.zotero-ft-cache` can
# exist even when the sibling `.pdf` isn't synced locally.
# ---------------------------------------------------------------------------


def _attachment_storage_dir(attachment_key: str) -> str:
    storage_path = os.environ.get(ZOTERO_STORAGE_ENV_VAR) or DEFAULT_ZOTERO_STORAGE_PATH
    return os.path.join(storage_path, attachment_key)


def _require_storage_root() -> str:
    """Returns the configured storage root if it actually exists, else
    raises ZoteroStorageError -- mirrors _open_zotero_db's clear-error
    pattern below (same idea: a missing/misconfigured local path gets a
    named, actionable error rather than silently degrading into what
    looks like "this item has no content")."""
    storage_path = os.environ.get(ZOTERO_STORAGE_ENV_VAR) or DEFAULT_ZOTERO_STORAGE_PATH
    if not os.path.isdir(storage_path):
        raise ZoteroStorageError(
            f"Zotero's local attachment storage directory wasn't found at "
            f"{storage_path!r}. Set {ZOTERO_STORAGE_ENV_VAR} if the "
            "researcher's Zotero data directory isn't the default "
            "(~/Zotero/storage), or ask them where it lives."
        )
    return storage_path


def _find_ft_cache(attachment_key: str) -> str | None:
    """AD-5: prefer Zotero's own pre-extracted fulltext index over the PDF
    -- returns the `.zotero-ft-cache` path for this attachment key if it
    exists and is a real file, else None."""
    path = os.path.join(_attachment_storage_dir(attachment_key), ".zotero-ft-cache")
    return path if os.path.isfile(path) else None


def _find_local_pdf(attachment_key: str) -> str | None:
    """Returns a PDF's absolute path inside this attachment's storage
    directory if one is actually synced locally, else None. A cloud-only/
    unsynced attachment can have this directory (and even a cached
    `.zotero-ft-cache`) with no `.pdf` inside it at all -- confirmed live,
    see module docstring -- so this is a real, expected None case, not a
    bug. If more than one `.pdf`-suffixed regular file is present (rare --
    a single imported attachment normally holds exactly one), the
    alphabetically-first one is returned; directory entries that merely
    end in `.pdf` but aren't regular files are skipped."""
    directory = _attachment_storage_dir(attachment_key)
    if not os.path.isdir(directory):
        return None
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return None
    for name in entries:
        if not name.lower().endswith(".pdf"):
            continue
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate
    return None


# Echoes --file's field-completeness reminder (see run_file below) --
# SKILL.md's "Field-completeness check" is a standing check on every touch,
# so it applies to a --get-content read exactly as it does to a --file write.
_FIELD_CHECKLIST_REMINDER = (
    "Now run the field-completeness check (checklist from the repo root's "
    "citation-contract.md; see SKILL.md) against \"resolved_fields\" -- it "
    "applies on every touch, including a read like this one (CAP-5), not "
    "just filing. If citation-contract.md is missing or still "
    "not-yet-negotiated, HALT and point at the onboarding skill."
)


def run_get_content(identifier: dict[str, Any]) -> dict[str, Any]:
    # Re-resolve live every call (AD-4) -- never cache citekey/item-key/
    # attachment-key across invocations, even within one conversation.
    resolved = resolve_item(identifier, retries=1)
    if not resolved:
        return {
            "status": "ok",
            "found": False,
            "message": "No existing item found for this identifier.",
        }

    citekey = resolved.get("citekey")
    item_key = resolved.get("item_key")
    resolved_fields = resolved.get("resolved_fields")
    attachment_keys = resolved.get("attachment_keys") or []

    # resolve_item()'s own documented race: a match was found but Better
    # BibTeX hasn't assigned a citekey yet (its early-return branch forces
    # attachment_keys/resolved_fields empty in this case) -- report this
    # distinctly rather than letting it silently fall through the passes
    # below into a misleading "no content available" (mirrors --file's
    # "resolve_after_write_failed" message for the same underlying race).
    if resolved.get("fields_lookup_failed") and not citekey:
        return {
            "status": "error",
            "reason": "fields_lookup_failed",
            "message": (
                "This identifier matched an item in Zotero, but Better "
                "BibTeX hasn't finished assigning a citekey yet (the same "
                "indexing race --file's \"resolve_after_write_failed\" "
                "guards against right after a fresh filing) -- re-run "
                "--get-content for this identifier in a moment rather than "
                "treating this as \"no content available\"."
            ),
            "item_key": item_key,
        }

    # A missing/misconfigured storage root would otherwise make every
    # attachment lookup below silently fail and look identical to a real
    # "no content available" -- only relevant when there's actually
    # something to look up.
    if attachment_keys:
        try:
            _require_storage_root()
        except ZoteroStorageError as exc:
            return {
                "status": "error",
                "reason": "zotero_storage_unavailable",
                "message": str(exc),
                "citekey": citekey,
                "item_key": item_key,
            }

    # Pass 1: try every attachment's fulltext-index cache first, across the
    # whole list, before any PDF or abstract fallback is even considered
    # (AD-5 ordering) -- "first found wins silently" per this story's Ask
    # First clause on multiple usable attachments.
    for key in attachment_keys:
        cache_path = _find_ft_cache(key)
        if not cache_path:
            continue
        try:
            with open(cache_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue  # try the next attachment key rather than fail outright
        if not content.strip():
            # An empty/whitespace-only extraction isn't usable content --
            # don't let it block falling through to the PDF/abstract passes.
            continue
        return {
            "status": "ok",
            "found": True,
            "source": "fulltext_index",
            "content": content,
            "citekey": citekey,
            "item_key": item_key,
            "resolved_fields": resolved_fields,
            "message": (
                f"Fulltext index hit for {citekey!r} (attachment {key}) -- "
                "no PDF was opened. " + _FIELD_CHECKLIST_REMINDER
            ),
        }

    # Pass 2: local PDF path across every attachment -- only reached once no
    # key in the library had a cache hit.
    for key in attachment_keys:
        pdf_path = _find_local_pdf(key)
        if not pdf_path:
            continue
        return {
            "status": "ok",
            "found": True,
            "source": "local_pdf",
            "local_path": pdf_path,
            "citekey": citekey,
            "item_key": item_key,
            "resolved_fields": resolved_fields,
            "message": (
                f"No fulltext index cached for {citekey!r}; local PDF found "
                f"at attachment {key} -- read it natively for page/figure-"
                "level detail. " + _FIELD_CHECKLIST_REMINDER
            ),
        }

    # Fall back to the CSL abstract -- every attachment key was tried above
    # for both cache and PDF before falling through this far.
    abstract = resolved_fields.get("abstract") if isinstance(resolved_fields, dict) else None
    abstract = abstract.strip() if isinstance(abstract, str) else ""
    if abstract:
        return {
            "status": "ok",
            "found": True,
            "source": "abstract",
            "citekey": citekey,
            "item_key": item_key,
            "resolved_fields": resolved_fields,
            "message": (
                f"No fulltext index or local PDF for {citekey!r} across "
                f"{len(attachment_keys)} attachment(s) -- falling back to "
                "the CSL abstract (\"resolved_fields\".\"abstract\"). "
                + _FIELD_CHECKLIST_REMINDER
            ),
        }

    return {
        "status": "error",
        "reason": "no_content_available",
        "message": (
            f"{citekey!r} has no cached fulltext index, no local PDF, and no "
            "abstract to fall back to across "
            f"{len(attachment_keys)} attachment(s) -- nothing to pull into "
            "context for this item."
        ),
        "citekey": citekey,
        "item_key": item_key,
    }


# ---------------------------------------------------------------------------
# --list-collection -- the one mode that reads zotero.sqlite directly
# instead of going through Connector/BBT (see module docstring's "Browsing").
# ---------------------------------------------------------------------------


def _open_zotero_db() -> sqlite3.Connection:
    path = os.environ.get(ZOTERO_SQLITE_ENV_VAR) or DEFAULT_ZOTERO_SQLITE_PATH
    if not os.path.isfile(path):
        raise ZoteroDatabaseError(
            f"Zotero's local database wasn't found at {path!r}. Set "
            f"{ZOTERO_SQLITE_ENV_VAR} if the researcher's Zotero data "
            "directory isn't the default, or ask them where it lives."
        )
    for attempt in range(SQLITE_LOCKED_RETRIES):
        try:
            # sqlite3.connect() alone doesn't touch the file lock -- it's
            # acquired lazily on the first statement, so the actual lock
            # check has to be a real query, not just a successful connect().
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            # "SELECT 1" is a constant expression -- it never touches an
            # actual table page, so it doesn't trigger the lock check
            # either. Read a real row to force it.
            con.execute("SELECT collectionID FROM collections LIMIT 1").fetchone()
            return con
        except sqlite3.OperationalError:
            if attempt < SQLITE_LOCKED_RETRIES - 1:
                time.sleep(SQLITE_LOCKED_RETRY_DELAY_SECONDS)
    # Confirmed live: Zotero desktop holds the file locked for its entire
    # run, not just mid-write, so plain read-only retries above are
    # expected to exhaust here whenever Zotero is open. immutable=1
    # bypasses locking rather than negotiating it -- see the module
    # docstring for why that's an accepted trade-off for a browse-only
    # query.
    try:
        return sqlite3.connect(f"file:{path}?immutable=1", uri=True)
    except sqlite3.OperationalError as exc:
        raise ZoteroDatabaseError(
            f"Could not open Zotero's local database at {path!r} even in "
            f"immutable fallback mode: {exc}"
        ) from exc


def _library_name(cur: sqlite3.Cursor, library_id: int) -> str:
    row = cur.execute("SELECT type FROM libraries WHERE libraryID = ?", (library_id,)).fetchone()
    if row and row[0] == "user":
        return "My Library"
    group = cur.execute("SELECT name FROM groups WHERE libraryID = ?", (library_id,)).fetchone()
    return group[0] if group else f"library {library_id}"


def _resolve_collection_ref(
    cur: sqlite3.Cursor, ref: str
) -> tuple[tuple[int, str, int] | None, dict[str, Any] | None]:
    """Returns ((collectionID, name, libraryID), None) on a unique match, or
    (None, error_or_halt_payload) otherwise -- same shape convention as
    resolve_collection_target below."""
    stripped = ref.strip()
    numeric = stripped[1:] if stripped[:1].upper() == "C" and stripped[1:].isdigit() else (
        stripped if stripped.isdigit() else None
    )
    if numeric is not None:
        row = cur.execute(
            "SELECT collectionID, collectionName, libraryID FROM collections WHERE collectionID = ?",
            (int(numeric),),
        ).fetchone()
        if not row:
            return None, {
                "status": "error",
                "reason": "invalid_input",
                "message": (
                    f"No collection with id {ref!r} exists. Use "
                    '--check-target\'s "targets" list to find a valid one, '
                    "or pass a name instead."
                ),
            }
        return row, None

    rows = cur.execute(
        "SELECT collectionID, collectionName, libraryID FROM collections WHERE lower(collectionName) = lower(?)",
        (stripped,),
    ).fetchall()
    if not rows:
        return None, {
            "status": "error",
            "reason": "invalid_input",
            "message": f"No collection named {ref!r} was found in any library.",
        }
    if len(rows) > 1:
        return None, {
            "status": "halt",
            "reason": "ambiguous_collection_name",
            "message": (
                f"{len(rows)} collections are named {ref!r} across "
                "different libraries/parents. Ask the researcher which one "
                'they mean -- pass the specific "C<id>" from "candidates" '
                "instead of the name."
            ),
            "candidates": [
                {"id": f"C{cid}", "name": name, "library": _library_name(cur, lib_id)}
                for cid, name, lib_id in rows
            ],
        }
    return rows[0], None


def _descendant_collection_ids(cur: sqlite3.Cursor, root_id: int) -> list[int]:
    ids = [root_id]
    frontier = [root_id]
    while frontier:
        placeholders = ",".join("?" * len(frontier))
        rows = cur.execute(
            f"SELECT collectionID FROM collections WHERE parentCollectionID IN ({placeholders})",
            frontier,
        ).fetchall()
        frontier = [r[0] for r in rows if r[0] not in ids]
        ids.extend(frontier)
    return ids


def _item_field(cur: sqlite3.Cursor, item_id: int, field_name: str) -> str | None:
    row = cur.execute(
        """
        SELECT idv.value FROM itemData id
        JOIN itemDataValues idv ON id.valueID = idv.valueID
        JOIN fields f ON id.fieldID = f.fieldID
        WHERE id.itemID = ? AND f.fieldName = ?
        LIMIT 1
        """,
        (item_id, field_name),
    ).fetchone()
    return row[0] if row else None


def run_list_collection(ref: str, recursive: bool) -> dict[str, Any]:
    try:
        con = _open_zotero_db()
    except ZoteroDatabaseError as exc:
        return {"status": "error", "reason": "zotero_database_unavailable", "message": str(exc)}

    try:
        cur = con.cursor()
        resolved, problem = _resolve_collection_ref(cur, ref)
        if problem is not None:
            return problem
        collection_id, collection_name, library_id = resolved

        target_ids = _descendant_collection_ids(cur, collection_id) if recursive else [collection_id]
        sub_collections = []
        if len(target_ids) > 1:
            placeholders = ",".join("?" * (len(target_ids) - 1))
            other_ids = [i for i in target_ids if i != collection_id]
            sub_collections = [
                {"id": f"C{cid}", "name": name}
                for cid, name in cur.execute(
                    f"SELECT collectionID, collectionName FROM collections WHERE collectionID IN ({placeholders})",
                    other_ids,
                ).fetchall()
            ]

        placeholders = ",".join("?" * len(target_ids))
        rows = cur.execute(
            f"""
            SELECT DISTINCT i.itemID, i.key, it.typeName
            FROM collectionItems ci
            JOIN items i ON ci.itemID = i.itemID
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE ci.collectionID IN ({placeholders})
              AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            ORDER BY i.itemID
            """,
            target_ids,
        ).fetchall()

        items = []
        excluded_non_paper = 0
        for item_id, item_key, type_name in rows:
            if type_name in NON_PAPER_ITEM_TYPES:
                excluded_non_paper += 1
                continue
            title = _item_field(cur, item_id, "title")
            doi = _item_field(cur, item_id, "DOI")
            date_value = _item_field(cur, item_id, "date")
            year_match = re.search(r"\d{4}", date_value) if date_value else None
            items.append(
                {
                    "item_key": item_key,
                    "title": title,
                    "item_type": type_name,
                    "year": int(year_match.group()) if year_match else None,
                    "identifier": {"doi": doi, "title": title, "arxiv_id": None},
                }
            )
        items.sort(key=lambda entry: (entry["title"] or "").lower())

        return {
            "status": "ok",
            "library": {"id": library_id, "name": _library_name(cur, library_id)},
            "collection": {"id": f"C{collection_id}", "name": collection_name},
            "recursive": recursive,
            "sub_collections_included": sub_collections,
            "count": len(items),
            "items": items,
            "attachments_and_notes_excluded": excluded_non_paper,
            "message": (
                f"{len(items)} paper(s) found in {collection_name!r}"
                + (
                    f" (including its {len(sub_collections)} sub-collection(s): "
                    + ", ".join(s["name"] for s in sub_collections) + ")"
                    if sub_collections
                    else ""
                )
                + ". Each item's \"identifier\" is the same flat shape "
                "lit-search/--check-duplicate/--resolve use -- pass it "
                "straight through if you need the citekey or full CSL "
                "fields for any of these."
            ),
        }
    finally:
        con.close()


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
                "field-completeness check cannot run yet -- re-run "
                "--resolve for this identifier before telling the "
                "researcher this citekey is ready to cite."
                if fields_lookup_failed
                else "Now run the field-completeness check (checklist from "
                "the repo root's citation-contract.md; see SKILL.md) "
                "against \"resolved_fields\" before telling the researcher "
                "this citekey is ready to cite."
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
    mode.add_argument("--get-content", metavar="IDENTIFIER_JSON", help="Pull a resolved item's content into context: fulltext-index cache, else local PDF path, else CSL abstract (AD-5).")
    mode.add_argument("--list-collection", metavar="COLLECTION_REF", help='List every paper already filed in a collection -- "C69" (from --check-target\'s "targets") or a name. Recurses into sub-collections by default; pass --no-recursive to list only direct members.')
    mode.add_argument("--file", metavar="IDENTIFIER_JSON", help="File a new item after dup-check, then resolve + confirm citekey.")

    parser.add_argument("--no-recursive", action="store_true", help="With --list-collection, list only direct members -- don't descend into sub-collections.")
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
    if args.no_recursive and args.list_collection is None:
        parser.error("--no-recursive only applies to --list-collection.")

    try:
        if args.check_target:
            result = run_check_target()
        elif args.check_duplicate is not None:
            identifier = validate_identifier(parse_json_arg(args.check_duplicate, "--check-duplicate"), "--check-duplicate")
            result = run_check_duplicate(identifier)
        elif args.resolve is not None:
            identifier = validate_identifier(parse_json_arg(args.resolve, "--resolve"), "--resolve")
            result = run_resolve(identifier)
        elif args.get_content is not None:
            identifier = validate_identifier(parse_json_arg(args.get_content, "--get-content"), "--get-content")
            result = run_get_content(identifier)
        elif args.list_collection is not None:
            result = run_list_collection(args.list_collection, recursive=not args.no_recursive)
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
