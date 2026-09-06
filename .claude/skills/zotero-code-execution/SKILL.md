---
name: zotero-code-execution
description: Check for an existing Zotero item by DOI/title/arXiv id, resolve one live, check the current default filing target, or file a researcher-confirmed paper into the local Zotero library. Use when the researcher confirms a candidate paper (from lit-search or otherwise) should be filed into Zotero, or when Claude Code needs to check for a duplicate, re-resolve a citekey, or see which collection filing would land in right now.
---

# zotero-code-execution

Wraps every local Zotero read/write this repo performs (CAP-4/CAP-5, AD-1,
AD-2, AD-3, AD-4, AD-8). **Never call the Connector or Better BibTeX
endpoints directly from conversation** -- always invoke `zotero_file.py`
and read its parsed JSON output.

Story 5 settled AD-3 as a **closed decision, approved by the researcher on
2026-09-06**: writes go through Zotero desktop's local Connector HTTP
endpoint (`POST localhost:23119/connector/saveItems`, real and functional,
undocumented for third-party use), never Zotero's cloud web API. The
alternative candidate -- a companion "Zotero Write Endpoint" plugin -- was
sourced and evaluated but rejected, not pursued further; see the story's
Spec Change Log for the detailed rationale (star count, maintenance
posture, etc. -- deliberately not repeated here, since those third-party
facts will go stale):
`_bmad-output/specs/spec-ai-research-harness/stories/5-zotero-write-mechanism-spike-filing.md`
in the sibling planning repo.

Every resolve/dup-check read goes through Better BibTeX's local JSON-RPC
endpoint (`localhost:23119/better-bibtex/json-rpc`) -- the only mechanism
that can recover a BBT citation key at all. Both endpoints share the same
local Zotero HTTP server; nothing here ever leaves the machine, and Zotero
desktop must be running with "Allow other applications on this computer to
communicate with Zotero" enabled (Settings -> Advanced).

## Invocation

Five mutually exclusive modes:

```bash
# 1. What would filing hit right now? (AD-2's default target)
uv run .claude/skills/zotero-code-execution/zotero_file.py --check-target

# 2. Live-identifier search only, no write (AD-4) -- same shape lit-search emits.
uv run .claude/skills/zotero-code-execution/zotero_file.py \
  --check-duplicate '{"doi":"10.xxxx/yyyy","title":"...","arxiv_id":null}'

# 3. Re-resolve an already-filed item live (never cache across invocations).
uv run .claude/skills/zotero-code-execution/zotero_file.py \
  --resolve '{"doi":"10.xxxx/yyyy","title":"...","arxiv_id":null}'

# 5a. Browse everything already filed in a collection, by id from
#     --check-target's "targets" (recurses into sub-collections by default).
uv run .claude/skills/zotero-code-execution/zotero_file.py --list-collection "C69"

# 5b. Or by name -- halts with candidates if the name is ambiguous across
#     libraries/parents (this library genuinely has that).
uv run .claude/skills/zotero-code-execution/zotero_file.py --list-collection "suitability_mapping"

# 5c. Direct members only, no sub-collections.
uv run .claude/skills/zotero-code-execution/zotero_file.py --list-collection "C44" --no-recursive

# 4. File a researcher-confirmed candidate: dup-check, write, resolve, confirm.
uv run .claude/skills/zotero-code-execution/zotero_file.py \
  --file '{"doi":"10.xxxx/yyyy","title":"...","arxiv_id":null}' \
  --item '{"itemType":"journalArticle","title":"...","creators":[{"firstName":"A","lastName":"B","creatorType":"author"}],"date":"2024","DOI":"10.xxxx/yyyy","url":"..."}' \
  --researcher-confirmed
```

