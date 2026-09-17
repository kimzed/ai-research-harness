---
name: scihub-pdf-downloader
description: >
  Use when the user wants to download a scientific article PDF via Sci-Hub.
  The user may provide a DOI, article title, or keywords. This skill first
  checks whether the paper is already filed in Zotero with an attached PDF
  (via zotero-code-execution), then downloads it via the Debvex Sci-Hub MCP
  server (preferred) or Chrome MCP mirror scraping (fallback), and offers to
  file the result into Zotero afterward.
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
  arXiv preprints, so neither Option A's tools nor Option B's mirrors can
  reliably find these. Point the researcher to arxiv.org directly, or ask
  whether the paper has since acquired a DOI (e.g. via journal publication).

**Before calling any Sci-Hub tool (Option A or Option B), always check
whether this paper is already filed in Zotero with a usable PDF attached** --
reuse it instead of re-downloading:

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

## Step 1: Resolve and Download via the Sci-Hub MCP Server (Option A)

**Option A (preferred): Debvex Sci-Hub MCP Server** --
`github.com/Debvex/Sci-Hub-MCP-Server`, package `sci-hub-mcp-server`.

The confirmed tool surface -- never call a tool name or signature beyond
this list:

- `search_scihub_by_doi(doi)`
- `search_scihub_by_title(title)`
- `search_scihub_by_keyword(keyword, num_results=10)`
- `download_scihub_pdf(pdf_url, output_path)`
- `get_paper_metadata(doi)`

**Check availability first.** Look for a connected `scihub` MCP server among
this session's available tools before calling any of the above. If it isn't
there, it's either not installed/configured yet on this machine, or
`~/.mcp.json` was edited but Claude Code hasn't been restarted since --
either way, skip straight to Option B (Step 2) after saying so.

**Install/config lives in `SETUP.md` §5, not here** -- that file is this
repo's single source of truth for install commands (per the `setup` skill's
own rule: never invent or duplicate an install command). If the researcher
wants to set this up now, walk them through `SETUP.md` §5 (a `uv`-managed
venv with the required `mcp<2` pin, a known packaging-bug workaround, and
the `~/.mcp.json` entry to add) rather than restating it here. If a proxy is
needed (Sci-Hub blocked/geofenced on this network), `SETUP.md` §5's
`~/.mcp.json` entry can carry `SCIHUB_HTTPS_PROXY`/`SCIHUB_HTTP_PROXY` in an
`"env"` block.

**Once the server is available, resolve the identifier:**

- **DOI known:** call `search_scihub_by_doi(doi)`.
- **Title only:** call `search_scihub_by_title(title)` -- resolves via
  CrossRef to a DOI + `pdf_url`. If CrossRef finds nothing, stop and ask the
  researcher for the DOI directly -- do not treat this as an MCP-availability
  failure and do not fall back to Option B for this reason.
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
  Report it to the researcher as-is. It never triggers a fallback to
  Option B by itself.

Once you have a `pdf_url`, go to Step 3.

## Step 2: Locate the Article via Chrome MCP (Option B -- fallback only)

Use this path only when the Debvex MCP server's tools are unavailable (not
configured, not connected, or erroring in a way that is not a normal
`not_found` result). **Tell the researcher explicitly that you are falling
back to Chrome-MCP mirror scraping and why.**

1. Navigate to a page that maintains a live mirror list, such as:
   - `https://sci-hub.se/` (the primary domain, often redirects)
   - `https://sci-hub.st/`
   - `https://sci-hub.ru/`
   - `https://sci-hub.ee/`
   - `https://sci-hub.vg/`

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
     raw title text (Step 0 only checks Zotero for existing content, it does
     not resolve a title to a DOI; that CrossRef resolution only happens in
     Option A, which is unavailable if you're here).
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

**If every mirror is CAPTCHA'd, blocked, or down, HALT and ask the
researcher how to proceed** -- never invent another download mechanism.

Once you have a direct PDF URL, go to Step 3.

## Step 3: Download the PDF

Before writing to any `output_path`, check whether a file already exists
there; if so, pick a non-colliding filename (e.g. append the DOI or a
counter) instead of silently overwriting it.

**Option A (MCP server available):** call
`download_scihub_pdf(pdf_url, output_path)` directly, with `output_path`
set to the researcher's current directory (or wherever they specified) plus
a sensible filename. If the call errors or reports failure, tell the
researcher and fall back to Option B rather than proceeding as if it
succeeded.

**Option B (Chrome MCP fallback):**
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

**After either option, verify the download before trusting it:** confirm
the file exists on disk and that its first bytes are `%PDF` (not an HTML
CAPTCHA/error/paywall page saved with a `.pdf` extension). If the file is
missing or isn't actually a PDF, delete any partial/bad file, report the
failure to the researcher, and do not proceed to Step 4.

## Step 4: Offer to File the Download into Zotero

After a successful download (either option), the PDF is not left as a
silent orphan file:

1. **Confirm it's the right paper** before offering to file anything: check
   the downloaded title/author/year (from `get_paper_metadata`/CrossRef
   resolution in Step 1, or from the mirror page in Step 2) against what the
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
   authors, year -- from `get_paper_metadata`/CrossRef resolution in Step 1,
   or from what the researcher told you) and show it to them for
   confirmation. If they don't confirm it as-is, pause and revise the
   metadata with them rather than filing anything. Once confirmed, invoke
   the existing filing flow from `zotero-code-execution` (per its "Filing
   from a hand-downloaded PDF" walkthrough) -- no new code needed here, just
   wiring the call:
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
   that result instead of repeating it if nothing has changed.) If
   `--check-duplicate` surfaces a blocking match here, surface it to the
   researcher instead of proceeding to `--file`. If `--file` itself errors,
   report the error and the local PDF path -- never imply a citekey was
   created when it wasn't.
5. **Report the citekey back** to the researcher in the same turn. If
   `"attach_pdf"."attached"` is `false`, also report the local PDF path from
   `"attach_pdf"."path"` so they can attach it manually in Zotero desktop --
   never drop this silently.
