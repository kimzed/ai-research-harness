---
name: onboarding
description: Onboard a freshly instantiated research-harness project - take stock of what already exists, discuss and record what the project is actually about, and negotiate this instance's citation contract into a repo-root `citation-contract.md`. Use when the researcher says "onboard this project", "set up this project", "this is a new project", "what is this project about", or when any citing/filing flow HALTs because `citation-contract.md` is missing or still `_not-yet-negotiated_`.
---

# onboarding

A methodology skill, not an API wrapper -- there is no `onboarding.py`. It runs
a conversation, once per instantiated project, and its only durable outputs are
a note in `knowledge-base/` and a generated `citation-contract.md` at the repo
root. Same no-wrapper-script shape as `subfield-lit-mapping` (instructions
only) and `latex-presentation` (instructions plus a `templates/` folder, which
is the part this skill also has).

Everything mechanical it needs already belongs to another skill. Probing Zotero
is `zotero-code-execution`'s job (`uv run
.claude/skills/zotero-code-execution/zotero_file.py --check-target` /
`--list-collection` -- see that skill for the full contract); the
vault's approval rules are `CLAUDE.md`'s "Obsidian research knowledge base"
section. Do not restate either here, and do not reimplement them.

## What this skill is for

A fresh instance of this template ships mechanism only: skills, conventions,
empty content folders. It ships **no** `citation-contract.md` -- that absence
is precisely the signal that onboarding has not run. This skill turns a generic
template into *this researcher's* project by asking, and it records the answers
where later sessions will actually find them.

**Nothing here is assumed.** Every decision is asked. The tables that ship in
`templates/citation-contract.md` are labelled starting proposals for exactly
this reason: a plausible-looking default is not a decision, and writing one in
as though it were is the specific failure this skill exists to prevent.

## Resumability -- read this before starting anywhere

The four phases below are **individually skippable and individually
resumable**. This is not an all-or-nothing script. Phase 1 (take stock) is what
decides where to start:

- `citation-contract.md` absent, vault empty, nothing in `manuscript/` --
  run all four phases.
- `citation-contract.md` exists with a decided style but a
  `_not-yet-negotiated_` field checklist -- report what is already decided,
  skip it, and resume at the unfinished section only. Never re-negotiate a
  section that already holds a real decision unless the researcher asks.
- A project-idea note already in `knowledge-base/` -- read it, reflect it back,
  and ask whether it still holds rather than re-interviewing from zero.
- The researcher explicitly wants only one piece ("just do the citation
  contract") -- do that phase, then say plainly in the wrap-up which phases
  were skipped and remain open.

Always run phase 1 first, even for a single-phase request: knowing what already
exists is what keeps the rest from clobbering it.

## Phase 1 -- Take stock

Look before asking. Report findings back as a short inventory, then ask about
anything the repo cannot tell you.

Probe, in whatever order is convenient:

- `citation-contract.md` at the repo root -- present? Which sections still say
  `_not-yet-negotiated_`? This is the single most load-bearing probe; it
  decides which of the phases below are still open.
- `knowledge-base/` -- anything beyond `README.md` and `.gitkeep`?
- `manuscript/` -- is `main.tex` still the scaffold, is `style=` set on the
  `biblatex` package, does `manuscript/sections/` hold anything real?
- `analysis/` and `data/` -- any scripts, any inputs? (`data/` is gitignored
  under AD-9; look, but never commit anything from it.)
- `manuscript/references.bib` -- how many entries? A single leftover
  mechanism-test entry means effectively empty; treat a real library as a
  strong signal the researcher has been working already.
- `presentations/` -- any decks.
- Zotero, via `zotero-code-execution`:

  ```bash
  uv run .claude/skills/zotero-code-execution/zotero_file.py --check-target
  ```

  to see which library/collection filing would hit right now and what the
  available targets are.

  **Any non-`"ok"` result here is a finding, not a failure.** That covers
  `zotero_not_running` (desktop closed) and equally `zotero_timeout`,
  `zotero_api_error`, `http_error`, `zotero_database_unavailable`, or any other
  `"halt"`/`"error"` status the script returns. In every one of those cases:
  report what came back in the script's own words, carry on with the rest of
  onboarding, and leave the collection-root decision (contract section 2)
  unfilled and explicitly flagged. Never guess a collection root from a target
  list you could not read, never retry in a loop, never diagnose it as
  "Zotero isn't running" when the status says something else, and never treat
  the contract as complete while that section is unfilled.

Then ask about what the repo cannot see:

- Is there existing material outside this repo -- a folder of PDFs, a `.bib`
  file, draft chapters, notes, an analysis script -- that should live here?
- If the researcher names such a place, **inventory it and report back what is
  there** before proposing anything.
- Propose where each item would land (`manuscript/`, `analysis/`, `data/`,
  `knowledge-base/`, or Zotero), one proposal at a time if the set is mixed.
- **Move, copy, rename, or organize only on explicit approval.** Never relocate
  or delete anything unasked, including inside the folders they point you at.
  A `.bib` file in particular is never merged into `manuscript/references.bib`
  by hand -- that file is Better BibTeX's one-directional export (AD-2); the
  right move is filing the underlying items into Zotero.

Close the phase with a plain statement of what exists, what is empty, and which
of phases 2-4 are therefore still open.

## Phase 2 -- What the project is actually about

Ask a **few questions at a time**, not a form. Let the answers steer the next
few. Roughly what needs to be known:

- The topic, in the researcher's own words.
- The research question or hypothesis, as far as it is settled.
- The field/subfield (this matters again in phase 3 -- citation conventions
  differ sharply across fields).
- The output type: journal article, thesis chapter, full thesis, conference
  paper, report, something else.
- The target venue or thesis style guide, if there is one yet. "Not decided
  yet" is a real answer -- record it as such; it makes the style question in
  phase 3 a live one rather than a formality.
- A working title.
- What stage this is at: idea, literature review, data collected, analysis
  underway, drafting.

Then:

1. **Reflect a summary back** in your own words and let the researcher correct
   it. Corrections here are cheap; a wrong premise recorded into the vault is
   not.
2. **Draft the note** and show it in the conversation.
3. **Write it to `knowledge-base/` only after the researcher approves it** --
   the vault's standing rule from `CLAUDE.md`, unchanged: draft, approve, then
   write.
4. **Propose the filename**, do not impose one. `knowledge-base/` deliberately
   has no taxonomy, and this note is likely its first real content -- that is
   not licence to invent a folder structure on its behalf. Suggest something
   plain, ask, and use what they choose.
5. **If that filename already exists, never overwrite it.** Show the researcher
   what is already in that file and ask which they want: append the new
   material to it, write under a different name, or replace it outright. A
   replace is a content deletion, so it needs their explicit yes -- silently
   clobbering an existing note is the one outcome this step exists to prevent.
   This applies on a resumed run too, where an earlier session's idea note is
   exactly what you are likely to collide with.
5. Never invent detail the researcher did not actually say. "Not decided yet"
   goes into the note as "not decided yet".

If the researcher would rather not record anything yet, that is a decline: skip
the write, keep what you learned for phase 3, and say in the wrap-up that no
note exists.

## Phase 3 -- Negotiate the citation contract

This is the phase that produces `citation-contract.md`. Read
`templates/citation-contract.md` first -- it holds the starting proposals and
the standing rules, and the generated file is derived from it.

Negotiate, section by section, in this order:

1. **Citation style.** Phase 2's target venue/style guide is the input here,
   not the answer -- ask. If the venue mandates a style, confirm which biblatex
   style implements it; if nothing is decided yet, ask what they want for now
   and record that it can change. Never fall through to biblatex's implicit
   `numeric` default by omission.
2. **Zotero collection root (AD-2).** Use
   `uv run .claude/skills/zotero-code-execution/zotero_file.py --check-target`
   for the live target list, and the same script's
   `--list-collection "<id-or-name>"` if the researcher wants to see what is
   already in a candidate collection before choosing. If that probe returned
   anything other than `"ok"` in phase 1, leave this section unfilled and
   flagged, and say so -- do not guess.
3. **Field checklist per reference type.** Walk the four proposal tables with
   the researcher: are the Required/Recommended/Optional splits right for their
   field? Ask which reference types they actually expect to cite -- preprints,
   datasets, software, reports, and book chapters are all common and none of
   them has a proposal row. Add rows for what they name.
4. **Malformed-field format.** Confirm the ISO-8601 date proposal, or record
   what they use instead.

Then write the outputs.

**Writing `citation-contract.md` at the repo root -- two different cases.**
Check whether the file already exists *before* writing anything.

- **No `citation-contract.md` yet (first run):** copy
  `templates/citation-contract.md` to the repo root, apply the header swap the
  template's own "Instruction to the generator" block specifies (so the
  generated file identifies itself as this project's contract rather than as
  the template), then replace the `_not-yet-negotiated_` markers for the
  sections just decided.
- **`citation-contract.md` already exists (resumed run): never copy the
  template over it.** Edit it in place, and touch *only* the sections whose
  marker still reads `_not-yet-negotiated_`. A section already holding a real
  decision is left exactly as it stands -- including its wording, its recorded
  date, and any rows the researcher added by hand since. Overwriting a
  negotiated decision with a fresh template proposal would silently discard
  their agreement, which is the specific failure the resumability rules above
  exist to prevent. If the researcher wants a settled section *re*-negotiated,
  they will say so; that is a deliberate change they ask for, and it is worth
  noting what the previous decision was when you replace it.

In both cases:

- Replace a marker with the actual decision, and record the date and who agreed
  it -- a decision with no provenance is hard to revisit later.
- Drop the "starting proposal" wording from any table that was confirmed. Each
  reference-type table has its own marker, so confirming `@article` while
  `@thesis` stays open is a normal state to write down, not a problem.
- Keep the standing "uncovered type/field -> ask and append here" rule verbatim.
- Leave anything the researcher did not decide as `_not-yet-negotiated_` -- an
  honest gap beats a fabricated decision, and this skill is resumable precisely
  so that gap can be closed later.

**Setting `style=` in `manuscript/main.tex`.** If style was decided, set it in
`\usepackage[backend=biber,style=<decided>]{biblatex}` and update the comment
above it. If it was not decided this session, leave `style=` unset and leave
the comment pointing back here.

After setting it, compile via the `latex-compile` skill: a style whose `.bbx`
is not installed on this machine fails at build time, not at decision time, so
the decision is not really verified until the document builds.

**If that compile fails**, never leave `manuscript/main.tex` in a non-building
state without saying so. Report the failure in the compiler's own terms, then
take one of two paths with the researcher -- their choice, not yours:

- **Revert `style=` to unset**, so the manuscript keeps building, and record
  the style in the contract as decided-but-not-yet-applied together with what
  the build needs (usually a missing TeX package).
- **Keep `style=` as decided**, and record in the contract that it is
  decided-but-not-yet-compilable on this machine, naming the missing package.
  The manuscript will not build until it is installed -- say that plainly in
  the wrap-up rather than leaving the next session to discover it.

Either way the contract records the truth. What is not acceptable is a silent
half-state: a style written into `main.tex` that nobody mentioned cannot build.

The contract lives at the repo root, not inside this skill: `.claude/skills/`
is mechanism copied verbatim into every new instance, while the contract is
negotiated per-project state, in the same category as `manuscript/` and
`knowledge-base/`.

## Phase 4 -- Wrap up

Close with an explicit ledger, not a "done":

- What is now **decided and recorded**, and in which file.
- What is still **unfilled** -- every remaining `_not-yet-negotiated_` section,
  every phase skipped, anything left flagged because Zotero was unreachable or
  the researcher wanted to think about it.
- That **each phase is independently resumable**: re-invoking this skill picks
  up from take-stock and only touches what is still open, so leaving something
  open now costs nothing later.
- If the field checklist or style is still unnegotiated, say plainly that
  citing and filing flows will HALT and point back here until it is -- that is
  the designed behavior, not a bug.

Never end this skill by describing an unfinished contract as finished.
