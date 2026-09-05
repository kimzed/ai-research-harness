---
name: lit-search
description: Search for academic papers by topic, citation graph, or similarity via Semantic Scholar (primary) with OpenAlex as a cross-check/fallback for missing abstracts. Use when the researcher wants to find literature -- "find papers on X", "what cites this paper", "what does this paper cite", or "find papers similar to this one".
---

# lit-search

Custom wrapper over the Semantic Scholar Graph API (primary, unauthenticated
pool) with OpenAlex as a cross-check/fallback when Semantic Scholar has no
abstract (CAP-3, AD-1, AD-8). This is the only literature-search mechanism in
this repo -- no Google Scholar, no scraping (PRD non-goal).

**Never consume a raw API response directly.** Always invoke
`lit_search.py` and read its parsed JSON output. It already turns raw
Semantic Scholar / OpenAlex responses into title/year/authors/abstract plus
the shared flat identifier shape -- there is no reason to call either API
directly from conversation.

## Invocation

```bash
uv run .claude/skills/lit-search/lit_search.py --topic "CRISPR base editing"
uv run .claude/skills/lit-search/lit_search.py --citations DOI:10.1038/nature14539
uv run .claude/skills/lit-search/lit_search.py --references DOI:10.1038/nature14539
uv run .claude/skills/lit-search/lit_search.py --similar DOI:10.1038/nature14539
```

Exactly one of `--topic` / `--citations` / `--references` / `--similar` is
required per invocation:

- `--topic "query text"` -- topic search (Semantic Scholar `/paper/search`).
- `--citations PAPER_ID` -- papers that cite `PAPER_ID` (citation graph,
  forward direction).
- `--references PAPER_ID` -- papers `PAPER_ID` itself cites (citation graph,
  backward direction).
- `--similar PAPER_ID` -- similarity search via the GET
  `/recommendations/v1/papers/forpaper/{id}` endpoint only. Never use or
  suggest the POST recommendations endpoint -- it only returns papers from
  the past 60 days, which silently narrows results without saying so.

`PAPER_ID` accepts a Semantic Scholar paper id, or a prefixed external id
(`DOI:10.xxxx/yyyy`, `ARXIV:2106.15928`). A bare DOI (starts with `10.`) or a
bare arXiv-shaped id is auto-prefixed by the script for convenience.

Optional: `--limit N` (default 10), `--no-openalex-fallback` (see Ask-First
below -- only pass this after the researcher has actually agreed to it).

## Output contract

The script always prints one JSON object to stdout and never a raw API dump.
`"status"` is one of:

- **`"ok"`** -- `"candidates"` is a list of parsed papers, each shaped as:
  ```json
  {
    "title": "...",
    "year": 2015,
    "authors": ["...", "..."],
    "abstract": "..." or null,
    "abstract_source": "semantic_scholar" | "openalex" | null,
    "identifier": {"doi": "...", "title": "...", "arxiv_id": null}
  }
  ```
  Present every candidate's `identifier` in conversation using this exact
  flat shape -- it's the same shape `zotero-code-execution` (story 5)
  consumes for filing, so no adapter is needed between the two skills.
  `"notes"` may explain per-candidate gaps (e.g. "no abstract available").
- **`"halt"`** (exit code 2) -- an Ask-First condition was hit: OpenAlex is
  actually needed (Semantic Scholar returned no abstract for at least one
  candidate) but `OPENALEX_API_KEY` is unset in `.env`. Relay the `"message"`
  field to the researcher verbatim and ask them to choose:
  1. **Proceed Semantic-Scholar-only for this session** -- re-run the same
     search with `--no-openalex-fallback` added, and keep adding that flag
     to every `lit-search` call for the remainder of this conversation (the
     script has no memory across invocations -- Claude Code carries this
     choice, not the script).
  2. **Pause** so the researcher can set `OPENALEX_API_KEY` in `.env` first,
     then re-run the original command without the flag.

  Do not guess which the researcher wants, and do not silently retry without
  asking. The `"candidates"` field still carries whatever was resolved
  before the halt (candidates that already had a Semantic Scholar abstract)
  so nothing already-good is thrown away while you wait for the answer.
- **`"error"`** (exit code 1) -- `"reason"` is `"throttled"` (Semantic
  Scholar or OpenAlex was still returning HTTP 429 after exponential
  backoff exhausted its retry budget), `"paper_not_found"`, or
  `"http_error"`. Tell the researcher what happened rather than retrying in
  a loop or treating it as an empty result.

Backoff on throttling happens inside the script automatically (bounded
retries with exponential backoff) -- Claude Code doesn't need to retry the
invocation itself when it sees a `429` in `stderr` progress lines; only act
on the final `"error"`/`"halt"`/`"ok"` JSON on stdout.

## Rules

- Semantic Scholar is always primary. OpenAlex is only ever queried as a
  cross-check/fallback for a specific candidate's missing/thin abstract --
  never as the primary source for a search itself.
- Never surface a raw Semantic Scholar or OpenAlex JSON response to the
  researcher -- always the parsed candidate shape above.
- Never invent or guess a `doi`/`arxiv_id` when the API didn't return one --
  leave the field `null`.
- This skill has no write access anywhere: no Zotero filing, no
  duplicate-check, no `.bib` edits. Filing a chosen candidate is CAP-4/CAP-5
  (story 5, `zotero-code-execution`), out of scope here. Show candidates and
  let the researcher decide.
