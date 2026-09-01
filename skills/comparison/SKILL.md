---
name: comparison
description: Structured comparison between two or more items (competitors, components, design options) according to predetermined criteria, source-per-cell, and confidence labeling. Use when a downstream agent needs to choose between options or understand relative positioning — e.g. comparing two motor options or our arm reach vs. a competitor's.
---

# Comparison (השוואה מובנית)

## מטרה
Produce a comparison table that can be relied upon: criteria established before looking at
the numbers (to prevent cherry-picking), a source for every cell, and information gaps explicitly marked
and not hidden. The Analyst rule of thumb applies: every value in the table rests on a cited source.

## מתי מופעל
When a downstream agent needs to choose between alternatives or understand relative positioning —
for example, comparing two motors (RMD-X15 versus Harmonic FHA-32C — an open point
in the project's core file), or comparing our arm reach against a competitor.

## צעדים
1. **Set the comparison criteria before looking at the values.** The axes are derived
   from the question being asked, not from the numbers that are easiest to find. Setting them later creates an opening
   for cherry-picking.
2. **Require the same criteria for every item in the set.** A criterion missing for an item is explicitly marked
   "unknown" — it is never silently omitted and never approximately estimated instead of
   the actual value.
3. **Normalize units within the column before comparison** (kg versus pounds, N·m versus kg·cm).
   Final unit cross-checking is the Validator's job, but the Analyst checks their own work
   already while building the table — a table with mixed units is a wrong conclusion in advance.
4. **When the sources themselves contradict one another** (two datasheets give different specifications for the same component) —
   present both values and mark the contradiction; do not choose one. Resolving
   contradictory values is a matter for the Validator, not the Analyst's judgment.
5. **Write one interpretation line at the end of the table.** A raw table is not a conclusion — the interpretation
   ("what it means") is the conclusion (see the reasoning skill).
6. **Assign an overall confidence label to the table** according to the weakest-source cell, not according to an average —
   a table that is strong in nine cells and weak in one is a weak table.

## פלט צפוי
A criteria×items table with a source for every cell, gaps explicitly marked (not silently
left blank), one interpretation line, and an overall confidence label.

## בעלים
Analyst

---
*Recorded in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage is enforced by R-004.*
