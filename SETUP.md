# Setup

Machine setup for working in this repo — four pieces: **Claude Code** (the agent doing the work), **VS Code + LaTeX Workshop** (human-facing editor/previewer), **TeX Live** (compiles `manuscript/*.tex`), and **Zotero + Better BibTeX** (reference library + the `manuscript/references.bib` feed). A fifth piece, the paper-metadata API (Semantic Scholar/OpenAlex), needs no install — see the `ai-research-harness-specs` repo's `research-tooling-overview.md` §3.

Per the specs repo's `SPEC.md` Assumptions, **v1 targets Cedric's Linux machine only** — all steps below are Linux (Ubuntu/Debian) commands. The pilot's Windows setup is an explicit non-goal for now, revisited as a follow-on once the Linux path is proven.

---

## 0. Claude Code

The core of the harness — everything else here exists to support it (LaTeX/Zotero are things *it* drives, or that a human uses alongside it). Install before anything else.

**Install (Linux):**
```bash
curl -fsSL https://claude.ai/install.sh | bash
```
Alternative via npm: `npm install -g @anthropic-ai/claude-code`

**Installed on this machine:** `claude` v2.1.258, at `~/.local/bin/claude`.

---

## 1. VS Code + LaTeX Workshop

A free, local, human-facing editor and PDF previewer for the same git-tracked `.tex` files Claude Code writes to — not a capability Claude Code itself needs, just a way to view/edit manually. Recommended over TeXstudio/LyX.

**Install VS Code (Linux):**
```bash
sudo snap install code --classic
```
Alternative (apt, via Microsoft's repo) — see https://code.visualstudio.com/docs/setup/linux for the `.deb`/repo method if you'd rather avoid snap.

**Install the LaTeX Workshop extension:**
```bash
code --install-extension James-Yu.latex-workshop
```

**Installed on this machine (2026-09-02):** VS Code via snap (`code` → `/snap/bin/code`), extension `james-yu.latex-workshop@10.18.0`.

**Usage:**
1. Open `manuscript/main.tex` in VS Code (open the repo folder, not just the file, so LaTeX Workshop picks up the right project root) — it adds a build button (▶) to the editor toolbar.
2. Build with `latexmk` (default recipe) — triggers on save (`latex-workshop.latex.autoBuild.run` default is `onFileChange`), or manually via the build button / `Ctrl+Alt+B`.
3. View the compiled PDF in a side panel via `Ctrl+Alt+V` (SyncTeX-linked — click in the PDF to jump to the matching source line, and vice versa).

---

## 2. TeX Live

Required by both CAP-8's build loop (Claude Code compiling) and LaTeX Workshop (human preview) — same distribution, shared dependency.

**Install (Linux/Ubuntu-Debian) — a focused subset, not `texlive-full`:**
```bash
sudo apt update && sudo apt install -y \
  texlive-latex-base texlive-latex-recommended texlive-latex-extra \
  texlive-fonts-recommended texlive-bibtex-extra latexmk biber
```
Covers compiling (`pdflatex`), bibliography/biblatex (`biber`), and build automation (`latexmk`). Use `sudo apt install texlive-full` instead only if you hit a missing-package wall repeatedly (multi-GB).

**Installed on this machine (2026-09-02):** verified working —
- `latexmk` 4.83
- `pdfTeX` 3.141592653 (TeX Live 2023/Debian)
- `biber` 2.19

---

## 3. Zotero + Better BibTeX

Reference library access + the bibliography bridge that feeds `manuscript/references.bib`. Chosen over EndNote — open formats, real API, and Better BibTeX (BBT) makes the bibliography → paper handoff nearly free. Full rationale in the specs repo's `research-tooling-overview.md` §2.

**Install Zotero (Linux):** no apt/snap package from Zotero itself — official method is the tarball:
```bash
wget -O zotero-install.tar.bz2 "https://www.zotero.org/download/client/dl?platform=linux-x86_64"
tar -xjf zotero-install.tar.bz2 -C /opt/
sudo /opt/Zotero_linux-x86_64/set_launcher_icon
/opt/Zotero_linux-x86_64/zotero  # first run
```

**Installed on this machine (2026-09-02):** Zotero **10.0.1**, at `/opt/zotero` — upgraded from 6.0.35 alongside story 3, since Better BibTeX's current release requires Zotero ≥8.0.1. This also satisfies the `pyzotero`/local-API version requirement (needs Zotero 7+); the Option A vs Option B read-path choice itself remains open (see below).

**Install Better BibTeX:**
1. Download the latest `.xpi` from https://github.com/retorquere/zotero-better-bibtex/releases
2. In Zotero: `Tools → Add-ons → ⚙ (gear icon) → Install Add-on From File…` → select the `.xpi`.
3. Right-click your library/collection → *Export* → translator **Better BibLaTeX** (not plain "Better BibTeX" — its "Keep updated" checkbox is missing/bugged in current BBT releases, and `manuscript/main.tex` uses `biblatex`, which expects BibLaTeX-flavored fields like `date`/`journaltitle` anyway), tick **Keep updated** → point the export at `manuscript/references.bib` in this repo.

**Installed and configured on this machine (2026-09-02):** confirmed working as part of story 3 (Bibliography read + citation emission) — `manuscript/references.bib` now carries a live Better-BibTeX "Keep updated" export (one entry so far, key `vanherzeleMonitoringToolProvision2003a`), and a fresh read of it round-tripped successfully through a real `\cite{key}` and a clean `latexmk`/`biber` compile.

---

## Open items

- **Zotero read-path choice** — `zotero-mcp` (Option A, batteries-included) vs `pyzotero` (Option B, no key) — undecided; both pair with Better BibTeX regardless. Zotero is now on 10.0.1, so neither option is version-blocked any more.
- **Windows/pilot setup** — deferred by design; not covered here.
