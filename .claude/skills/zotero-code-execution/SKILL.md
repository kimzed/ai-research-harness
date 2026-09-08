---
name: zotero-code-execution
description: Check for an existing Zotero item by DOI/title/arXiv id, resolve one live, pull its stored PDF/fulltext/abstract content into context, check the current default filing target, or file a researcher-confirmed paper into the local Zotero library. Use when the researcher confirms a candidate paper (from lit-search or otherwise) should be filed into Zotero, when Claude Code needs to read/summarize a paper already in the library, or when it needs to check for a duplicate, re-resolve a citekey, or see which collection filing would land in right now.
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

Six mutually exclusive modes:

```bash
# 1. What would filing hit right now? (AD-2's default target)
uv run .claude/skills/zotero-code-execution/zotero_file.py --check-target

# 2. Live-identifier search only, no write (AD-4) -- same shape lit-search emits.
uv run .claude/skills/zotero-code-execution/zotero_file.py \
  --check-duplicate '{"doi":"10.xxxx/yyyy","title":"...","arxiv_id":null}'

# 3. Re-resolve an already-filed item live (never cache across invocations).
uv run .claude/skills/zotero-code-execution/zotero_file.py \
  --resolve '{"doi":"10.xxxx/yyyy","title":"...","arxiv_id":null}'

# 4. File a researcher-confirmed candidate: dup-check, write, resolve, confirm.
uv run .claude/skills/zotero-code-execution/zotero_file.py \
  --file '{"doi":"10.xxxx/yyyy","title":"...","arxiv_id":null}' \
  --item '{"itemType":"journalArticle","title":"...","creators":[{"firstName":"A","lastName":"B","creatorType":"author"}],"date":"2024","DOI":"10.xxxx/yyyy","url":"..."}' \
  --researcher-confirmed

# 5a. Browse everything already filed in a collection, by id from
#     --check-target's "targets" (recurses into sub-collections by default).
uv run .claude/skills/zotero-code-execution/zotero_file.py --list-collection "C69"

# 5b. Or by name -- halts with candidates if the name is ambiguous across
#     libraries/parents (this library genuinely has that).
uv run .claude/skills/zotero-code-execution/zotero_file.py --list-collection "suitability_mapping"

# 5c. Direct members only, no sub-collections.
uv run .claude/skills/zotero-code-execution/zotero_file.py --list-collection "C44" --no-recursive

# 6. Pull a resolved item's content into context (CAP-5, AD-5): fulltext-index
#    cache, else local PDF path, else CSL abstract. Read-only, resolves live.
uv run .claude/skills/zotero-code-execution/zotero_file.py \
  --get-content '{"doi":"10.xxxx/yyyy","title":"...","arxiv_id":null}'
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

- The `--file`/`--check-duplicate`/`--resolve`/`--get-content` identifier
  JSON is the exact shared flat shape `lit-search` emits per candidate --
  `{"doi","title","arxiv_id"}`. Pass it straight through; no adapter (per
  the story's Always rule and ARCHITECTURE-SPINE.md's Consistency
  Conventions). At least one of the three must be a non-empty string --
  an all-null/all-blank identifier is rejected (`"invalid_input"`) rather
  than silently reporting "no duplicate found."
- `--get-content` resolves live (AD-4, same as `--resolve` -- never cached
  across calls) then, per AD-5, tries each attachment's `.zotero-ft-cache`
  sidecar first (across *every* attachment key before falling through --
  not just the first, and skipping an empty/whitespace-only cache file as
  a non-hit), then each attachment's local `.pdf` path (again across every
  key -- if one attachment folder somehow holds more than one `.pdf`, the
  alphabetically-first regular file wins), then the item's CSL `abstract`.
  It never opens or parses a PDF itself -- on a `"local_pdf"` hit it only
  returns the absolute path so Claude Code can read it natively for
  page/figure-level detail. Read-only: it reads from
  `~/Zotero/storage/` (that's the whole mechanism above) but never writes
  there or anywhere else. If multiple attachments each have usable
  content, the first one found (in Better BibTeX's own `item.attachments`
  order) wins silently -- if that's ever the wrong one in practice, ask
  the researcher rather than building disambiguation preemptively (per the
  story's Ask First clause). Set `ZOTERO_STORAGE_PATH` if the researcher's
  Zotero data directory isn't the default `~/Zotero/storage` (mirrors
  `ZOTERO_SQLITE_PATH` for `--list-collection`) -- a missing/misconfigured
  path there is reported as `"zotero_storage_unavailable"`, not silently
  treated as "no content."
- `--item` is a native Zotero item payload (itemType + fields + creators)
  for `--file` only -- assemble it from whatever the researcher confirmed
  plus anything `lit-search` already surfaced (title/year/authors/DOI at
  minimum; journal/volume/pages usually aren't in a `lit-search` candidate
  and will show up missing in `resolved_fields` after filing -- that's
  expected, see Field-completeness check below, not a bug). Its DOI (or
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
- **Which collection to pass comes from the contract, not from the UI.**
  Before any `--file`, read section 2 ("Zotero collection root") of
  `citation-contract.md` at the repo root:
  - **If it holds a decided collection root**, pass it explicitly via
    `--collection-id` (or `--collection-name`) on the `--file` call. Do not
    rely on whatever happens to be selected in the Zotero desktop UI at
    filing time -- AD-2 makes the per-project root the point, and a
    negotiated root that filing silently ignores is worse than no root at
    all. If the researcher wants this specific paper somewhere else, that is
    a deliberate one-off they state; confirm it, and don't quietly rewrite
    the contract on their behalf.
  - **If section 2 is still `_not-yet-negotiated_`** (or the file is
    missing), fall back to the existing behavior: `--check-target` first,
    show the researcher the live library/collection, and confirm with them
    before filing -- per AD-2's "ask when ambiguous rather than guess."
    Mention that running the `onboarding` skill would settle it once.
  - **If the contract names a collection the live target list doesn't have**
    (renamed or deleted in Zotero since it was negotiated), the call HALTs
    on `collection_not_found`/`ambiguous_collection_name` -- report that to
    the researcher and ask whether to re-point the contract, rather than
    silently falling back to the UI selection.
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

**`--resolve`** -- `{"status":"ok","found":bool, ...same match fields plus "resolved_fields" (CSL-JSON, same key name --file uses), "item_type_csl", "attachment_keys", "collections", "fields_lookup_failed", "note"}`.
A found item carries `"note"`: the same field-completeness reminder `--file`
and `--get-content` carry -- a `--resolve` is a touch like any other, so the
standing check below applies to it too.
`"collections"` is Better BibTeX's `item.collections` result passed
through verbatim (a list of `{"key","name","parentCollection"}`) -- this
skill doesn't independently validate or guarantee that shape, only relays
it.

**`--get-content`** (CAP-5, AD-5) -- `{"status":"ok","found":true,"source":"fulltext_index"|"local_pdf"|"abstract", "citekey", "item_key", "resolved_fields", ...}`:
- `"source":"fulltext_index"` -- a `.zotero-ft-cache` sidecar was found for
  some attachment (tried across *every* attachment key first, per AD-5).
  `"content"` holds the full cached text. No PDF is ever opened for this
  path.
- `"source":"local_pdf"` -- no cache on any attachment, but a locally-synced
  PDF was found (again tried across every attachment key). `"local_path"`
  is the absolute path to a real file on disk -- read it natively for
  page/figure-level detail.
- `"source":"abstract"` -- no cache and no local PDF on any attachment.
  The abstract text lives in `"resolved_fields"."abstract"` (already
  included per the rule above) -- there is no separate top-level
  `"content"` field for this case.
- `{"status":"ok","found":false}` -- the identifier matched nothing (same
  shape as `--resolve`'s not-found case).
- `{"status":"error","reason":"no_content_available"}` -- the item was
  found but has no cached fulltext, no local PDF, and no abstract across
  every attachment tried; `"citekey"`/`"item_key"` are still included for
  context.
- `{"status":"error","reason":"fields_lookup_failed"}` -- a match was found
  but Better BibTeX hasn't assigned it a citekey yet (the same indexing
  race `--file`'s `"resolve_after_write_failed"` guards against, most
  likely right after a fresh filing) -- re-run `--get-content` for this
  identifier in a moment; this is distinct from `"no_content_available"`
  and must not be reported to the researcher as "no content."
- `{"status":"error","reason":"zotero_storage_unavailable"}` -- the item
  has attachments to look up but the configured storage root (default
  `~/Zotero/storage`, or `ZOTERO_STORAGE_PATH` if set) doesn't exist on
  disk -- a configuration problem, not "no content available." Check/set
  `ZOTERO_STORAGE_PATH`.
- Every hit (`fulltext_index`/`local_pdf`/`abstract`) also carries the same
  field-completeness reminder `--file` gives on success -- the "Field-
  completeness check" section below is a standing check on every touch,
  including a read (CAP-1/CAP-5), not just filing.
- Read-only and resolves live every call (AD-4) -- never caches a
  citekey/item-key/attachment-key mapping, and never writes to Zotero or
  `~/Zotero/storage/`.

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
`true`, `resolved_fields` is `null` and the field-completeness check below
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
again), `"no_content_available"` (`--get-content` only -- the item was
found but had no cached fulltext, no local PDF, and no abstract across
every attachment tried), `"fields_lookup_failed"` (`--get-content` only --
matched but Better BibTeX hasn't assigned a citekey yet; re-run in a
moment, distinct from `"no_content_available"`), `"zotero_storage_unavailable"`
(`--get-content` only -- the configured storage root doesn't exist on
disk; check/set `ZOTERO_STORAGE_PATH`), or `"unexpected_error"` (an
unhandled failure of some other kind -- still a single JSON object, never
a raw traceback, per AD-8).

## Field-completeness check (every touch: filed, resolved, or read)

Run this against `resolved_fields` after every `--file`, `--resolve`, and
`--get-content` -- not once at setup. It is a **standing** check: the
researcher edits Zotero directly between calls, so an item that passed
yesterday can fail today.

The behavior in this section is **fixed mechanism, not a per-project
preference**. It applies whatever the project's contract turns out to say.

### Where the checklist comes from

The checklist itself is *not* here and is not a default anyone gets to assume.
It lives in **`citation-contract.md` at the repo root** -- this instance's
negotiated citation contract, generated by the `onboarding` skill from
`.claude/skills/onboarding/templates/citation-contract.md`.

- **If `citation-contract.md` is missing, or the section you need still reads
  `_not-yet-negotiated_`: HALT.** Tell the researcher the contract hasn't been
  negotiated for this project and point them at the `onboarding` skill. Never
  fall through to the template's proposal tables as though they were decided,
  never invent a field set, and never let a citation-emitting flow proceed on
  biblatex's implicit `numeric` default by omission.
- **Reference-type tables are marked independently.** Each type's table in the
  contract carries its own `Negotiated decision:` marker, so check the marker
  on the table for *this item's* type -- an item whose own type is still
  `_not-yet-negotiated_` HALTs even when every other table has been agreed.
- **If the contract has no row for the item's reference type** (a dataset, a
  preprint, a report, a web page, software...): HALT, ask the researcher for
  that type's checklist, and append the answer to `citation-contract.md`.
  Never silently pass the item, and never silently block it either.

### Field-name mapping

Better BibTeX returns CSL-JSON, not raw BBT export field names. The contract is
written in biblatex-flavored BBT names, so map like this:

| Checklist field (`citation-contract.md`) | `resolved_fields` (CSL-JSON) |
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

If `"fields_lookup_failed"` is `true`, `resolved_fields` is `null` and this
check cannot run yet -- re-run `--resolve` before telling the researcher the
citekey is ready to cite. Don't report an unrunnable check as a passing one.

### Detection & remediation

- **Required fields only.** A missing **Recommended** or **Optional** field is
  mentioned to the researcher once and never triggers the lookup chain below.
- **On a missing or malformed Required field**, before ever asking the
  researcher to supply it manually, attempt an external lookup for that
  specific value, in this order: (1) DOI resolution, if a `doi` is present;
  (2) Semantic Scholar, by DOI or title; (3) OpenAlex, by DOI or title, as
  cross-check/fallback -- mirroring CAP-3's Semantic-Scholar-primary,
  OpenAlex-fallback pattern, via `lit-search` or a direct DOI lookup. For
  `@book`/`@thesis` items (rarely indexed by either), skip straight to asking
  the researcher directly.
- **Ask First on anything recovered.** Any value the lookup chain produces is
  shown to the researcher for confirmation before it is used anywhere. This
  skill files only *new* items -- it has no update-existing-item mode -- so a
  confirmed value is handed to the researcher to enter into Zotero themselves,
  never written back by Claude Code. If the researcher rejects the recovered
  value, treat the field as unresolved and fall through to the next rule --
  never re-propose the same rejected value and never invent an alternative.
- **If external lookup can't resolve the value (or the researcher rejects what
  it found): HALT and ask the researcher directly.** Never proceed silently and
  never invent a plausible-looking value. If they confirm the value is
  genuinely unobtainable (e.g. a pre-DOI-era print-only source with no `doi` or
  `url`), record it as an **accepted gap for that specific item**, so the
  standing check stops re-flagging it on every future touch. An accepted gap is
  per-item; it is never a change to the checklist itself.
- **Never write into `manuscript/references.bib`** to "fix" a missing or
  malformed field -- it is a generated export (AD-2), and any correction goes
  into Zotero itself.
- This detection/remediation behavior is canonically defined in
  `spec-zotero-citation-field-contract.md` in the harness planning repo (the
  sibling planning repo's `_bmad-output/implementation-artifacts/`).

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
