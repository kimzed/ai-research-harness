---
name: subfield-lit-mapping
description: Build a comprehensive reading list for a research subfield starting from one or more seed papers, by iteratively hopping across the citation graph (snowball sampling) until it converges -- not a single citations/references/similar call against the original seed. Use when the researcher wants to "map this subfield", "find most/all the papers on X starting from this one", "do a systematic/snowball search", or otherwise wants broad subfield coverage rather than a single lookup.
---

# subfield-lit-mapping

A methodology skill, not an API wrapper. It drives repeated calls to
`lit-search` (see that skill for `lit_search.py`'s invocation, output
contract, and error handling -- not repeated here) according to an explicit
snowball algorithm, and hands off to `zotero-code-execution` at the end for
organizing/filing. If you find yourself explaining HTTP status codes or CLI
flags while using this skill, that content belongs in `lit-search`'s
SKILL.md instead.

## Why this is a separate skill

A single combined `--paper SEED --citations --references --similar` call
only sees one paper's immediate neighborhood. Empirically (verified against
a real curated subfield folder), that one hop recovers only a small fraction
of a manually-curated reading list -- even the backward `--references` hop,
which pulls in papers old enough to plausibly already be in such a list,
mostly surfaces *different* specific papers than the ones a human found by
building the field knowledge over time. Real subfield coverage requires
treating newly-accepted papers as new seeds and continuing to hop, plus a
complementary keyword search to catch canonical/foundational texts that a
single paper's citation graph may under-link. Getting this right is a
procedure, not a one-line reminder, hence its own skill.

## The algorithm

Maintain three sets across the whole session (in your own working memory,
not persisted by any script):

- **seeds** -- papers queued to be expanded (citations+references+similar
  pulled for them). Starts as whatever the researcher gave you.
- **expanded** -- seeds already queried; never re-query the same paper.
- **accepted** -- candidates judged relevant to the subfield so far
  (dedup against this before presenting or re-adding anything).

```text
seeds = {starting paper(s) the researcher gave you}
expanded = {}
accepted = {}

while seeds - expanded is non-empty:
    pick an unexpanded paper S from seeds
    run: lit_search.py --paper S --citations --references --similar
    mark S as expanded

    for each candidate C returned (across all three relations, deduped):
        if C is already in accepted or already in expanded: skip
        judge C's relevance to the subfield (title/abstract) -- your job,
          the script does no filtering
        if relevant:
            add C to accepted
            add C to seeds          # <-- this is the step that makes it a
                                     #     snowball instead of one hop; do
                                     #     not skip it
        else:
            note briefly why excluded (useful for the divergence check below)

    if this hop added zero new accepted candidates: it's a signal the graph
      has converged from this direction (see Stopping below) -- not
      necessarily done overall, see Topic search below.
```

The critical difference from a single lit-search call: **every candidate
you accept becomes a new seed**, and you keep going until expansion stops
producing anything new. Running one combined call against only the
original seed and then switching to keyword search is not this algorithm --
it's a single hop plus a fallback, and will under-recall.

## Defining subfield scope

Don't infer the subfield's boundary purely from one seed paper's title if
you can avoid it -- ask the researcher for a sentence describing scope (or
confirm your inferred one) before you've burned many hops on the wrong
boundary. A title like "X evaluation using method Y in city Z" conflates a
general method with a narrow case study; know which one you're mapping.

## Topic search as a complement, not a fallback

Citation graphs under-link canonical/foundational texts (methodology papers,
surveys, textbooks) that a narrow empirical paper cites informally or not at
all, even though they define the subfield. Run `--topic` queries alongside
the citation-graph hops, not only after they "run dry":

- Derive query terms from the seed(s) and from titles already in `accepted`
  -- specific enough to stay on-topic (e.g. the subfield's actual named
  method + domain), not generic method-level terms alone (a bare method name
  like a weighting technique, decoupled from the application domain, will
  pull in every unrelated field that also uses that method).
- Treat topic-search hits the same as citation-graph candidates: judge
  relevance, dedup against `accepted`/`expanded`, and if accepted and it has
  a resolvable paper id, add it to `seeds` too -- a topic hit can open a new
  citation neighborhood you hadn't reached yet.

## Stopping

No fixed hop count or corpus-size target -- don't stop at an arbitrary
number and call it done, and don't keep expanding past the point of
diminishing returns just to hit a round number. Converge when:

- A full pass over the current `seeds` produces no new `accepted` papers
  (citation-graph side), **and**
- A couple of differently-worded `--topic` queries drawn from the accepted
  set surface nothing new either.

If the researcher is present, check with them once it looks converged --
they may know a boundary or a missing sub-thread you can't see from the
papers alone.

## Push back on divergence

If expansion starts pulling in papers that don't share a coherent subject
with the seeds (a broadly-cited method drags in unrelated application
domains, or a topic query goes generic), say so explicitly: name what
diverged and why, and propose alternatives (a narrower seed, leaning on a
different relation, or isolating a sub-cluster) rather than presenting an
incoherent mixed-topic list as if it were the subfield. Never paper over a
divergent result with a false "coherent subfield" narrative.

## Stay read-only, then hand off

This skill only searches and reasons about relevance -- it never files,
dedup-checks against Zotero, or writes anything. Once the `accepted` set has
converged, hand off to `zotero-code-execution` (its own SKILL.md covers
`--check-target`/`--check-duplicate`/`--resolve`/`--list-collection`/
`--file`) to check where things should be filed and get the researcher's
confirmation before writing anything. Don't re-describe that skill's
mechanics here -- but do use `--list-collection` early, not just at the end:
if the researcher already has a collection that looks related (surfaced via
`--check-target`'s target list, or something they mention), browse its
actual contents before spending hops re-discovering what's already there.
`--check-duplicate` one candidate at a time is a poor substitute for
actually seeing a folder's contents up front.
