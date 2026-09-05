# /// script
# requires-python = ">=3.12"
# dependencies = ["requests"]
# ///
"""lit_search.py -- Semantic Scholar (primary) + OpenAlex (fallback) literature
search for the `lit-search` Claude Code skill (CAP-3, AD-8).

Three search modes, one per CLI flag:
  --topic "query text"      topic search (Semantic Scholar /paper/search)
  --citations PAPER_ID      papers citing PAPER_ID (/paper/{id}/citations)
  --references PAPER_ID     papers referenced by PAPER_ID (/paper/{id}/references)
  --similar PAPER_ID        similar papers (/recommendations/v1/papers/forpaper/{id})

PAPER_ID accepts a Semantic Scholar paper id, or a prefixed external id such
as "DOI:10.1038/nature14539" or "ARXIV:2106.15928". A bare DOI (starts with
"10.") or a bare arXiv id is auto-prefixed for convenience.

Always prints a single parsed JSON object to stdout -- never a raw API dump
(AD-8, PRD "no raw JSON API response surfaced to the researcher"). Every
candidate's identifier is emitted in the shared flat shape:
    {"doi": "...", "title": "...", "arxiv_id": null}

Exit codes:
  0  ok -- see "status": "ok" in the printed JSON
  1  error (throttled after max backoff, HTTP failure, paper not found, etc.)
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
        "--citations", metavar="PAPER_ID", help="Papers citing PAPER_ID."
    )
    mode_group.add_argument(
        "--references", metavar="PAPER_ID", help="Papers referenced by PAPER_ID."
    )
    mode_group.add_argument(
        "--similar", metavar="PAPER_ID", help="Papers similar to PAPER_ID."
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Max candidates to return (default 10)."
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


def main() -> int:
    args = build_parser().parse_args()
    repo_root = find_repo_root(Path.cwd())
    load_dotenv(repo_root)

    notes: list[str] = []
    try:
        if args.topic:
            mode, query = "topic", args.topic
            raw_papers = s2_search_topic(args.topic, args.limit)
        elif args.citations:
            mode, query = "citations", args.citations
            raw_papers = s2_citations(normalize_paper_id(args.citations), args.limit)
        elif args.references:
            mode, query = "references", args.references
            raw_papers = s2_references(normalize_paper_id(args.references), args.limit)
        else:
            mode, query = "similar", args.similar
            raw_papers = s2_similar(normalize_paper_id(args.similar), args.limit)
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
                    "mode": mode,
                    "query": query,
                    "count": 0,
                    "candidates": [],
                    "notes": [f"No results found for {mode} query {query!r}."],
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
        pending_titles = [c["title"] for c in candidates if c["abstract_source"] is None and c["abstract"] is None]
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
                    "mode": mode,
                    "query": query,
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
                "mode": mode,
                "query": query,
                "count": len(candidates),
                "candidates": candidates,
                "notes": notes,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
