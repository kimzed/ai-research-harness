---
name: latex-presentation
description: Draft a Beamer (LaTeX) slide deck from a topic, outline, or talking points the researcher gives in conversation, then compile it via the latex-compile skill. Use when the researcher wants to "make a presentation/slides/deck", "turn these results into slides", "build a Beamer talk", or otherwise wants a `presentations/<slug>/main.tex` produced and compiled -- not for editing `manuscript/` (that's the manuscript writing flow) and not for running the LaTeX build loop directly (that's `latex-compile`).
---

# latex-presentation

A methodology skill, not an API wrapper -- there is no `latex_presentation.py`.
This skill drives Claude directly to draft populated Beamer frames straight
into `presentations/<slug>/main.tex`, then hands off to the existing
`latex-compile` skill to build it (same shape as `subfield-lit-mapping`:
instructions only, no dedicated wrapper script). If you find yourself
explaining `latexmk` flags or regexing compiler log text, that belongs in
`latex-compile`'s `SKILL.md` instead -- never reimplement compilation or
compile-error parsing here.

## Directory convention

Every deck gets its own directory, parallel to (never inside) `manuscript/`:

```text
presentations/<slug>/
  main.tex
  images/       # static images the researcher supplies for this deck
  generated/    # AD-7: figures copied from analysis/outputs/, with provenance
```

`<slug>` is a short kebab-case identifier for the deck (e.g. a talk's venue
or subject: `q3-results`, `thesis-defense`). Propose one from the topic the
researcher gives you and confirm it with them rather than guessing silently
when it's ambiguous. Never write into `manuscript/` or its subfolders from
this skill -- a presentation is not manuscript content, even when it draws
on the same analysis results.

`default-preamble.tex`'s `\graphicspath{{./images/}}` only covers `images/`
-- it does not cover `generated/`. Reference a generated figure with an
explicit `generated/<file>` relative path in `\includegraphics{}` (e.g.
`\includegraphics{generated/results-plot.png}`), never a bare filename that
relies on `graphicspath` finding it.

## Gathering content

Draft frame content only from what the researcher actually provides in the
conversation -- an outline, talking points, results they've already
produced, a paper/section they point you at. **Never fabricate a claim,
number, or citation to fill a slide** just because a frame looks sparse; if
the researcher hasn't given you enough to fill a section, ask rather than
inventing filler. This mirrors AD-7/CAP-7's manuscript rule -- it isn't
relaxed just because the deliverable is slides instead of prose.

Before drafting, get from the researcher (or infer and confirm, don't
silently assume):

