---
name: design-alternatives
description: Building a group of concept alternatives for a function in a uniform format for comparison, before running them through the screening gates. Use when a function or requirement has no design yet and needs candidate concepts drafted before gating and scoring.
---

# חלופות-תכן

## מטרה

To produce a small, fixed number of genuine concept alternatives for each function, in a uniform format
that makes it possible to compare dozens of them in the same table — before investing in costly testing of any one
of them. An alternative that was not written in the uniform format cannot be compared later.

## מתי מופעל

- A new function/requirement that does not yet have a proposed concept.
- Beginning stage-A of `system-design` for a given function.
- An existing alternative failed a gate, and a replacement is needed for the same role.

## צעדים

1. **Define the function in one sentence** — what it needs to achieve, not how. ("Lift
   a load from 0 to 1 m while maintaining stability", not "X linear motor").
2. **Build exactly three concept alternatives** (this number is not arbitrary — too few
   do not scan a real design space, while too many drown the process in testing costs. Three that are truly different in essence,
   not three cosmetic variations of the same idea).
3. **Write each alternative in a fixed template**:
   ```
   ALT-<פונקציה>-<מס'>
     קונספט:      Description in one sentence
     פלטפורמה:    Which platform/enclosure it belongs to
     כלים/רכיבים: What is required to build it, including part number if available
     שערים:       Empty at this stage — will be filled in when the gates are run
     המדד הקריטי: The physical parameter determining success/failure
     עלות:        Estimate $ · percentage of off-the-shelf components
     סיכון:       The largest currently untested gap/assumption
   ```
4. **Mark the critical metric (the newtons) in advance** for each alternative — the force/torque/accuracy
   it must meet, and the source of the threshold (standard/axiom/calculation). Without this, it is impossible
   to know at which gate it will fail first.
5. **Add the three to the matrix file** (`alternatives.csv` or equivalent) before
   transferring them to `system-design` to run the actual gates.
6. **An alternative that failed a gate remains in the table with a documented FAIL**, rather than being deleted — so
   the next attempt does not accidentally rebuild that alternative.

## פלט צפוי

Three complete `ALT-*` rows in the uniform format in the matrix file, each with an explicit critical metric
and threshold and a source for the threshold, ready to be entered into the `system-design` process.

## בעלים

Engineering Lead

---
*Registered in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage is enforced by R-004.*