**`--list-collection COLLECTION_REF`** -- use this whenever you need to know
what's *already* in a collection (e.g. before proposing where a subfield
reading list should be filed, or to sanity-check whether a folder someone
mentions actually has anything in it). `COLLECTION_REF` is either a
Connector-style id (`"C69"`, from `--check-target`'s `"targets"`) or a
collection name. Mechanically different from every other mode: it reads
`zotero.sqlite` directly instead of going through Connector/BBT -- see the
script's module docstring if you need the why. Still fully read-only and
fully local.

- The `--file`/`--check-duplicate`/`--resolve` identifier JSON is the exact
  shared flat shape `lit-search` emits per candidate --
  `{"doi","title","arxiv_id"}`. Pass it straight through; no adapter (per
  the story's Always rule and ARCHITECTURE-SPINE.md's Consistency
  Conventions). At least one of the three must be a non-empty string --
  an all-null/all-blank identifier is rejected (`"invalid_input"`) rather
  than silently reporting "no duplicate found."
- `--item` is a native Zotero item payload (itemType + fields + creators)
  for `--file` only -- assemble it from whatever the researcher confirmed
  plus anything `lit-search` already surfaced (title/year/authors/DOI at
  minimum; journal/volume/pages usually aren't in a `lit-search` candidate
  and will show up missing in `resolved_fields` after filing -- that's
  expected, see Required-field checklist below, not a bug). Its DOI (or
  title, if it has no DOI) must match the `--file` identifier argument --
  a mismatch is rejected as invalid input rather than filed. Any `"id"`
  field you put in `--item` is silently overwritten -- the script always
  assigns its own client-side item id internally (needed to move the item
  via `updateSession`), so don't rely on a caller-supplied one surviving.
- `--researcher-confirmed` is **mandatory** for `--file` and is not a
  formality: only pass it after the researcher has explicitly confirmed
  *this exact candidate* in the conversation. Never pass it right after
  just showing search results -- that would be auto-filing, which the PRD
  and AD-4 forbid outright.
- `--collection-id "C83"` / `--collection-name "..."` (optional, `--file`
  only): move the filed item into a specific collection instead of
  accepting whatever's currently selected in the Zotero desktop UI
  (AD-2's default, mirroring the browser connector). Both are validated
  against the live target list before anything is written -- an unknown
  `--collection-id` HALTs just like an unknown `--collection-name` rather
  than silently targeting nothing. Use `--check-target` first if you need
  to ask the researcher which collection to use, or when the
  currently-selected one doesn't obviously fit this paper. `--collection-
  name` matching is case-insensitive and trimmed, and a name that matches
  more than one collection (this library genuinely has same-named
  collections nested at different levels) HALTs rather than guessing --
  resolve with `--collection-id` from the `candidates` it returns.
- `--override-duplicate-match` (optional, `--file` only): file anyway
  after the researcher reviews a *title-only* fuzzy match (substring
  search, no DOI/arXiv id involved) and confirms it's a different paper.
  It can never override an exact DOI/arXiv match -- that block is
  unconditional.

## Output contract

Always one JSON object on stdout, never a raw Connector/BBT response
(AD-8). `"status"` is `"ok"` (exit 0), `"halt"` (exit 2), or `"error"`
(exit 1).

**`--check-target`** -- `{"status":"ok","library":{...},"collection":{...}|null,"targets":[...]}`.
Show the researcher `collection`/`library` if it's not obviously right for
the paper being filed, per AD-2's "ask when ambiguous rather than guess."

**`--check-duplicate`** -- `{"status":"ok","duplicate_found":bool,"matches":[...]}`.
Each match carries `"confidence"`: `"doi_exact"` / `"arxiv"` (authoritative
-- treat as the same paper) or `"title_contains"` (heuristic substring
match -- judge it, don't assume).

**`--resolve`** -- `{"status":"ok","found":bool, ...same match fields plus "resolved_fields" (CSL-JSON, same key name --file uses), "item_type_csl", "attachment_keys", "collections", "fields_lookup_failed"}`.
`"collections"` is Better BibTeX's `item.collections` result passed
through verbatim (a list of `{"key","name","parentCollection"}`) -- this
skill doesn't independently validate or guarantee that shape, only relays
it.

**`--list-collection`** -- `{"status":"ok","library":{...},"collection":{"id","name"},"recursive":bool,"sub_collections_included":[{"id","name"},...],"count":int,"items":[...],"attachments_and_notes_excluded":int}`.
Each entry in `"items"` is `{"item_key","title","item_type","year","identifier":{"doi","title","arxiv_id"}}`
-- the `"identifier"` is the same flat shape `lit-search`/`--check-duplicate`/
`--resolve` use, so pass it straight through to either of those if you need
a citekey or full CSL fields for a specific item. Attachments/notes/
annotations are counted but excluded from `"items"` (they're not papers).
`"halt"`/`"ambiguous_collection_name"` (with `"candidates"`, same shape as
`--file`'s) fires when a name matches more than one collection -- ask the
researcher, or retry with the specific `"C<id>"`. `"error"`/`"invalid_input"`
fires when the id/name doesn't exist at all.

**`--file`** success -- `filed:true`:
```json
{
  "status": "ok",
  "filed": true,
  "duplicate_found": false,
  "citekey": "...",
  "item_key": "...",
  "library": "My Library",
  "collections": [{"key": "...", "name": "...", "parentCollection": false}],
  "attachment_keys": [],
  "item_type_csl": "article-journal",
  "resolved_fields": { "...CSL-JSON as Better BibTeX returns it..." },
  "fields_lookup_failed": false
}
```
Confirm `citekey` back to the researcher in this same turn (AD-3/AD-4) --
`"filed":true` never appears with a null `citekey`; if a citekey can't be
recovered after the write, the response is `"status":"error","reason":
"resolve_after_write_failed"` instead (the item still exists -- don't
file it again, re-run `--resolve` shortly). If `"fields_lookup_failed"` is
`true`, `resolved_fields` is `null` and the Required-field checklist below
can't run yet -- re-run `--resolve` before telling the researcher the
citekey is ready to cite. `attachment_keys` is usually empty for a
metadata-only filing from a search candidate (no PDF attached); that's
expected, not a failure -- CAP-5's PDF pull is a separate capability.

**`--file`** duplicate found -- `filed:false, duplicate_found:true`:
report the existing `citekey` (or, if Better BibTeX hadn't assigned one,
the `item_key`) from `matches` instead of filing. An exact DOI/arXiv match
blocks unconditionally; a title-only match is shown to the researcher
first (see `--override-duplicate-match` above).

**`"halt"`** reasons: `"zotero_not_running"` (Zotero desktop unreachable --
ask the researcher to start it; never retry silently, per the matrix),
`"zotero_timeout"` (Zotero reachable but slow/unresponsive -- distinct
from not running; don't tell the researcher to start Zotero for this one),
`"collection_not_found"` / `"ambiguous_collection_name"` (ask which
collection the researcher means, using the `targets`/`candidates` list
returned -- this now applies to `--collection-id` as well as
`--collection-name`).

**`"error"`** reasons: `"invalid_input"` (malformed/empty identifier,
non-object `--item`, or `--item`'s DOI/title disagreeing with the `--file`
identifier -- fix the input, nothing was attempted), `"zotero_api_error"`
/ `"http_error"` (an unexpected Connector/BBT failure -- report it rather
than retrying in a loop), `"resolve_after_write_failed"` /
`"move_to_collection_failed"` (the write itself succeeded but a follow-up
step didn't -- the response still carries whatever citekey/item_key/
library could be recovered; the item already exists, don't file it
again), or `"unexpected_error"` (an unhandled failure of some other kind --
still a single JSON object, never a raw traceback, per AD-8).

## Required-field checklist (post-file, before confirming the citekey)

This story is what makes CLAUDE.md's "Citation-format & field contract"
section exercisable (previously flag-only, no write path to check
against). After a successful `--file`, run that section's negotiated
checklist (currently a starter proposal, not yet negotiated for a real
instance -- negotiate first if this is the first real filing) against
`resolved_fields`. Better BibTeX returns CSL-JSON, not raw BBT export
field names -- map like this:

| Checklist field (CLAUDE.md) | `resolved_fields` (CSL-JSON) |
| --- | --- |
| `author`/`editor` | `author` / `editor` (array of `{family, given}`) |
| `title` | `title` |
| `journaltitle` | `container-title` |
| `booktitle` | `container-title` |
| `date` | `issued.date-parts` |
| `volume` | `volume` |
| `number` | `issue` |
| `pages` | `page` |
| `doi` | `DOI` |
| `url` + `urldate` | `URL` + `accessed.date-parts` |
| `issn`/`isbn` | `ISSN`/`ISBN` |
| `langid` | `language` |
| `publisher` | `publisher` |
| `location` | `publisher-place` / `event-place` |
| `institution` (thesis) | `publisher` |
| `type` (thesis degree level) | `genre` |
| item type (for picking which checklist table) | `item_type_csl`: `"article-journal"`->`@article`, `"paper-conference"`->`@inproceedings`, `"book"`->`@book`, `"thesis"`->`@thesis` |

On a gap in a **Required** field, follow
`spec-zotero-citation-field-contract.md`'s external-lookup-then-ask chain
(DOI resolution -> Semantic Scholar -> OpenAlex, via `lit-search` or a
direct DOI lookup) before asking the researcher -- never invent a value,
and never write a recovered value back into Zotero yourself: this skill
only files *new* items, it has no update-existing-item mode yet, so hand
any confirmed recovered value to the researcher to enter into Zotero
themselves (unchanged from that spec's Ask-First rule).

## Rules

- **Always** run `--check-duplicate` (or let `--file` run its internal
  dup-check) before filing anything, and file only when the researcher has
  explicitly confirmed the specific candidate -- never a top search result
  on its own (`--researcher-confirmed` enforces the mechanical half of
  this; the conversational confirmation itself is on you, not the script).
- **Always** re-resolve live after any write, and confirm the citekey to
  the researcher in the same turn -- a bare "filed successfully" without a
  citekey doesn't satisfy AD-3/AD-4.
- **Always** treat `references.bib` as still read-only -- this skill itself
  never writes to that file directly; any addition goes into Zotero
  directly. Better BibTeX's own auto-export then updates
  `references.bib` on disk as an indirect side effect of any Zotero write
  (confirmed live during this story: filing a disposable spike-test item
  made it appear in `references.bib` on its own, with no code in this
  skill touching the file) -- that's BBT's normal behavior, not a
  violation of "read-only," but don't be surprised by it showing up as a
  changed file in `git status` right after a `--file` call.
- **Never** search or resolve by Better BibTeX citekey -- always DOI/title/
  arXiv id (AD-4; BBT's maintainer disabled citekey search server-side, and
  a cached key would drift from the real library).
- **Never** cache a citekey/item-key/attachment-key mapping across
  invocations -- each call re-resolves live, even within the same
  conversation.
- **Never** leave a disposable spike/test item in the researcher's real
  library. If you ever create one while diagnosing an issue with this
  skill, tell the researcher exactly which collection/citekey to delete --
  there is no delete endpoint available through the Connector or Better
  BibTeX APIs, so cleanup is a manual step in the Zotero desktop UI.
  **Outstanding from this story's own live spike/verification testing:**
  four such test items are still sitting in the `test_ai_research_harness`
  collection
  (titles contain "safe to delete (story 5" / "SPIKE-TEST") -- mention this
  to the researcher the first time this skill comes up in conversation,
  since nothing else surfaces it to them proactively.
