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

Two mutually exclusive top-level modes: `--topic` (a plain topic search) or
`--paper` (an anchor paper id, combined with one or more relation flags).

```bash
# Topic search -- its own mode, no anchor paper.
uv run .claude/skills/lit-search/lit_search.py --topic "CRISPR base editing"

# --paper + any combination of --citations/--references/--similar, in ONE
# invocation -- the script makes up to three internal HTTP calls itself
# rather than you launching three separate processes.
uv run .claude/skills/lit-search/lit_search.py --paper DOI:10.1038/nature14539 --citations
uv run .claude/skills/lit-search/lit_search.py --paper DOI:10.1038/nature14539 --citations --references --similar
```

- `--topic "query text"` -- topic search (Semantic Scholar `/paper/search`).
  Mutually exclusive with `--paper` and the relation flags -- topic search
  has no anchor paper.
- `--paper PAPER_ID` -- anchor paper id for a citation-graph/similarity
  query. Requires at least one of the relation flags below; any combination
  is valid in a single invocation:
  - `--citations` -- papers that cite `PAPER_ID` (citation graph, forward
    direction).
  - `--references` -- papers `PAPER_ID` itself cites (citation graph,
    backward direction).
  - `--similar` -- similarity search via the GET
    `/recommendations/v1/papers/forpaper/{id}` endpoint only. Never use or
    suggest the POST recommendations endpoint -- it only returns papers from
    the past 60 days, which silently narrows results without saying so.

`PAPER_ID` accepts a Semantic Scholar paper id, or a prefixed external id
(`DOI:10.xxxx/yyyy`, `ARXIV:2106.15928`). A bare DOI (starts with `10.`) or a
bare arXiv-shaped id is auto-prefixed by the script for convenience.

Optional: `--limit N` (default 10, applied independently to each relation
requested), `--no-openalex-fallback` (see Ask-First below -- only pass this
after the researcher has actually agreed to it).

## Output contract

The script always prints one JSON object to stdout and never a raw API dump.

**`--topic` output** (a single list, same shape as before this change):
`"status"` is `"ok"`, `"halt"`, or `"error"` and the object also carries
`"mode": "topic"` and `"query"`. See the shared candidate shape and status
meanings below.

