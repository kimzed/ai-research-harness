# /// script
# requires-python = ">=3.12"
# dependencies = ["requests"]
# ///
"""lit_search.py -- Semantic Scholar (primary) + OpenAlex (fallback) literature
search for the `lit-search` Claude Code skill (CAP-3, AD-8).

Two mutually exclusive top-level modes:
  --topic "query text"   topic search (Semantic Scholar /paper/search)
  --paper PAPER_ID       anchor a citation-graph/similarity query to one
                         paper id, combined with one or more of:
                           --citations    papers citing PAPER_ID
                           --references   papers PAPER_ID itself cites
                           --similar      papers similar to PAPER_ID
                         Any combination of --citations/--references/
                         --similar is valid in a single invocation; each
                         relation is fetched, parsed, and reported
                         independently -- one relation's throttle/error/halt
                         never discards another relation's already-resolved
                         candidates in the same invocation.

PAPER_ID accepts a Semantic Scholar paper id, or a prefixed external id such
as "DOI:10.1038/nature14539" or "ARXIV:2106.15928". A bare DOI (starts with
"10.") or a bare arXiv id is auto-prefixed for convenience.

Always prints a single parsed JSON object to stdout -- never a raw API dump
(AD-8, PRD "no raw JSON API response surfaced to the researcher"). Every
candidate's identifier is emitted in the shared flat shape:
    {"doi": "...", "title": "...", "arxiv_id": null}

`--topic` output shape (unchanged from the previous single-mode CLI):
    {"status": "ok"|"error"|"halt", "mode": "topic", "query": ...,
     "count": N, "candidates": [...], "notes": [...]}

`--paper` output shape groups each requested relation independently, with no
cross-relation merge, dedup, or found-via tagging -- that judgment is left to
Claude Code from the conversation:
    {"status": "ok"|"halt", "paper": PAPER_ID,
     "relations": {"citations": {...}, "references": {...}, "similar": {...}}}
Each present relation entry uses the same "status"/"count"/"candidates"/
"notes" shape as a `--topic` result (minus "mode"/"query"). A throttle/error
on one relation is reported as that relation's own "status": "error" without
discarding another relation's candidates. If any relation needs the OpenAlex
Ask-First halt, the top-level "status" is "halt" (exit 2) and every
already-resolved relation's candidates remain present alongside it.

Exit codes:
  0  ok -- see "status": "ok" in the printed JSON
  1  error (throttled after max backoff, HTTP failure, paper not found, etc.)
     -- `--topic` mode only; `--paper` mode never aborts the whole call on a
     relation error, it reports that relation's own "status": "error" instead
     (see above) and still exits 0 unless a halt fired.
  2  halt -- Ask-First condition hit (OPENALEX_API_KEY unset, fallback needed).
     Claude Code must relay the "message" field to the researcher rather than
     silently proceeding or re-running.

This script never guesses OpenAlex or otherwise routes around a HALT. On the
researcher's decision to proceed Semantic-Scholar-only for the session,
Claude Code re-invokes with --no-openalex-fallback for the remainder of that
session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_REC_BASE = "https://api.semanticscholar.org/recommendations/v1"
OPENALEX_BASE = "https://api.openalex.org"

S2_FIELDS = "title,year,authors,abstract,externalIds"

MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 30


class ThrottledError(RuntimeError):
    """Raised when a service is still throttling after MAX_RETRIES."""


class PaperNotFoundError(RuntimeError):
    """Raised when Semantic Scholar returns 404 for a given paper id."""


# ---------------------------------------------------------------------------
# .env loading (no python-dotenv dependency -- keep this a single-file script)
# ---------------------------------------------------------------------------


def find_repo_root(start: Path) -> Path:
    """Walk upward from `start` looking for a `.git` directory."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def load_dotenv(repo_root: Path) -> None:
    """Populate os.environ from a simple KEY=VALUE `.env` file, if present.

    Does not overwrite variables already set in the real environment.
    """
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# Shared HTTP GET with exponential backoff on 429 (PRD NFR / I-O matrix)
# ---------------------------------------------------------------------------


