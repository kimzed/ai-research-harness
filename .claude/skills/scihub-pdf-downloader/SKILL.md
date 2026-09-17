---
name: scihub-pdf-downloader
description: >
  Use when the user wants to download a scientific article PDF via Sci-Hub.
  The user may provide a DOI, article title, or keywords. This skill first
  checks whether the paper is already filed in Zotero with an attached PDF
  (via zotero-code-execution), then downloads it via Chrome MCP mirror
  scraping (default -- no extra install, works for any researcher) or,
  optionally, the Debvex Sci-Hub MCP server (only useful if this network can
  actually reach Sci-Hub already, or the researcher has a working
  system-wide VPN/proxy), and offers to file the result into Zotero
  afterward.
---

# Sci-Hub PDF Downloader

You are a research assistant that helps users download scientific articles
from Sci-Hub. Follow these steps precisely.

## Step 0: Parse the Request, Then Check Zotero First

Extract the article identifier from the context of the session or ask the
user for it. This could be:
- A DOI (e.g., `10.1038/nature09492` or `https://doi.org/10.1038/nature09492`
  -- strip the `https://doi.org/` prefix before passing it to any
  `zotero-code-execution` or Sci-Hub call; both expect the bare DOI)
- An article title (e.g., "Attention Is All You Need")
- Keywords for a search (e.g., "reinforcement learning survey 2023")
- An arXiv id only, with no DOI -- Sci-Hub and CrossRef mostly don't cover
  arXiv preprints, so neither download path below reliably finds these.
  Point the researcher to arxiv.org directly, or ask whether the paper has
  since acquired a DOI (e.g. via journal publication).

**Before downloading anything, always check whether this paper is already
filed in Zotero with a usable PDF attached** -- reuse it instead of
re-downloading:

- **If a DOI is already known**, run `--get-content` directly (resolves live,
  and returns the best available content in one call):
  ```bash
  uv run .claude/skills/zotero-code-execution/zotero_file.py \
    --get-content '{"doi":"10.xxxx/yyyy","title":null,"arxiv_id":null}'
  ```
  - `"found":true` with `"source":"fulltext_index"` or `"source":"local_pdf"`
    -- the paper is already in the library with usable content. Report the
    cached text or the `"local_path"` to the researcher and **stop -- do not
    call Sci-Hub.**
  - `"found":true` with `"source":"abstract"` only, `"found":false`, or
    `"status":"error","reason":"no_content_available"` -- no usable PDF is on
    file yet; continue to Step 1.
  - Any other `"status":"error"` (a different `reason`, or none) -- do not
    guess; report the error to the researcher and stop rather than
    continuing to Step 1 as if the paper simply weren't filed.
- **If no DOI is known yet** (title/keyword only), run `--check-duplicate`
  with whatever identifier fields you have (title at minimum):
  ```bash
  uv run .claude/skills/zotero-code-execution/zotero_file.py \
    --check-duplicate '{"doi":null,"title":"...","arxiv_id":null}'
  ```
  - `"duplicate_found":true` with a `"doi_exact"`/`"arxiv"` match -- re-run
    `--get-content` using that match's DOI/arXiv id as above, and stop if it
    turns up usable content.
  - `"duplicate_found":true` with only a `"title_contains"` match -- this is
    a heuristic, not a confirmed identity; mention it to the researcher, and
    only skip Sci-Hub if they confirm it's the same paper (then check its
    content via `--get-content` as above).
  - `"duplicate_found":false` -- not in the library yet; continue to Step 1.
  - Any other match type, or a malformed/unexpected response -- treat it
    conservatively: tell the researcher what was returned and confirm how to
    proceed before continuing, rather than assuming it means no duplicate.

This is a reuse check, not a filing operation -- no `--researcher-confirmed`,
no write, nothing else from `zotero-code-execution` is invoked here.

## Step 1: Locate and Download via Chrome MCP (default path)

This is the default download path -- no install, no MCP server config,
works for any researcher regardless of network/VPN situation (beyond what
Chrome itself can already reach). Use this first unless the researcher has
already confirmed the Debvex MCP server (Step 2) is connected and working
for them.

