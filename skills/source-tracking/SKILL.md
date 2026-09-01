---
name: source-tracking
description: Data provenance for every collected data item — where it came from, when, how, and at what confidence level. Use whenever data is gathered from any channel (search, scraping, provider outreach) and needs to be traceable back to where it came from, before it is trusted or merged into a canonical table.
---

# מעקב-מקורות (source-tracking)

## מטרה

Ensure that every collected data item can be traced back to where it came from — the golden rule of
Data gathering: every item is linked to a source. Without provenance, a number is a guess
that looks like a fact, and such an error is discovered only after it has already entered a calculation.

## מתי מופעל

- Every time a new item is collected — an image, price line, specification, video frame, provider response.
- Before merging data from staging into a canonical table — source verification is the entry gate.
- When encountering a new data source (repository, price list, API) — not just a single item but
  an entire repository — it must be recorded in the central registry immediately, not at the end of a cycle.
- When citing a number in a report: cite back to the line in the manifest/registry, not from memory.

## צעדים

1. **For every item — minimum fields.** Source (URL/provider name/video identifier), retrieval date,
   access method (official API / scraping / direct outreach / manual entry),
   and confidence rating. Example of a structured manifest in the project:
   `company, model, source, kind, query, url, localfile, w, h, video_id`
   (competitor-images manifest) — every line carries everything needed to reproduce
   where it came from.

2. **Rate confidence on a fixed scale, not in free-form description.** At least three levels: verified
   (independent confirmation/primary source), marketing claim (from the entity's own website without
   confirmation), unverified (not checked at all). A fixed "confidence rating" field in every table
   that the agent populates.

3. **A central living registry for repositories, not only for individual items.** One document that consolidates
   *types* of data sources (not every line separately) — with a consistent status legend (for example:
   in use / collected and ready / to check / lead not yet checked) and a license/commercial-use
   column. Update it at the same moment a new source is found — not a task list
   that is postponed.

4. **No back-end merge into the canonical table.** Collected data enters the staging of
   Data gathering with its source recorded on it. The merge into the table of truth is a separate
   and deliberate step (in this project — owned by Engineering), not a direct overwrite. This way it is always
   possible to compare "what was collected" with "what was approved for the table."

5. **Do not duplicate a record.** Before adding a new line — check whether the source already exists
   (dedup by URL/hash/entity name). A source that was checked and failed (403, unavailable, refusal)
   is also recorded — so no one checks it again by mistake a week later.

6. **Closing an open item updates the central document.** If a new finding closes
   a question that was marked "unknown"/"to check" — update the status in the same commit,
   and do not leave two conflicting sources on the same subject without specifying which one prevails.

## פלט צפוי

Every collected data item carries a source+date+method+confidence rating; a central source registry
updated in real time with every new repository; no data line without provenance enters
staging.

## בעלים

Data gathering

---
*Recorded in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage is enforced by R-004.*