def get_with_backoff(
    url: str, params: dict[str, Any] | None, service: str
) -> requests.Response:
    backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(1, MAX_RETRIES + 1):
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 429:
            if attempt == MAX_RETRIES:
                raise ThrottledError(
                    f"{service} is still throttling requests (HTTP 429) after "
                    f"{MAX_RETRIES} attempts with exponential backoff. Try again "
                    "in a few minutes rather than retrying immediately."
                )
            retry_after = response.headers.get("Retry-After")
            try:
                sleep_seconds = float(retry_after) if retry_after else backoff
            except ValueError:
                sleep_seconds = backoff
            print(
                f"[lit-search] {service} responded 429 -- backing off "
                f"{sleep_seconds:.1f}s (attempt {attempt}/{MAX_RETRIES})",
                file=sys.stderr,
            )
            time.sleep(sleep_seconds)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            continue
        return response
    raise ThrottledError(f"{service} throttled -- exhausted retries.")


# ---------------------------------------------------------------------------
# Semantic Scholar (primary)
# ---------------------------------------------------------------------------


def normalize_paper_id(raw_id: str) -> str:
    """Auto-prefix bare DOIs / arXiv ids for convenience; pass through anything
    that already looks prefixed or is a bare Semantic Scholar paper id."""
    known_prefixes = (
        "DOI:",
        "ARXIV:",
        "MAG:",
        "ACL:",
        "PMID:",
        "PMCID:",
        "CorpusID:",
        "URL:",
    )
    if any(raw_id.upper().startswith(p.upper()) for p in known_prefixes):
        return raw_id
    if raw_id.startswith("10."):
        return f"DOI:{raw_id}"
    # crude arXiv id shape check: 4 digits, dot, 4-5 digits (e.g. 2106.15928)
    digits_dot = raw_id.split(".")
    if len(digits_dot) == 2 and all(part.isdigit() for part in digits_dot):
        return f"ARXIV:{raw_id}"
    return raw_id


def s2_get(path: str, params: dict[str, Any], base: str = S2_BASE) -> dict[str, Any]:
    url = f"{base}{path}"
    response = get_with_backoff(url, params, service="Semantic Scholar")
    if response.status_code == 404:
        raise PaperNotFoundError(
            f"Semantic Scholar could not find a paper for id used in {path!r}."
        )
    response.raise_for_status()
    return response.json()


def s2_search_topic(query: str, limit: int) -> list[dict[str, Any]]:
    data = s2_get("/paper/search", {"query": query, "fields": S2_FIELDS, "limit": limit})
    return data.get("data") or []


def s2_citations(paper_id: str, limit: int) -> list[dict[str, Any]]:
    data = s2_get(
        f"/paper/{paper_id}/citations", {"fields": S2_FIELDS, "limit": limit}
    )
    return [
        entry["citingPaper"]
        for entry in (data.get("data") or [])
        if entry.get("citingPaper")
    ]


def s2_references(paper_id: str, limit: int) -> list[dict[str, Any]]:
    data = s2_get(
        f"/paper/{paper_id}/references", {"fields": S2_FIELDS, "limit": limit}
    )
    return [
        entry["citedPaper"]
        for entry in (data.get("data") or [])
        if entry.get("citedPaper")
    ]


def s2_similar(paper_id: str, limit: int) -> list[dict[str, Any]]:
    # GET /recommendations/v1/papers/forpaper/{id} only -- never the POST
    # variant, which is restricted to papers from the past 60 days.
    data = s2_get(
        f"/papers/forpaper/{paper_id}",
        {"fields": S2_FIELDS, "limit": limit},
        base=S2_REC_BASE,
    )
    return data.get("recommendedPapers") or []


