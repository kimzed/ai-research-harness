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

MISSING_INPUT_RE = re.compile(r"Missing input file '([^']+)'")
UNDEFINED_CITATION_RE = re.compile(
    r"Citation '([^']+)' on page \d+ undefined on input line (\d+)"
)
GENERIC_LATEX_ERROR_RE = re.compile(r"^! (.+)$", re.MULTILINE)


def check_missing_tools() -> list[str]:
    """Return the subset of REQUIRED_TOOLS not found on PATH."""
    return [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]


def run_latexmk(tex_file: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["latexmk", "-cd", "-pdf", "-interaction=nonstopmode", tex_file],
        capture_output=True,
        text=True,
    )


def locate_source_line(tex_path: Path, needle: str) -> int | None:
    """Scan `tex_path` for a single, unambiguous `\\usepackage`/`\\input`/
    `\\includegraphics` occurrence naming `needle` (the missing file's stem,
    extension stripped). Returns the 1-indexed line number only when exactly
    one line matches -- never a guess among several candidates."""
    try:
        lines = tex_path.read_text().splitlines()
    except OSError:
        return None

    pattern = re.compile(
        r"\\(?:usepackage(?:\[[^\]]*\])?|input|includegraphics(?:\[[^\]]*\])?)"
        r"\{[^}]*" + re.escape(needle) + r"[^}]*\}"
    )
    matches = [line_no + 1 for line_no, line in enumerate(lines) if pattern.search(line)]
    return matches[0] if len(matches) == 1 else None


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


def parse_generic_errors(log_text: str, file_field: str) -> list[dict[str, Any]]:
    """Fallback for a failed compile that isn't a recognized missing-input
    error -- still returns one structured entry, never a raw log dump."""
    match = GENERIC_LATEX_ERROR_RE.search(log_text)
    message = (
        f"latexmk/pdflatex reported: {match.group(1).strip()}"
        if match
        else "latexmk reported a non-zero exit or produced no PDF; see the "
        "compile's own log for detail (not reproduced here per AD-8)."
    )
    return [{"file": file_field, "line": None, "package": None, "message": message}]


def parse_undefined_citation_warnings(log_text: str, file_field: str) -> list[dict[str, Any]]:
    """Undefined-citation notices (AD-6): the one case latexmk's summary
    gives a directly trustworthy source line for, so it's used as-is."""
    warnings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in UNDEFINED_CITATION_RE.finditer(log_text):
        key, line = match.group(1), int(match.group(2))
        if key in seen:
            continue
        seen.add(key)
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

    if not tex_path.exists():
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

    result = run_latexmk(args.tex_file)

    log_path = tex_path.with_suffix(".log")
    log_text = ""
    if log_path.exists():
        try:
            log_text = log_path.read_text(errors="replace")
        except OSError:
            log_text = ""
    combined = f"{result.stdout or ''}\n{result.stderr or ''}\n{log_text}"

    errors = parse_missing_input_errors(combined, args.tex_file, tex_path)
    warnings = parse_undefined_citation_warnings(combined, args.tex_file)

    pdf_path = tex_path.with_suffix(".pdf")
    compile_failed = result.returncode != 0 or not pdf_path.exists()

    if compile_failed and not errors:
        errors = parse_generic_errors(combined, args.tex_file)

    status = "error" if (compile_failed or errors) else "ok"
    print(json.dumps({"status": status, "errors": errors, "warnings": warnings}, indent=2))
    return 1 if status == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