**`--paper` output** groups each requested relation independently -- no
cross-relation merge, dedup, or found-via tagging is done by the script;
that judgment is yours (see Subfield exploration below):
```json
{
  "status": "ok" | "halt",
  "paper": "DOI:10.1038/nature14539",
  "relations": {
    "citations": { "status": "ok", "count": 8, "candidates": [...], "notes": [] },
    "references": { "status": "error", "reason": "throttled", "message": "..." },
    "similar": { "status": "halt", "reason": "openalex_key_missing", "message": "...", "pending_abstract_lookups": [...], "count": 3, "candidates": [...], "notes": [] }
  },
  "message": "... present only when at least one relation halted ..."
}
```
Only the relations you requested appear as keys. Each relation entry uses
the same `"status"`/`"count"`/`"candidates"`/`"notes"` shape as a `--topic`
result (minus `"mode"`/`"query"`, which don't apply per relation). **A
throttle or error on one relation never discards another relation's
already-resolved candidates** -- read each relation's own `"status"`
independently rather than treating the whole call as failed because one
relation shows `"status": "error"`.

Per-candidate shape (identical in both modes), each entry in a
`"candidates"` list:
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
Present every candidate's `identifier` in conversation using this exact flat
shape -- it's the same shape `zotero-code-execution` (story 5) consumes for
filing, so no adapter is needed between the two skills. `"notes"` may
explain per-candidate gaps (e.g. "no abstract available").

Status meanings:

- **`"ok"`** -- (topic: top-level; paper: per relation, and top-level when
  no relation halted) candidates were resolved normally.
- **`"halt"`** (exit code 2 when it's the top-level status) -- an Ask-First
  condition was hit: OpenAlex is actually needed (Semantic Scholar returned
  no abstract for at least one candidate) but `OPENALEX_API_KEY` is unset in
  `.env`. In `--paper` mode this is scoped per relation -- one relation can
  halt while others report `"ok"`/`"error"`, and the top-level `"status"`
  becomes `"halt"` whenever any relation halts, carrying a summary
  `"message"` naming which relation(s) need a decision. Relay the relevant
  `"message"` field(s) to the researcher verbatim and ask them to choose:
  1. **Proceed Semantic-Scholar-only for this session** -- re-run the same
     search (same `--paper`/relation flags, or `--topic`) with
     `--no-openalex-fallback` added, and keep adding that flag to every
     `lit-search` call for the remainder of this conversation (the script
     has no memory across invocations -- Claude Code carries this choice,
     not the script).
  2. **Pause** so the researcher can set `OPENALEX_API_KEY` in `.env` first,
     then re-run the original command without the flag.

  Do not guess which the researcher wants, and do not silently retry without
  asking. Every already-resolved relation's `"candidates"` (and the halted
  relation's own partial `"candidates"`) are still present in the halt
  payload, so nothing already-good is thrown away while you wait for the
  answer.
- **`"error"`** (exit code 1 for `--topic`; per-relation only for `--paper`,
  which still exits 0 overall unless a halt also fired) -- `"reason"` is
  `"throttled"` (Semantic Scholar or OpenAlex was still returning HTTP 429
  after exponential backoff exhausted its retry budget), `"paper_not_found"`,
  or `"http_error"`. Tell the researcher what happened for that
  relation/query rather than retrying in a loop or treating it as an empty
  result.

Backoff on throttling happens inside the script automatically (bounded
retries with exponential backoff) -- Claude Code doesn't need to retry the
invocation itself when it sees a `429` in `stderr` progress lines; only act
on the final JSON on stdout.

## Rules

- Semantic Scholar is always primary. OpenAlex is only ever queried as a
  cross-check/fallback for a specific candidate's missing/thin abstract --
  never as the primary source for a search itself.
- Never surface a raw Semantic Scholar or OpenAlex JSON response to the
  researcher -- always the parsed candidate shape above.
- Never invent or guess a `doi`/`arxiv_id` when the API didn't return one --
  leave the field `null`.
- This skill has no write access anywhere: no Zotero filing, no
  duplicate-check, no `.bib` edits, and no automatic filing follow-up of any
  kind. Filing a chosen candidate is CAP-4/CAP-5 (story 5,
  `zotero-code-execution`), a fully separate, uncoupled capability out of
  scope here. Show candidates and let the researcher decide -- don't mention
  or suggest Zotero filing as part of this skill's own guidance.
- The script never merges, dedups, or found-via-tags candidates across
  relations -- when you request `--citations --references --similar`
  together, judge relevance from title/abstract, dedup across the three
  groups, and pick next seeds yourself. That's intentionally your job, not
  the script's.

## Subfield exploration

Use a combined `--paper` call (one or more of `--citations`/`--references`/
`--similar` together) to explore a seed paper's citation neighborhood and
help the researcher build a coherent subfield literature corpus. This
supersedes making three separate single-relation calls for the same seed.

**Workflow, per hop:**

1. **Pick a seed.** Start from a paper the researcher named, or a strong
   candidate surfaced earlier in the conversation.
2. **One combined call per hop.** Run `--paper SEED --citations --references
   --similar` (or whichever subset makes sense -- e.g. skip `--similar` if
   the researcher only wants graph-connected papers) in a single invocation
   rather than three separate ones.
3. **Judge relevance yourself.** Read each relation's `candidates` and
   decide, from title/abstract, which plausibly belong to the subfield the
   researcher is building. The script does no relevance filtering -- that's
   your job.
4. **Dedup across relations and hops yourself.** The same paper can appear
   in `citations`, `references`, and `similar`, or reappear from a later
   seed's expansion. The script never merges or tags these -- track what
   you've already surfaced across the conversation and don't re-present the
   same paper as if it were new.
5. **Pick the next seed(s) yourself.** From the relevant candidates, choose
   which paper(s) to expand from next, and say why, before making the next
   combined call.
6. **No fixed corpus-size target.** Keep expanding conversationally with the
   researcher -- driven by their sense of the subfield's boundary, not by
   hitting some candidate count. Don't stop at an arbitrary number and call
   it done, and don't keep expanding past the point the researcher is
   satisfied just to reach a round number.
7. **Push back when it doesn't converge.** If citation/reference/similar
   expansion starts pulling in papers that don't share a coherent subject
   with the seed (e.g. a broadly-cited method paper drags in unrelated
   application domains), say so explicitly: name what diverged and why, and
   propose one or more alternative directions (a narrower seed, a different
   relation to lean on, or a specific sub-cluster within the results) rather
   than presenting an incoherent mixed-topic list as if it were a subfield
   corpus. Never paper over a divergent result with a false "coherent
   subfield" narrative.
8. **Stay read-only.** This workflow never files, dedup-checks against
   Zotero, or writes anything -- it only surfaces candidates in conversation
   for the researcher to react to. Filing is a separate step the researcher
   drives explicitly, through a different capability.
