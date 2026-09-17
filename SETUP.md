# Setup

Machine setup for working in this repo — five pieces: **Claude Code** (the agent doing the work), **VS Code + LaTeX Workshop** (human-facing editor/previewer), **TeX Live** (compiles `manuscript/*.tex`), **Zotero + Better BibTeX** (reference library + the `manuscript/references.bib` feed), and **Obsidian** (human-facing viewer/editor for the `knowledge-base/` vault). A sixth piece, the paper-metadata API (Semantic Scholar/OpenAlex), needs no install — see the `ai-research-harness-specs` repo's `research-tooling-overview.md` §3. A seventh piece, the **Sci-Hub MCP Server** (§5), is optional and personal — it is never part of a fresh clone's required setup; install it only if and when the researcher wants `scihub-pdf-downloader` to work.

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

## 5. Sci-Hub MCP Server (optional, personal — not part of the required five)

Lets `scihub-pdf-downloader` fetch a paper's PDF directly by DOI/title/keyword instead of falling back to Chrome-MCP mirror scraping. Unlike §0-4, this is never installed as part of setting up a fresh clone: it is the researcher's own personal, per-machine config (`~/.mcp.json`, outside this repo) — install it only if and when you actually want that skill to use it. See `ai-research-harness-specs`' `spec-scihub-downloader-mcp-wiring.md` for why.

**Install (Linux):**
```bash
pip install "sci-hub-mcp-server" "mcp<2"
```
Requires Python 3.11+. The `mcp<2` pin is required as of package v0.1.1 (2026-09-17) — an unpinned install pulls `mcp` 2.x, which renamed `mcp.server.fastmcp.FastMCP`, so the server fails to import without it.

**Known packaging bug (still present as of v0.1.1) — check first, since a later release may have fixed it:**
```bash
python -c "import sci_hub_mcp_server.sci_hub_server" && echo OK
```
If that fails with `ModuleNotFoundError: No module named 'sci_hub_mcp_server'`, the installed package ships its code under a hyphenated directory name (`sci-hub-mcp-server`) that its own `__init__.py` can't import under that name. Work around it (safe to re-run, including after an upgrade):
```bash
SITE=$(python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
rm -rf "$SITE/sci_hub_mcp_server"
cp -r "$SITE/sci-hub-mcp-server" "$SITE/sci_hub_mcp_server"
```
Re-run the import check above to confirm it now prints `OK`.

**Add to your personal `~/.mcp.json`** (never commit this file or add this entry to the repo/template):
```json
{
  "mcpServers": {
    "scihub": {
      "command": "python",
      "args": ["-m", "sci_hub_mcp_server.sci_hub_server"]
    }
  }
}
```
Use whichever `python` has the package installed. Restart Claude Code after editing `~/.mcp.json`. Package license is GPL-3.0-or-later. See `scihub-pdf-downloader/SKILL.md` for the confirmed tool surface and usage.

**Verified on this machine:** _not-yet-verified_ — and not expected to be, unless the researcher opted into this.

---

## Open items

- **Zotero read-path choice** — `zotero-mcp` (Option A, batteries-included) vs `pyzotero` (Option B, no key) — undecided; both pair with Better BibTeX regardless. Both need Zotero 7+, and Better BibTeX's current release needs Zotero ≥8.0.1, so §3's install covers either choice.
- **Windows/pilot setup** — deferred by design; not covered here.
