# Setup

Machine setup for working in this repo — five pieces: **Claude Code** (the agent doing the work), **VS Code + LaTeX Workshop** (human-facing editor/previewer), **TeX Live** (compiles `manuscript/*.tex`), **Zotero + Better BibTeX** (reference library + the `manuscript/references.bib` feed), and **Obsidian** (human-facing viewer/editor for the `knowledge-base/` vault). A sixth piece, the paper-metadata API (Semantic Scholar/OpenAlex), needs no install — see the `ai-research-harness-specs` repo's `research-tooling-overview.md` §3.

**All steps below are Linux (Ubuntu/Debian) commands.** Windows is an explicit non-goal for v1 (see the specs repo's `SPEC.md` Assumptions), revisited once the Linux path is proven. On any other platform, none of the commands below apply — say what you are on rather than substituting an equivalent.

Each section below carries a **Verified on this machine** line. A fresh clone
ships them all as `_not-yet-verified_` — that is the reset state, not an
oversight. The `setup` skill (`.claude/skills/setup/`) probes what is actually
present and fills them in; never treat a line here as evidence that something
is installed, because this file travels between machines.

---

## 0. Claude Code

The core of the harness — everything else here exists to support it (LaTeX/Zotero are things *it* drives, or that a human uses alongside it). Install before anything else.

**Install (Linux):**
```bash
curl -fsSL https://claude.ai/install.sh | bash
```
Alternative via npm: `npm install -g @anthropic-ai/claude-code`

**Verified on this machine:** _not-yet-verified_

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

**Verified on this machine:** _not-yet-verified_

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

**Verified on this machine:** _not-yet-verified_

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

**Verified on this machine:** _not-yet-verified_

**Install Better BibTeX:**
1. Download the latest `.xpi` from https://github.com/retorquere/zotero-better-bibtex/releases
2. In Zotero: `Tools → Add-ons → ⚙ (gear icon) → Install Add-on From File…` → select the `.xpi`.
3. Right-click your library/collection → *Export* → translator **Better BibLaTeX** (not plain "Better BibTeX" — its "Keep updated" checkbox is missing/bugged in current BBT releases, and `manuscript/main.tex` uses `biblatex`, which expects BibLaTeX-flavored fields like `date`/`journaltitle` anyway), tick **Keep updated** → point the export at `manuscript/references.bib` in this repo.

**Verified on this machine:** _not-yet-verified_ — confirm the "Keep updated" export is
actually pointed at `manuscript/references.bib`, not just that Better BibTeX is
installed.

---

## 4. Obsidian

Free, local, human-facing viewer/editor for `knowledge-base/` — the vault Claude Code writes findings/concepts/article notes into (see `CLAUDE.md`'s "Obsidian research knowledge base" section). Not a capability Claude Code itself needs; it just reads/writes the same plain-markdown files directly, same relationship as VS Code + LaTeX Workshop has to `manuscript/`.

**Install (Linux):**
```bash
sudo snap install obsidian --classic
```
Alternative (AppImage, no install) — download from https://obsidian.md/download and run directly if you'd rather avoid snap.

**Verified on this machine:** _not-yet-verified_

**Usage:**
1. Launch Obsidian, choose "Open folder as vault", point it at `knowledge-base/` in this repo (not the repo root).
2. The vault starts empty by design — no folder structure is imposed upfront; how notes get organized emerges from actual use (see `CLAUDE.md`'s "Obsidian research knowledge base" section). `[[wikilinks]]` between notes render as clickable links and populate the graph view automatically, no configuration needed.

---

## Open items

- **Zotero read-path choice** — `zotero-mcp` (Option A, batteries-included) vs `pyzotero` (Option B, no key) — undecided; both pair with Better BibTeX regardless. Both need Zotero 7+, and Better BibTeX's current release needs Zotero ≥8.0.1, so §3's install covers either choice.
- **Windows/pilot setup** — deferred by design; not covered here.
