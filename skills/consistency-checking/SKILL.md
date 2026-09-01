---
name: consistency-checking
description: Cross-checking the same fact across multiple documents/tables line by line so that contradictions do not silently propagate downstream. Use after any shared fact (mass, geometry, price, decision) is updated somewhere it is also referenced elsewhere, or periodically before a value is trusted across documents.
---

# Consistency checking (הצלבת-עקביות)

## מטרה

The same fact almost always appears in more than one place — a source table, a derived document, a summary.
The goal: catch a discrepancy between two places that are supposed to agree, *before* that discrepancy rolls into
a wrong design decision. A classic example from the project: the `robot_params.csv` summary gives
a full mass of 428 kg, while the BOM gives 409.7 — a gap of ~18 kg that has not been closed.

## מתי מופעל

- After updating a shared fact (mass, geometry, price, decision) that is mentioned in more
  than one document.
- Periodic review before locking critical numbers (stability, FEA, pricing).
- Before a value crosses into the next downstream document.

## צעדים

1. **Locate all places that state/derive the same fact.** Text-search across
   both canonical and derived files — not just the declared source.

2. **Extract the value + unit + definition scope from each place**, into a small table. “Definition
   scope” is the detail that is easiest to miss: dry mass versus full mass, unit price versus
   price including shipping, etc.

3. **Compare in pairs.** A discrepancy is a real contradiction only if the definition scope is identical. Two
   different values that measure different things (dry versus full) are not a contradiction — write
   this explicitly; do not mistakenly flag it.

4. **Determine which one wins** according to the domain’s source-of-truth hierarchy (the mechanical file/primary
   source is authoritative; an old snapshot document is not).

5. **Do not correct here.** Pass the losing values to the `correction` skill with the winning source identified — cross-checking is for detection, not correction.

6. **A contradiction that cannot be closed now** (insufficient data to decide) — do not invent
   a solution. Record it as a documented open contradiction (for example, in the open-contradictions
   section of the project’s core file, or in notes/) so that it is not rediscovered every time.

## פלט צפוי

A table of all places checked + matching/mismatching + the winning value and its source. Cases
that were not closed — explicitly recorded as open, not guessed.

## בעלים

Validator

---
*Recorded in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage is enforced by R-004.*