1. **This list is a living cache, not a fixed reference -- curate it as you
   go.** Sci-Hub mirrors die, come back, and get blocked constantly, so
   treat every attempt as new evidence about the list below, not just a
   means to a download:
   - Try mirrors in the order listed (best-known-status first).
   - **Whenever a mirror's actual behavior disagrees with what's noted
     next to it** -- a "confirmed working" one is now dead, a "dead" one
     now works, or you're testing one marked "not re-verified" -- use the
     `Edit` tool to update that mirror's line in this file with what you
     observed and today's date, before continuing. Don't wait for the
     whole list to fail to correct one stale entry.
   - Reorder the list itself when your update changes the picture (move a
     newly-dead mirror down, a newly-working one up) so the next session
     tries the best candidate first.
   - If every mirror below fails, tell the researcher the whole list is
     likely stale, suggest a fresh web search for current Sci-Hub mirrors,
     and add any newly-discovered working mirror to this list.

   Status as last manually verified 2026-09-17 (see "Troubleshooting:
   network blocking" below for what "blocked" meant here):
   - `https://sci-hub.ru/` -- reachable with a VPN; blocked by ISP-level DNS
     without one. Try this one first if a VPN is available.
   - `https://sci-hub.ee/` -- not re-verified this round.
   - `https://sci-hub.vg/` -- not re-verified this round.
   - `https://sci-hub.se/` -- dead even with a VPN as of the date above,
     not just blocked -- likely retired. Kept last; drop entirely once a
     second re-check confirms it's still dead.
   - `https://sci-hub.st/` -- same as `.se`: dead even with a VPN. Kept
     last for the same reason.

2. For each candidate mirror, pausing briefly between attempts rather than
   hammering the list in rapid succession:
   - Use Chrome MCP to navigate to the page.
   - Read the page to check if it loaded correctly (look for the Sci-Hub
     search bar).
   - If the page shows a CAPTCHA, a blocked message, fails to load, or loads
     without CAPTCHA/block but the expected search bar isn't there (layout
     changed, redirected elsewhere, etc.), treat it as a failed mirror and
     try the next one.

3. Once you find a working mirror:
   - Find the search input field (usually an `<input>` with `name="request"`
     or `id="request"`).
   - Enter whatever identifier you have -- the DOI if known, otherwise the
     raw title text (Step 0 doesn't resolve a title to a DOI; that CrossRef
     resolution only happens in Step 2's MCP-server path).
   - Click the submit button (often labeled "Open" or with a magnifying
     glass icon).
   - Wait for the page to load. The article page may contain:
     - A direct PDF link (often in an `<embed>` or `<iframe>` with a `.pdf`
       URL).
     - A "Save" button or a link to download the PDF.
     - A CAPTCHA or an error message -- in which case, go back to step 2 and
       try another mirror.
     - An explicit "article not found on Sci-Hub" message (distinct from a
       CAPTCHA/block) -- report this to the researcher immediately rather
       than retrying the remaining mirrors, since a different mirror won't
       have an article Sci-Hub itself doesn't hold.
   - Read the page to find the PDF URL. It typically looks like
     `https://sci-hub.se/downloads/.../....pdf` or is embedded in an
     `<embed>` tag.

**If every mirror is CAPTCHA'd, blocked, or down:** try Step 2 (the Debvex
MCP server) if it's connected -- its domain list sometimes differs from the
one above. If that's not available either, HALT and ask the researcher how
to proceed -- never invent another download mechanism.

Once you have a direct PDF URL, go to Step 3.

## Step 2: Resolve and Download via the Debvex Sci-Hub MCP Server (optional)

**Optional enhancement, not required.** Lower token cost and adds
CrossRef-based metadata enrichment over Step 1, but only useful if this
network can already reach Sci-Hub, or the researcher has a working
**system-wide** VPN/proxy -- see "Troubleshooting: network blocking" below
for why a browser-extension VPN doesn't help this path even though it helps
Step 1. Debvex Sci-Hub MCP Server: `github.com/Debvex/Sci-Hub-MCP-Server`,
package `sci-hub-mcp-server`.

The confirmed tool surface -- never call a tool name or signature beyond
this list:

- `search_scihub_by_doi(doi)`
- `search_scihub_by_title(title)`
- `search_scihub_by_keyword(keyword, num_results=10)`
- `download_scihub_pdf(pdf_url, output_path)`
- `get_paper_metadata(doi)`

**Check availability first.** Look for a connected `scihub` MCP server among
this session's available tools before calling any of the above. If it isn't
there, it's either not installed/configured, not worth setting up (needs a
system-wide VPN/proxy to be useful if Sci-Hub is blocked here), or
`~/.mcp.json` was edited but Claude Code hasn't been restarted since --
either way, use Step 1 instead.

