---
name: weighted-scoring
description: Ranking design alternatives that satisfy all constraints according to several weighted objective axes, including Pareto-frontier checking. Use when multiple design alternatives all satisfy the hard constraints and a ranked recommendation is needed based on strategic priorities like weight, cost, size, or commonality.
---

# ניקוד משוקלל

## מטרה

To rank **feasible** design alternatives (those that have already passed all constraints — see
`decision-making`) according to several objective axes simultaneously, without allowing one axis (for example, price)
to conceal a real trade-off against another axis (for example, weight).

## מתי מופעל

- Several alternatives have passed all the filtering gates of `system-design` and a choice is still needed.
- It is necessary to compare different priority profiles (for example, a "high-volume product" versus an "old structure where weight is critical") and see whether the same alternative still wins.

## צעדים

1. **Verify that the alternatives have already passed constraint filtering.** Scoring does not fix an alternative that failed
   at a gate — it only ranks alternatives whose constraints already PASS.
2. **Define the relevant objective axes.** Example from the project (five axes):
   weight↓, size↓, price↓, percentage-of-off-the-shelf-components↑, commonality (sharing with
   other configurations)↑. Other axes depend on the context — the pattern is identical:
   a clear optimal direction for each axis.
3. **Normalize each axis to 0–1**, independently of its unit:
   - For minimization (`weight, cost` etc.): `score = (worst − value) / (worst − best)`
   - For maximization (`% off-the-shelf, commonality`): `score = (value − worst) / (best − worst)`.
   - **Edge case — all alternatives are equal on an axis** (`worst == best`, denominator=0): set `score = 1` for all of them, or remove the axis from the weighting (no variation = no distinguishing information).
4. **Set weights — ⚠️ this is a strategic decision, not an engineering one.** A weight `wᵢ` for each axis,
   sum=1. **The weights are always set by a human**, not inferred automatically (see
   `decision-making` §2) — the same alternative can win under one weight profile and lose
   under another, so the weight itself is a decision, not a calculation.
5. **Calculate the weighted score**: `Total = Σ (wᵢ × scoreᵢ)`. The highest score = the leader
   *under the same weights*.
6. **⚠️ Do not rely only on the weighted score — check the Pareto frontier.** A weighted score hides
   trade-offs: an alternative that cannot be improved on one axis without harming another is "on the frontier" —
   a legitimate weight-dependent choice, even if its score is not the highest. An alternative **outside**
   the frontier (there is another alternative that is at least as good on every axis) is dominated (dominated) — reject
   it always, regardless of the weights.
7. **Run the same table against several weight profiles** if there is more than one use-case —
   an alternative may suit one role and not another. Example from the project: a shoulder actuator
   that is the clear winner for a demolition arm (weight/price/off-the-shelf) but loses for a precision arm, where the
   commonality axis (consolidation into one arm) becomes critical and reverses the result.
8. **Document the final ranking** in the matrix file: every axis normalized, the weights applied,
   the score, and a flag indicating whether the alternative is on the Pareto frontier. For locking — proceed to the `decision-log` skill.

## פלט צפוי

A ranking table with a final score for each feasible alternative, details of the normalization for each axis, the weights
applied (and who set them), and an explicit indication of the Pareto frontier — not just "the
winning alternative".

## בעלים

Engineering Lead

---
*Listed in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage is enforced by R-004.*