# ---------------------------------------------------------------------------
# OpenAlex (cross-check / fallback only -- never primary, AD-1/CAP-3)
# ---------------------------------------------------------------------------


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex stores abstracts as an inverted index, not plain text --
    reconstruct plain text so the researcher never sees raw index data."""
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    max_position = 0
    for word, indices in inverted_index.items():
        for index in indices:
            positions[index] = word
            max_position = max(max_position, index)
    words = [positions.get(i, "") for i in range(max_position + 1)]
    text = " ".join(w for w in words if w).strip()
    return text or None


def openalex_lookup_abstract(doi: str | None, title: str | None, api_key: str) -> str | None:
    work: dict[str, Any] | None = None

    if doi:
        response = get_with_backoff(
            f"{OPENALEX_BASE}/works/doi:{doi}",
            {"api_key": api_key},
            service="OpenAlex",
        )
        if response.status_code == 404:
            work = None
        else:
            response.raise_for_status()
            work = response.json()

    if work is None and title:
        response = get_with_backoff(
            f"{OPENALEX_BASE}/works",
            {"filter": f"title.search:{title}", "per-page": 1, "api_key": api_key},
            service="OpenAlex",
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        work = results[0] if results else None

    if not work:
        return None
    return reconstruct_abstract(work.get("abstract_inverted_index"))


# ---------------------------------------------------------------------------
# Candidate parsing -- shared flat identifier shape (Consistency Conventions)
# ---------------------------------------------------------------------------


def parse_candidates(
    raw_papers: list[dict[str, Any]],
    skip_openalex_fallback: bool,
    notes: list[str],
) -> tuple[list[dict[str, Any]], bool]:
    """Returns (candidates, needs_openalex_key).

    needs_openalex_key is True as soon as a candidate needs the OpenAlex
    cross-check (thin/missing abstract) but OPENALEX_API_KEY is unset --
    callers must HALT and ask rather than reporting "no abstract available"
    for that candidate.
    """
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    candidates: list[dict[str, Any]] = []
    needs_openalex_key = False

    for paper in raw_papers:
        external_ids = paper.get("externalIds") or {}
        doi = external_ids.get("DOI")
        arxiv_id = external_ids.get("ArXiv")
        title = paper.get("title")
        abstract = paper.get("abstract")
        abstract_source = "semantic_scholar" if abstract else None

        if not abstract:
            if skip_openalex_fallback:
                notes.append(
                    f'No abstract available for "{title}" (OpenAlex cross-check '
                    "skipped this session by researcher's choice)."
                )
            elif not api_key:
                needs_openalex_key = True
                # Don't attempt the fallback and don't report "no abstract" --
                # this candidate's abstract status is left pending the halt.
            else:
                lookup_failed = False
                try:
                    fetched = openalex_lookup_abstract(doi, title, api_key)
                except (ThrottledError, requests.RequestException) as exc:
                    fetched = None
                    lookup_failed = True
                    notes.append(
                        f'No abstract available for "{title}" (Semantic Scholar had '
                        f"none, and the OpenAlex cross-check failed: {exc})."
                    )
                if fetched:
                    abstract = fetched
                    abstract_source = "openalex"
                elif not lookup_failed:
                    notes.append(
                        f'No abstract available for "{title}" (checked Semantic '
                        "Scholar and OpenAlex)."
                    )

        candidates.append(
            {
                "title": title,
                "year": paper.get("year"),
                "authors": [a.get("name") or "" for a in (paper.get("authors") or [])],
                "abstract": abstract,
                "abstract_source": abstract_source,
                "identifier": {"doi": doi, "title": title, "arxiv_id": arxiv_id},
            }
        )

    return candidates, needs_openalex_key


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "lit-search: Semantic Scholar (primary) + OpenAlex (fallback) "
            "literature search."
        )
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--topic", metavar="QUERY", help="Topic search.")
    mode_group.add_argument(
        "--paper",
        metavar="PAPER_ID",
        help=(
            "Anchor paper id for a citation-graph/similarity query. Requires "
            "at least one of --citations/--references/--similar; any "
            "combination of those is valid in one invocation."
        ),
    )
    parser.add_argument(
        "--citations",
        action="store_true",
        help="Include papers citing --paper.",
    )
    parser.add_argument(
        "--references",
        action="store_true",
        help="Include papers --paper itself cites.",
    )
    parser.add_argument(
        "--similar",
        action="store_true",
        help="Include papers similar to --paper.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max candidates per relation (or per topic search), default 10.",
    )
    parser.add_argument(
        "--no-openalex-fallback",
        action="store_true",
        help=(
            "Skip the OpenAlex cross-check entirely and report 'no abstract "
            "available' for thin candidates instead. Use only after the "
            "researcher has explicitly chosen to proceed Semantic-Scholar-only "
            "for this session (Ask-First)."
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# --topic mode (unchanged single-list shape)
# ---------------------------------------------------------------------------


def run_topic_mode(args: argparse.Namespace) -> int:
    notes: list[str] = []
    try:
        raw_papers = s2_search_topic(args.topic, args.limit)
    except ThrottledError as exc:
        print(
            json.dumps(
                {"status": "error", "reason": "throttled", "message": str(exc)},
                indent=2,
            )
        )
        return 1
    except PaperNotFoundError as exc:
        print(
            json.dumps(
                {"status": "error", "reason": "paper_not_found", "message": str(exc)},
                indent=2,
            )
        )
        return 1
    except requests.RequestException as exc:
        print(
            json.dumps(
                {"status": "error", "reason": "http_error", "message": str(exc)},
                indent=2,
            )
        )
        return 1

    if not raw_papers:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": "topic",
                    "query": args.topic,
                    "count": 0,
                    "candidates": [],
                    "notes": [f"No results found for topic query {args.topic!r}."],
                },
                indent=2,
            )
        )
        return 0

    try:
        candidates, needs_openalex_key = parse_candidates(
            raw_papers, args.no_openalex_fallback, notes
        )
    except ThrottledError as exc:
        print(
            json.dumps(
                {"status": "error", "reason": "throttled", "message": str(exc)},
                indent=2,
            )
        )
        return 1
    except requests.RequestException as exc:
        print(
            json.dumps(
                {"status": "error", "reason": "http_error", "message": str(exc)},
                indent=2,
            )
        )
        return 1

    if needs_openalex_key:
        pending_titles = [
            c["title"]
            for c in candidates
            if c["abstract_source"] is None and c["abstract"] is None
        ]
        print(
            json.dumps(
                {
                    "status": "halt",
                    "reason": "openalex_key_missing",
                    "message": (
                        "Semantic Scholar returned no abstract for one or more "
                        "candidates and OPENALEX_API_KEY is unset in .env, so the "
                        "OpenAlex cross-check cannot run. Ask the researcher: "
                        "proceed Semantic-Scholar-only for this session (then "
                        "re-run this search with --no-openalex-fallback), or pause "
                        "so they can set OPENALEX_API_KEY in .env first?"
                    ),
                    "pending_abstract_lookups": pending_titles,
                    "mode": "topic",
                    "query": args.topic,
                    "count": len(candidates),
                    "candidates": candidates,
                },
                indent=2,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "status": "ok",
                "mode": "topic",
                "query": args.topic,
                "count": len(candidates),
                "candidates": candidates,
                "notes": notes,
            },
            indent=2,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# --paper mode -- independently combinable relation flags
# ---------------------------------------------------------------------------

RELATION_FETCHERS = {
    "citations": s2_citations,
    "references": s2_references,
    "similar": s2_similar,
}


def run_relation(
    relation: str, paper_id: str, limit: int, skip_openalex_fallback: bool
) -> dict[str, Any]:
    """Fetch + parse exactly one relation in isolation.

    Never raises -- a throttle, HTTP error, or paper-not-found becomes this
    relation's own `"status": "error"` entry instead of propagating, so it
    can't discard another relation's already-resolved candidates in the same
    invocation.
    """
    notes: list[str] = []
    fetch = RELATION_FETCHERS[relation]

    try:
        raw_papers = fetch(paper_id, limit)
    except ThrottledError as exc:
        return {"status": "error", "reason": "throttled", "message": str(exc)}
    except PaperNotFoundError as exc:
        return {"status": "error", "reason": "paper_not_found", "message": str(exc)}
    except requests.RequestException as exc:
        return {"status": "error", "reason": "http_error", "message": str(exc)}
    except Exception as exc:
        return {"status": "error", "reason": "unexpected", "message": str(exc)}

    if not raw_papers:
        return {
            "status": "ok",
            "count": 0,
            "candidates": [],
            "notes": [f"No results found for {relation} of {paper_id!r}."],
        }

    try:
        candidates, needs_openalex_key = parse_candidates(
            raw_papers, skip_openalex_fallback, notes
        )
    except ThrottledError as exc:
        return {"status": "error", "reason": "throttled", "message": str(exc)}
    except requests.RequestException as exc:
        return {"status": "error", "reason": "http_error", "message": str(exc)}
    except Exception as exc:
        return {"status": "error", "reason": "unexpected", "message": str(exc)}

    if needs_openalex_key:
        pending_titles = [
            c["title"]
            for c in candidates
            if c["abstract_source"] is None and c["abstract"] is None
        ]
        return {
            "status": "halt",
            "reason": "openalex_key_missing",
            "message": (
                "Semantic Scholar returned no abstract for one or more "
                f"candidates in the {relation!r} relation and "
                "OPENALEX_API_KEY is unset in .env, so the OpenAlex "
                "cross-check cannot run for it. Ask the researcher: proceed "
                "Semantic-Scholar-only for this session (then re-run with "
                "--no-openalex-fallback), or pause so they can set "
                "OPENALEX_API_KEY in .env first?"
            ),
            "pending_abstract_lookups": pending_titles,
            "count": len(candidates),
            "candidates": candidates,
            "notes": notes,
        }

    return {
        "status": "ok",
        "count": len(candidates),
        "candidates": candidates,
        "notes": notes,
    }


def run_paper_mode(args: argparse.Namespace) -> int:
    paper_id = normalize_paper_id(args.paper)
    requested = [
        name
        for name, flag in (
            ("citations", args.citations),
            ("references", args.references),
            ("similar", args.similar),
        )
        if flag
    ]

    relations: dict[str, Any] = {
        name: run_relation(name, paper_id, args.limit, args.no_openalex_fallback)
        for name in requested
    }

    halted = [name for name, result in relations.items() if result.get("status") == "halt"]
    errored = [name for name, result in relations.items() if result.get("status") == "error"]

    payload: dict[str, Any] = {
        "status": "halt" if halted else "ok",
        "paper": paper_id,
        "relations": relations,
    }
    if halted:
        also_errored = (
            f" Separately, the following relation(s) also failed: "
            f"{', '.join(errored)} -- see each one's own \"message\"."
            if errored
            else ""
        )
        payload["message"] = (
            "OpenAlex is needed for at least one candidate in the following "
            f"relation(s): {', '.join(halted)}, but OPENALEX_API_KEY is unset "
            "in .env. See each halted relation's own \"message\" for detail. "
            "Ask the researcher: proceed Semantic-Scholar-only for this "
            "session (then re-run this same --paper call with "
            "--no-openalex-fallback), or pause so they can set "
            "OPENALEX_API_KEY in .env first. Already-resolved candidates in "
            "every requested relation are included above and are not "
            "discarded while waiting on the answer." + also_errored
        )

    print(json.dumps(payload, indent=2))
    return 2 if halted else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.paper is not None and not (args.citations or args.references or args.similar):
        parser.error(
            "--paper requires at least one of --citations/--references/--similar."
        )
    if args.topic is not None and (args.citations or args.references or args.similar):
        parser.error(
            "--topic is its own mode and cannot be combined with "
            "--citations/--references/--similar -- use --paper for those "
            "(topic search has no anchor paper)."
        )

    repo_root = find_repo_root(Path.cwd())
    load_dotenv(repo_root)

    if args.topic is not None:
        return run_topic_mode(args)
    return run_paper_mode(args)


if __name__ == "__main__":
    sys.exit(main())