**Install/config lives in `SETUP.md` §6, not here** -- that file is this
repo's single source of truth for install commands (per the `setup` skill's
own rule: never invent or duplicate an install command). If the researcher
wants to set this up, walk them through `SETUP.md` §6 (a `uv`-managed venv
with the required `mcp<2` pin, a known packaging-bug workaround, and the
`~/.mcp.json` entry to add) rather than restating it here. If a proxy is
needed (Sci-Hub blocked/geofenced on this network), `SETUP.md` §6's
`~/.mcp.json` entry can carry `SCIHUB_HTTPS_PROXY`/`SCIHUB_HTTP_PROXY` in an
`"env"` block -- but note this only works with an HTTP/SOCKS proxy or
system-wide VPN, not a browser extension.

**Domain list is hardcoded, not configurable.** Debvex resolves against its
own internal domain list -- currently (package v0.1.1) `sci-hub.su`,
`sci-hub.red`, a corrupted entry (`sci-hub.rensci-hub.rusci-hub.st` -- a
real upstream bug, always fails to resolve, harmless since the other
domains still get tried), and `sci-hub.box`. Verified 2026-09-17: `.su` and
`.box` reachable with a VPN, `.red` currently 502 (mirror-side, unrelated to
blocking). There is no env var to override this list -- if it goes stale,
that's an upstream fix, not something to work around here.

**Once the server is available, resolve the identifier:**

- **DOI known:** call `search_scihub_by_doi(doi)`.
- **Title only:** call `search_scihub_by_title(title)` -- resolves via
  CrossRef to a DOI + `pdf_url`. If CrossRef finds nothing, stop and ask the
  researcher for the DOI directly -- do not treat this as an MCP-availability
  failure and do not fall back to Step 1 for this reason.
- **Keywords only:** call `search_scihub_by_keyword(keyword, num_results=10)`
  -- same CrossRef resolution. Show the researcher each candidate's title,
  author(s), year, and DOI (cap the list at a handful, e.g. the first 5) and
  have them pick one before downloading anything. If none of the candidates
  match what they meant, ask for a DOI or a more specific title instead of
  guessing.
- **Optional enrichment:** call `get_paper_metadata(doi)` once a DOI is known
  to pull title/author/year for confirming the right paper with the
  researcher. Missing CrossRef coverage means empty metadata fields -- that's
  expected, not an error; the DOI-based download still works.
- **A `status: not_found` result is a normal outcome, not a server failure.**
  Report it to the researcher as-is. It never triggers a fallback to Step 1
  by itself.

Once you have a `pdf_url`, go to Step 3.

### Troubleshooting: network blocking

If Step 1's mirrors won't load, or Step 2 returns errors/`not_found` for
papers that are clearly on Sci-Hub, the likely cause is **ISP-level DNS
blocking of Sci-Hub domains** (legally mandated in some countries) rather
than a bug in this skill. Confirmed real on one researcher's machine
2026-09-17: the system DNS resolver returned `NXDOMAIN` for Sci-Hub domains,
but the same domains resolved and loaded fine through a VPN.

**A VPN or proxy fixes this differently for each path, and the difference
matters:**
- **Step 1 (Chrome MCP)** runs inside the actual browser -- any VPN that
  affects browser traffic helps, including a browser-extension VPN.
- **Step 2 (the MCP server)** runs as a separate process outside the
  browser -- a **browser-extension VPN does nothing for it**. It needs
  either a system-wide VPN client, or an HTTP/SOCKS proxy your VPN provider
  exposes, pointed at via `SCIHUB_HTTPS_PROXY`/`SCIHUB_HTTP_PROXY` in the
  `~/.mcp.json` entry's `"env"` block (`SETUP.md` §6).

This is exactly why Step 1 is the default: it degrades gracefully with
whatever VPN the researcher already has (including just a browser
extension), while Step 2 needs infrastructure most researchers won't have
set up. If the researcher only has a browser-extension VPN (or none), don't
suggest Step 2 as the fix -- it won't help.

## Step 3: Download the PDF

Before writing to any `output_path`, check whether a file already exists
there; if so, pick a non-colliding filename (e.g. append the DOI or a
counter) instead of silently overwriting it.