- the topic/scope and the section structure (a rough outline is enough --
  you don't need a full script per slide)
- any figures/plots they want included, and where each one comes from
  (`analysis/outputs/`, a `manuscript/generated/`-style path, or a plain
  static image they'll hand you) -- see Figures below
- whether they want the default look or something different -- see Theming
  below

A frame containing verbatim or code content needs Beamer's `[fragile]`
frame option (`\begin{frame}[fragile]`) -- without it, verbatim/code
environments inside a frame won't compile.

## Theming: default vs. override

- **Default (no theming request):** start the new `main.tex` from
  `templates/default-preamble.tex` verbatim (Madrid theme, the custom
  footline showing section/subsection + frame count, the
  `AtBeginSection` divider frame, the titlepage) -- fill in the
  `\title`/`\author` placeholders, then append drafted `\section{...}` +
  `\begin{frame}...\end{frame}` content after the marker near the bottom of
  that file. Use `templates/frame-patterns.tex` as a reference for layout
  options (two-column text+figure, equations+itemize, block+figure) --
  those are style examples to draw structure from, never content to
  reproduce verbatim.
- **Override (researcher asks for different theming/layout/colors):** start
  from a blank or alternate preamble instead -- do not layer the
  researcher's requested theme on top of `default-preamble.tex`, and do not
  silently blend the two (e.g. don't keep the Madrid footline while
  switching `\usetheme`). `default-preamble.tex` is a convenience default,
  not an enforced contract, and `latex-compile` itself stays agnostic about
  document class/theme -- it just compiles whatever `.tex` exists, so
  nothing here needs to special-case an override at compile time.

## Ask First -- existing deck

If `presentations/<slug>/main.tex` already exists, **do not overwrite it**.
Ask the researcher first, and offer to extend/append new sections instead
of replacing the file outright. If `presentations/<slug>/` (or its
`images/`/`generated/` subfolders) already exists but `main.tex` doesn't,
there's nothing to overwrite yet -- proceed to create it, same as a
brand-new slug.

The same Ask-First rule applies to an individual file inside `generated/`:
if the specific figure you're about to copy already exists there, confirm
with the researcher before overwriting it (e.g. a re-run analysis script
producing a newer version of the same plot) rather than replacing it
silently.

## Figures: AD-7 provenance

When a slide cites a figure sourced from `analysis/outputs/` or an existing
`manuscript/generated/`-style output (i.e. anything a script produced,
rather than a plain static image the researcher hands you directly for
`images/`):

1. Copy or reference the file into `presentations/<slug>/generated/`
   (create the folder if it doesn't exist yet) -- never point
   `\includegraphics{}` at `analysis/outputs/` or `manuscript/generated/`
   directly, since a presentation gets its own copy per the directory
   convention above.
2. Apply the exact one-line AD-7 provenance format from `CLAUDE.md`'s
   "Generated-artifact provenance format" section, in **both** places it
   belongs for a binary figure:
   - a sidecar `<figure-file>.prov` text file next to the copied binary
     (figures are binary -- PNG/PDF/JPG -- so they can't carry a leading
     `%` comment themselves; this is the same convention `manuscript/`
     already uses, not a new one for slides), and
   - a matching `%`-comment line directly above the `\includegraphics{}`
     call in `main.tex` itself, so the provenance is visible right where
     the figure is used, not only in the sidecar.

   Format (identical in both places):
   ```
   % Generated by <script path> @ <git-short-hash> -- do not edit by hand
   ```
   The script/hash identify when the *analysis script* generated the
   figure, not when it was copied into the deck -- carry the source file's
   existing provenance line (from its own leading comment or `.prov`
   sidecar under `analysis/outputs/`/`manuscript/generated/`) forward
   verbatim into both new locations; never fabricate a new hash for the
   copy action itself.
3. **If you can't determine the source script or its git hash** (e.g. the
   researcher just points at a file with no clear generating script, or the
   file predates the current git history), **HALT and ask** rather than
   omitting the provenance line or guessing a plausible-looking one.

A plain static image the researcher hands you for decoration/branding (not
representing an analysis result) goes in `images/` instead and needs no
provenance line -- provenance is for generated results, not arbitrary
images.

## Citations on slides

If a slide needs a `\cite{key}`, the same rule as `CLAUDE.md`'s
Bibliography convention (CAP-1/CAP-2) applies, unchanged -- it is not
relaxed for slides:

- Re-read `manuscript/references.bib` fresh from disk in the same turn,
  immediately before emitting the citation -- never rely on an earlier or
  cached view.
- Never write a `\cite{key}` unless `key` is present in that fresh read;
  never invent, guess, or hand-type a plausible-looking key.
- This is also an **Ask First** point: confirm with the researcher before
  actually citing something on a slide, the same way you'd confirm before
  overwriting an existing deck.
- To wire up citations in a deck that doesn't already have them, add
  `\usepackage[backend=biber]{biblatex}` and
  `\addbibresource{../../manuscript/references.bib}` to the deck's preamble
  (pointing back at the one canonical `references.bib` under `manuscript/`
  -- never copy or duplicate that file into `presentations/<slug>/`), and
  add a `\printbibliography` call on its own frame near the end of the
  deck -- without it, `\cite{key}` markers render but no reference list
  ever appears, which is worse for an audience than having no citations at
  all.

## Compiling: hand off to latex-compile

Once `main.tex` is drafted (or updated), compile it exactly the way
`latex-compile`'s own `SKILL.md` specifies -- do not invoke
`latexmk`/`pdflatex` directly, and do not re-parse or re-summarize compiler
output yourself:

```bash
uv run .claude/skills/latex-compile/latex_compile.py presentations/<slug>/main.tex
```

- **`"status": "ok"`**: a PDF was produced at `presentations/<slug>/main.pdf`.
  Still check `"warnings"` (e.g. an undefined citation) even on success --
  AD-6 requires surfacing it. `latex-compile`'s JSON only tells you the
  document built -- it says nothing about overfull frames, content running
  off a slide, or a figure rendering at the wrong scale. Skim the actual
  PDF (or its text/page count) before telling the researcher the deck is
  done, the same way you'd proofread prose before calling a section
  finished.
- **`"status": "error"`**: relay the structured `{file, line, package,
  message}` entries to the researcher exactly as `latex-compile` returns
  them -- never raw compiler log text, and never invent a fix without
  proposing it to the researcher first (mirrors `latex-compile`'s own "no
  auto-fix without stating the change" rule). An unfamiliar package name in
  an error is checked against Context7 MCP first, falling back to
  `texdoc`/CTAN when Context7 has nothing useful -- existing convention,
  not new to this skill.

**Known interaction, verified during this skill's build -- a brand-new
deck's very first `latex-compile` invocation can report `"status": "error"`
naming an aux file (`main.nav`/`main.toc`, and by the same mechanism
potentially `main.snm`/`main.vrb`) as a missing input file, even though a
PDF was actually produced.** This is a `latexmk`-summary quirk specific to
Beamer output (these files are written and read back within the same first
compile pass, before they exist on disk; `latex-compile` treats any
"missing input file" line in the summary as fatal without checking whether
`latexmk` itself already recovered) -- `manuscript/`'s `article`-class
document never triggers this, so it wasn't visible before this skill added
the first Beamer document to the repo. Recompiling once resolves it (the
files exist on disk for the second run).

If a fresh deck's first compile reports exactly this shape of error
(`package` naming a `.nav`/`.toc`/`.snm`/`.vrb` file with the same basename
as `main.tex`, sitting right next to it -- not a real missing package),
recompile once before relaying it to the researcher as a real error. **If
the second compile reports the same error shape again** (not just the same
file name -- an identical unresolved missing-input error), stop treating it
as the known quirk and relay it to the researcher as a real error per the
normal contract above; the quirk is specifically a one-time first-pass
artifact, never a persistent one. This is **not** something to special-case
by touching `latex-compile` itself -- that skill owns compile-error parsing
unchanged; this is purely operational knowledge for using it with Beamer
decks.

## What this skill never does

- Never invokes `latexmk`/`pdflatex` directly, or reimplements
  compile-error parsing -- that's entirely `latex-compile`'s job.
- Never hand-rolls a Python scaffolding script for frame authoring -- frame
  content is direct `.tex` writing by Claude, same as the manuscript flow.
- Never touches `manuscript/` or its conventions -- a presentation is a
  sibling deliverable, not a manuscript edit.
- Never fabricates slide content, numbers, or citations to fill space.

Canonical spec: `spec-latex-presentation-generation.md` in the harness
planning repo (the sibling planning repo's
`_bmad-output/implementation-artifacts/`).
