---
name: setup
description: Check which of this repo's machine prerequisites are installed - Claude Code, VS Code + LaTeX Workshop, TeX Live, Zotero + Better BibTeX, Obsidian - report what is missing, and install the gaps with the researcher's approval, following SETUP.md. Use when the researcher says "set up this machine", "install the prerequisites", "is everything installed", "nothing compiles", or when a fresh clone of this template has not been set up yet.
---

# setup

A methodology skill, not an API wrapper -- there is no `setup.py`. It probes
the machine, reports gaps, and runs install commands **only** on the
researcher's explicit approval.

`SETUP.md` at the repo root owns the install instructions and the
per-machine record. This skill does not restate them: read `SETUP.md` for
the command for any gap you find, and never invent one.

## Never

- **Never run an install command without the researcher's explicit approval
  for that specific command.** Everything in `SETUP.md` §1-4 is `sudo` or
  writes outside the repo. Show the command, say what it installs, wait.
- **Never invent an install command, repository, or version.** If `SETUP.md`
  has no command for something, say so and ask.
- **Never treat `SETUP.md`'s "Installed on this machine" lines as proof.**
  They are a record written on one machine at one date, and this template
  gets cloned onto others. Probe first; trust the probe, not the note.
- **Never install anything the researcher did not ask about while you happen
  to be in the area.**

## Phase 1 -- Probe

Run these read-only checks and build a present/missing list. A non-zero exit
or "command not found" is a gap, not an error to report as a failure.

```bash
claude --version                                    # 0. Claude Code
code --version                                      # 1. VS Code
code --list-extensions | grep -i latex-workshop     # 1. LaTeX Workshop
latexmk -v && pdflatex --version && biber --version # 2. TeX Live
command -v obsidian                                 # 4. Obsidian
```

For **3. Zotero + Better BibTeX**, do not curl the endpoints directly --
every Zotero read goes through `zotero-code-execution` (AD-8):

```bash
# Zotero desktop only -- hits the Connector endpoint, says nothing about BBT.
uv run .claude/skills/zotero-code-execution/zotero_file.py --check-target

# Zotero AND Better BibTeX -- this one goes through BBT's JSON-RPC search,
# so an "ok" here is the only probe that confirms BBT is actually answering.
uv run .claude/skills/zotero-code-execution/zotero_file.py \
  --check-duplicate '{"doi":"10.1038/nature14539","title":null,"arxiv_id":null}'
```

The two probes fail differently and that difference is the diagnosis:
`--check-target` succeeding while `--check-duplicate` fails points at Better
BibTeX, not at Zotero. Read `zotero-code-execution`'s SKILL.md for what each
status means -- `zotero_not_running` (desktop closed) is a different finding
from `zotero_api_error`, and **neither is an install gap on its own**. A
closed Zotero is a closed Zotero; do not report it as missing software or
offer to reinstall it.

The `--check-duplicate` probe is read-only and writes nothing. Its result --
whether that DOI is in the library -- is irrelevant here; only the `status`
matters.

Two things no probe can confirm, so ask instead:

- Whether Better BibTeX's **"Keep updated"** export is pointed at
  `manuscript/references.bib` (`SETUP.md` §3, step 3). A non-empty
  `references.bib` is evidence it worked once, not that it is still live.
- Whether the researcher wants VS Code and Obsidian at all -- both are
  human-facing conveniences, not things Claude Code needs. A researcher who
  uses a different editor is not missing a prerequisite.

## Phase 2 -- Report

Give one list: what is present (with the version the probe returned), what
is missing, and what could not be determined. Name the `SETUP.md` section
for each gap.

State plainly that `SETUP.md` §0-4 are Linux (Ubuntu/Debian) commands and
that Windows is an explicit non-goal -- if this is not such a machine, the
commands there do not apply and the researcher should say what they are on
rather than have you guess an equivalent.

## Phase 3 -- Install, one gap at a time

For each gap the researcher wants closed:

1. Show the exact command from `SETUP.md`, and what it installs.
2. Wait for approval of that command. Approval of one is not approval of
   the next.
3. Run it, then re-run that item's probe from Phase 1 to confirm.
4. If it fails, report the failure in the tool's own words. Never retry
   silently, and never substitute a different install method.

`SETUP.md` offers non-snap alternatives for VS Code and Obsidian, and warns
against `texlive-full` unless a focused install repeatedly hits missing
packages. Surface the choice rather than picking for the researcher.

Zotero and Better BibTeX (§3) end in **manual GUI steps** -- installing the
`.xpi` through Zotero's Add-ons dialog, and configuring the "Keep updated"
export. Those cannot be scripted: walk the researcher through them and
confirm with `--check-target` afterwards.

## Phase 4 -- Record and hand off

Each `SETUP.md` section carries a **Verified on this machine** line. Replace
the `_not-yet-verified_` marker with what the probe actually returned --
version and path -- plus today's date, for every item you confirmed this
session. Write only what a probe reported: never copy a version out of
`SETUP.md`'s own prose, and never mark something verified because the install
command exited 0 without re-probing it.

Leave the marker in place for anything still missing, declined, or
unconfirmable. An honest `_not-yet-verified_` beats a line claiming software
this machine does not have -- that inversion is exactly why these lines are
reset in a fresh clone.

Then say what remains open, and hand off:

- If the machine is ready and `citation-contract.md` does not exist at the
  repo root, the next step is the **`onboarding`** skill -- this instance
  has the software but has not yet been told what the project is or which
  citation contract it uses. Offer it; do not start it unasked.
- If anything is still missing, say which flows stay blocked: no TeX Live
  means `latex-compile` cannot build, and no Zotero/BBT means every
  `zotero-code-execution` mode and the `references.bib` feed are unavailable.