**Via Chrome MCP (Step 1):**
1. Use Chrome MCP to open the PDF URL in a new tab and confirm it loads.
2. Download the file with `curl`, if a shell is available in your
   environment:
   ```bash
   curl -fL --max-time 60 -A "Mozilla/5.0" -o paper.pdf "PDF_URL"
   ```
   `-f` makes curl fail (rather than saving an HTML error page as if it were
   the PDF) on a non-2xx response. If curl still fails or the mirror
   requires session cookies/referer that a bare `curl` call won't carry
   (some mirrors do, even though the URL loaded fine in Chrome), fetch the
   PDF through Chrome MCP itself instead of `curl`.
3. If no shell is available, tell the researcher the direct PDF URL so they
   can save it themselves.

**Via the Debvex MCP server (Step 2):** call
`download_scihub_pdf(pdf_url, output_path)` directly, with `output_path`
set to the researcher's current directory (or wherever they specified) plus
a sensible filename. If the call errors or reports failure, tell the
researcher and fall back to Step 1 rather than proceeding as if it
succeeded.

**After either path, verify the download before trusting it:** confirm
the file exists on disk and that its first bytes are `%PDF` (not an HTML
CAPTCHA/error/paywall page saved with a `.pdf` extension). If the file is
missing or isn't actually a PDF, delete any partial/bad file, report the
failure to the researcher, and do not proceed to Step 4.

## Step 4: Offer to File the Download into Zotero

After a successful download (either path), the PDF is not left as a
silent orphan file:

1. **Confirm it's the right paper** before offering to file anything: check
   the downloaded title/author/year (from the mirror page in Step 1, or
   `get_paper_metadata`/CrossRef resolution in Step 2) against what the
   researcher actually asked for. If there's any doubt -- an ambiguous
   keyword match, or a title-search result that only loosely matches --
   confirm with the researcher which paper this actually is before asking
   about filing below.
2. **Ask the researcher** whether to file this PDF into Zotero ("Ask First":
   filing changes the researcher's Zotero library, so it needs explicit
   confirmation, unlike the download itself which only touches a local
   file).
3. **On "no":** stop here. Report the local path and leave it as-is.
4. **On "yes":** assemble the item metadata you already have (DOI, title,
   authors, year -- from the mirror page in Step 1, `get_paper_metadata`/
   CrossRef resolution in Step 2, or from what the researcher told you) and
   show it to them for confirmation. If they don't confirm it as-is, pause
   and revise the metadata with them rather than filing anything. Once
   confirmed, invoke the existing filing flow from `zotero-code-execution`
   (per its "Filing from a hand-downloaded PDF" walkthrough) -- no new code
   needed here, just wiring the call:
   ```bash
   uv run .claude/skills/zotero-code-execution/zotero_file.py \
     --check-duplicate '{"doi":"10.xxxx/yyyy","title":"...","arxiv_id":null}'
   # if no blocking match, researcher confirms this exact candidate, then:
   uv run .claude/skills/zotero-code-execution/zotero_file.py \
     --file '{"doi":"10.xxxx/yyyy","title":"...","arxiv_id":null}' \
     --item '{"itemType":"journalArticle","title":"...","creators":[...],"date":"...","DOI":"10.xxxx/yyyy"}' \
     --researcher-confirmed \
     --attach-pdf /absolute/path/to/the/downloaded.pdf
   ```
   (Step 0 may already have run a duplicate check on this identifier -- reuse
   that result instead of repeating it if nothing has changed.) A
   `doi_exact`/`arxiv` match blocks unconditionally -- report the existing
   citekey, don't file. A `title_contains`-only match is a heuristic: ask the
   researcher whether it's the same paper.
   - **Different paper:** re-run `--file` with `--override-duplicate-match`.
   - **Same paper:** `zotero-code-execution` has no update-existing-item
     mode -- `--file --attach-pdf` will report `filed: false` and echo the
     local PDF path back rather than attaching it. Report the existing
     citekey and tell the researcher to drag the downloaded PDF into that
     item manually in Zotero desktop; don't retry `--file` for this paper.

   If `--file` itself errors for a reason other than a duplicate, report the
   error and the local PDF path -- never imply a citekey was created when it
   wasn't.
5. **Report the citekey back** to the researcher in the same turn. If
   `"attach_pdf"."attached"` is `false`, also report the local PDF path from
   `"attach_pdf"."path"` so they can attach it manually in Zotero desktop --
   never drop this silently.
