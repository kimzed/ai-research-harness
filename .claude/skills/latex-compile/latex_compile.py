# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""latex_compile.py -- `latexmk -cd -pdf` compile wrapper for the
`latex-compile` Claude Code skill (CAP-8, AD-8).

Invokes `latexmk -cd -pdf <tex-file>` (the invocation story 2 confirmed
works from any cwd -- `-cd` makes latexmk change to the tex file's own
directory before compiling) and parses its output into AD-8's structured
report instead of ever surfacing raw `pdflatex`/`latexmk` log text.

Always prints a single JSON object to stdout:
    {"status": "ok"|"error", "errors": [...], "warnings": [...]}

Per-entry shape (both "errors" and "warnings"):
    {"file": str|null, "line": int|null, "package": str|null, "message": str}

`"status": "ok"` implies an empty "errors" list and a produced PDF.
`"warnings"` (e.g. undefined-citation notices) never change "status" away
from "ok" -- AD-6 still requires they be surfaced, just not treated as a
compile failure.

Line-number caveat (story 2's finding): raw `pdflatex` `l.N` pointers are
unreliable (off-by-one-ish, `^^M` artifacts) -- never trusted here. `file`/
`package` for a missing-input error come from latexmk's own summary line
("Missing input file '...' (or dependence on it) from following: ...");
`line` is populated only by scanning the source `.tex` file for a single,
unambiguous `\usepackage`/`\input`/`\includegraphics` occurrence of the
named file -- otherwise it stays `null` rather than propagate a misleading
number. Undefined-citation warnings are the one case latexmk's own summary
*does* give a reliable source line for ("... undefined on input line N"),
so those are trusted directly.

Exit codes mirror lit_search.py's status-driven convention:
  0  "status": "ok"
  1  "status": "error" (compile failed, or a required tool is missing --
     see Ask-First handling below; either way this exits before/without a
     raw traceback)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REQUIRED_TOOLS = ("latexmk", "pdflatex")
LATEXMK_TIMEOUT_SECONDS = 120

MISSING_INPUT_RE = re.compile(r"Missing input file '([^']+)'")
UNDEFINED_CITATION_RE = re.compile(
    r"Citation '([^']+)' on page \d+ undefined on input line (\d+)"
)
GENERIC_LATEX_ERROR_RE = re.compile(r"^! (.+)$", re.MULTILINE)
# Matches a \usepackage/\input/\includegraphics call and captures its brace
# content, so locate_source_line can compare each comma-separated target
# for an *exact* match rather than an unanchored substring (bug #3).
SOURCE_TARGET_RE = re.compile(
    r"\\(?:usepackage|input|includegraphics)(?:\[[^\]]*\])?\{([^}]*)\}"
)


def check_missing_tools() -> list[str]:
    """Return the subset of REQUIRED_TOOLS not found on PATH."""
    return [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]


def run_latexmk(tex_file: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        # -g forces latexmk to always fully rerun every rule, ignoring its
        # own "up to date" cache -- without it, an unchanged source file on
        # a second invocation makes latexmk print "Nothing to do" and skip
        # re-emitting its own undefined-citation summary, which silently
        # produces a stale "warnings": [] false negative (bug #1).
        ["latexmk", "-g", "-cd", "-pdf", "-interaction=nonstopmode", tex_file],
        capture_output=True,
        text=True,
        timeout=LATEXMK_TIMEOUT_SECONDS,
    )


def locate_source_line(tex_path: Path, needle: str) -> int | None:
    """Scan `tex_path` for a single, unambiguous `\\usepackage`/`\\input`/
    `\\includegraphics` occurrence naming `needle` (the missing file's stem,
    extension stripped). A target matches only when it is *exactly* `needle`
    (bare or with any extension) -- never a substring match, so `foo` can't
    spuriously match `foobar.sty` elsewhere in the source. `\\usepackage`'s
    comma-separated multi-package form (`\\usepackage{a,b,c}`) is split and
    each token checked individually. Returns the 1-indexed line number only
    when exactly one line matches -- never a guess among several
    candidates."""
    try:
        lines = tex_path.read_text(errors="replace").splitlines()
    except OSError:
        return None

    matching_lines: set[int] = set()
    for line_no, line in enumerate(lines, start=1):
        for call_match in SOURCE_TARGET_RE.finditer(line):
            targets = [t.strip() for t in call_match.group(1).split(",")]
            for target in targets:
                target_stem = Path(target).stem if target else target
                if target == needle or target_stem == needle:
                    matching_lines.add(line_no)
                    break

    return next(iter(matching_lines)) if len(matching_lines) == 1 else None


def parse_missing_input_errors(log_text: str, file_field: str, tex_path: Path) -> list[dict[str, Any]]:
    """Missing package/input-file errors, sourced from latexmk's own summary
    line rather than the raw pdflatex `l.N` pointer (story 2's finding)."""
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in MISSING_INPUT_RE.finditer(log_text):
        missing_file = match.group(1)
        if missing_file in seen:
            continue
        seen.add(missing_file)
        needle = Path(missing_file).stem
        line = locate_source_line(tex_path, needle)
        errors.append(
            {
                "file": file_field,
                "line": line,
                "package": missing_file,
                "message": (
                    f"latexmk: missing input file '{missing_file}' "
                    "(or a dependency of it) -- see latexmk's own summary "
                    "line, not the raw pdflatex line pointer."
                ),
            }
        )
    return errors


def parse_generic_errors(
    log_text: str, file_field: str, explained_files: set[str] | None = None
) -> list[dict[str, Any]]:
    """Fallback for compile-failure detail not already covered by a
    missing-input match. Collects *every* distinct `! ...` fatal-error line
    (exact-text dedup) rather than just the first, so multiple unrelated
    errors in one log are never masked down to a single report (bug #2a).
    A message that references a file already explained by a missing-input
    error is skipped, so the same root cause isn't reported twice."""
    explained_files = explained_files or set()
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in GENERIC_LATEX_ERROR_RE.finditer(log_text):
        message = match.group(1).strip()
        if message in seen:
            continue
        seen.add(message)
        if any(missing_file in message for missing_file in explained_files):
            continue
        errors.append(
            {
                "file": file_field,
                "line": None,
                "package": None,
                "message": f"latexmk/pdflatex reported: {message}",
            }
        )
    return errors


def parse_undefined_citation_warnings(log_text: str, file_field: str) -> list[dict[str, Any]]:
    """Undefined-citation notices (AD-6): the one case latexmk's summary
    gives a directly trustworthy source line for, so it's used as-is."""
    warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in UNDEFINED_CITATION_RE.finditer(log_text):
        key, line = match.group(1), int(match.group(2))
        # Dedup on (key, line), not key alone -- the same undefined key
        # cited on two different source lines is two real occurrences, not
        # one (bug #6).
        if (key, line) in seen:
            continue
        seen.add((key, line))
        warnings.append(
            {
                "file": file_field,
                "line": line,
                "package": None,
                "message": (
                    f"Undefined citation '{key}' -- key does not resolve "
                    "against references.bib (AD-6 still applies to warnings, "
                    "not just hard errors)."
                ),
            }
        )
    return warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "latex-compile: latexmk -cd -pdf wrapper that reports a "
            "structured {status, errors, warnings} JSON object, never raw "
            "compiler log text."
        )
    )
    parser.add_argument(
        "tex_file",
        help="Path to the .tex file to compile, e.g. manuscript/main.tex.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    tex_path = Path(args.tex_file)

    if not tex_path.is_file():
        # is_file(), not exists() -- a directory argument also satisfies
        # exists() and would otherwise sail past this guard and fail
        # confusingly later, e.g. inside with_suffix() or the latexmk call
        # itself (bug #7).
        print(
            json.dumps(
                {
                    "status": "error",
                    "errors": [
                        {
                            "file": args.tex_file,
                            "line": None,
                            "package": None,
                            "message": f"No such file: {args.tex_file}",
                        }
                    ],
                    "warnings": [],
                },
                indent=2,
            )
        )
        return 1

    missing_tools = check_missing_tools()
    if missing_tools:
        # Ask-First (SPEC.md): report the missing tool and stop before
        # compiling -- never guess an install path, never crash/traceback.
        print(
            json.dumps(
                {
                    "status": "error",
                    "errors": [
                        {
                            "file": None,
                            "line": None,
                            "package": None,
                            "message": (
                                "Required tool(s) not found on PATH: "
                                f"{', '.join(missing_tools)}. Compile was not "
                                "attempted. Ask the researcher how to proceed "
                                "(e.g. confirm the TeX distribution is "
                                "installed) rather than guessing an install "
                                "path."
                            ),
                        }
                    ],
                    "warnings": [],
                },
                indent=2,
            )
        )
        return 1

    try:
        result = run_latexmk(args.tex_file)
    except subprocess.TimeoutExpired:
        # A pathological input (infinite macro loop, a prompt that slips
        # past -interaction=nonstopmode) must not block the script
        # indefinitely with no recovery (bug #4).
        print(
            json.dumps(
                {
                    "status": "error",
                    "errors": [
                        {
                            "file": args.tex_file,
                            "line": None,
                            "package": None,
                            "message": (
                                "latexmk did not finish within "
                                f"{LATEXMK_TIMEOUT_SECONDS}s and was aborted -- "
                                "possible infinite macro loop or a prompt that "
                                "slipped past -interaction=nonstopmode. Inspect "
                                "the .tex source directly rather than "
                                "re-running blindly."
                            ),
                        }
                    ],
                    "warnings": [],
                },
                indent=2,
            )
        )
        return 1

    log_path = tex_path.with_suffix(".log")
    log_text = ""
    if log_path.exists():
        try:
            log_text = log_path.read_text(errors="replace")
        except OSError:
            log_text = ""
    combined = f"{result.stdout or ''}\n{result.stderr or ''}\n{log_text}"

    missing_input_errors = parse_missing_input_errors(combined, args.tex_file, tex_path)
    warnings = parse_undefined_citation_warnings(combined, args.tex_file)

    pdf_path = tex_path.with_suffix(".pdf")
    compile_failed = result.returncode != 0 or not pdf_path.exists()

    # Always scan for generic errors not already explained by a missing-input
    # match when the compile failed -- never skip the scan just because a
    # missing-input error was already found, so an unrelated second error in
    # the same compile isn't silently dropped (bug #2b).
    explained_files = {e["package"] for e in missing_input_errors if e["package"]}
    generic_errors = (
        parse_generic_errors(combined, args.tex_file, explained_files)
        if compile_failed
        else []
    )
    errors = missing_input_errors + generic_errors

    if compile_failed and not errors:
        errors = [
            {
                "file": args.tex_file,
                "line": None,
                "package": None,
                "message": (
                    "latexmk reported a non-zero exit or produced no PDF; "
                    "see the compile's own log for detail (not reproduced "
                    "here per AD-8)."
                ),
            }
        ]

    status = "error" if (compile_failed or errors) else "ok"
    print(json.dumps({"status": status, "errors": errors, "warnings": warnings}, indent=2))
    return 1 if status == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
