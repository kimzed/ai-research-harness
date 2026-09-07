---
name: latex-compile
description: Compile a LaTeX document with latexmk and get back a structured, machine-parseable report of compile errors and warnings -- never raw pdflatex/latexmk log text. Use whenever the researcher wants a document (re)compiled, or after Claude Code edits `.tex` source and needs to confirm it still builds -- "compile main.tex", "does this still build", "check for undefined citations".
---

# latex-compile

Thin wrapper over `latexmk -cd -pdf` (CAP-8, AD-8). This is the only way
Claude Code invokes the LaTeX build loop -- never call `latexmk`/`pdflatex`
directly from conversation and never paste raw compiler log text back to
the researcher.

**Never consume raw compiler output directly.** Always invoke
`latex_compile.py` and read its parsed JSON output.

## Invocation

```bash
uv run .claude/skills/latex-compile/latex_compile.py manuscript/main.tex
```

One positional argument: the path to the `.tex` file to compile (relative
to whatever cwd the script is invoked from -- the confirmed convention is
to run from the repo root, e.g. `manuscript/main.tex`). The script runs
`latexmk -cd -pdf <tex-file>` itself -- `-cd` makes latexmk change to the
tex file's own directory before compiling, which is what makes this
invocation work identically from the repo root or from inside
`manuscript/` (story 2 confirmed both).

v1 targets the confirmed TeX Live `-pdf` (pdflatex) path only -- no
`xelatex`/`lualatex` engine negotiation.

## Output contract

The script always prints exactly one JSON object to stdout, never raw
`pdflatex`/`latexmk` text:

```json
{
  "status": "ok" | "error",
  "errors": [
    {"file": "manuscript/main.tex", "line": 9, "package": "foo.sty", "message": "..."}
  ],
  "warnings": [
    {"file": "manuscript/main.tex", "line": 12, "package": null, "message": "Undefined citation '...' -- ..."}
  ]
}
```

- **`"status": "ok"`** -- implies an empty `"errors"` list and that a PDF
  was produced. `"warnings"` may still be non-empty (e.g. an undefined
  citation) -- a warning never fails the compile, but AD-6 requires it be
  surfaced, so always check `"warnings"` even when `"status"` is `"ok"`.
- **`"status": "error"`** -- the compile failed (or a required tool was
  missing -- see Ask-First below). `"errors"` is non-empty; act on it
  rather than re-running blindly.
- Every entry in both `"errors"` and `"warnings"` uses the same shape:
  `{"file", "line", "package", "message"}`. `"package"` is populated for a
  missing-package/missing-input error (the exact `.sty`/file name) and
  `null` otherwise (e.g. for a citation warning). `"file"` can also be
  `null` -- specifically the Ask-First missing-tool case below, where no
  `.tex` file was ever reached.

**Line-number caveat (load-bearing, from story 2's spike):** raw
`pdflatex`'s `l.N` error pointer is unreliable for a missing-input error --
it points at wherever `pdflatex`'s error-recovery loop happened to stop
consuming input, not the actual offending line, and carries a cosmetic
`^^M` artifact. This script never surfaces that raw pointer. Instead:
- `"file"`/`"package"` for a missing-input error come from `latexmk`'s own
  summary line (`Missing input file '...' (or dependence on it) from
  following: ...`), which reliably names the missing file.
- `"line"` is populated only when the source `.tex` file has exactly one
  unambiguous `\usepackage`/`\input`/`\includegraphics` occurrence of the
  named file -- otherwise it is `null`. A `null` line means "look for it
  yourself", never a fabricated guess.
- Undefined-citation warnings are the one case `latexmk`'s summary *does*
  give a directly trustworthy source line for (`Citation '...' undefined
  on input line N`), so that line number is used as-is.

Exit codes mirror `lit_search.py`'s status-driven convention: `0` for
`"status": "ok"`, `1` for `"status": "error"`.

## Ask First -- missing tool

If `latexmk` or `pdflatex` isn't on `PATH`, the script reports this as a
`"status": "error"` entry and exits **before** attempting to compile --
never a crash/traceback, never a guessed install path. Relay that message
to the researcher and ask how to proceed (e.g. confirm the TeX
distribution is installed) rather than guessing.

## Rules

- Never auto-fix a compile error without first stating the proposed change
  to the researcher (SM-C1) -- this skill only reports; it never edits
  `.tex` source itself.
- A missing package named by an unfamiliar name is checked against
  Context7 MCP first, falling back to `texdoc`/CTAN directly when Context7
  has nothing useful (Consistency Conventions, ARCHITECTURE-SPINE.md) --
  not this skill's job to look it up, just to name it precisely enough to
  look up.
- For the AD-7 convention on *how* generated numbers/tables/figures get
  inserted into `.tex` source in the first place (`\input{}`/
  `\includegraphics{}`, provenance comment format, `manuscript/generated/`
  ownership) -- see `.claude/CLAUDE.md`'s "Generated-artifact provenance
  format (AD-7)" section. Not duplicated here; this skill only compiles
  whatever source already exists.
