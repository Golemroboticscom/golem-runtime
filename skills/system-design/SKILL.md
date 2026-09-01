---
name: system-design
description: Design one function in isolation by running its concept alternatives through a cheapest-to-most-expensive gate sequence, then unify the surviving winners into the fewest shared platforms. Use when designing a new function/subsystem from scratch, deciding how many concept alternatives to evaluate, or converging several winning alternatives into a shared platform.
---

# System design — function, gates, unification

## Purpose

Design a **function**, not a whole product, and stop over-engineering and wasted
computation on alternatives that will fail anyway. The finished robot is what
falls out of *unifying* the separately-solved functions — it is not the starting
point. The deliverable is never one solver or one opinion; it is a filtered,
documented alternative that survived an ordered gate sequence, with the losing
options recorded so they are never re-proposed.

## When to use

- A new requirement with no design solution yet (component, mechanism, subsystem).
- Several concept alternatives compete for the same role and one must be chosen
  systematically.
- The function stage is done and winning alternatives must be unified into the
  minimum set of shared platforms (couple to `cross-discipline-coordination`).

## The process, grounded

This project already has the **approved, locked design-selection algorithm**:
`projects/design_robot/design/demolition_design_algorithm_APPROVED_2026-07-28.md`
(24 steps; supersedes the v1/v2 drafts). Its shape *is* this skill's method:

- **Cheapest-first ladder.** Analytic sanity (minutes) -> low-resource
  kinematic / stability (manager gate 9) -> full dynamic / FEA (step 14).
  Escalate only survivors.
- **Five manager gates** (steps 6, 9, 13, 21, and the physical final gate 24)
  are hard stops; the agent does not pass a gate alone, and every approval is
  recorded to `_meta/decisions/`.
- **A living rejected-and-why register** so a killed alternative is never
  re-proposed (step 5); if everything is rejected, return to concept generation.
- **The independent second AI receives the spec only**, never the preferred
  alternative -- no anchoring (couple to `second-engine-crosscheck`).
- **Binary pass/fail is a gate;** efficiency / price / speed only *rank* the
  survivors.

Concrete project artifacts to read and extend rather than reinvent: the
alternatives register `demolition_alternatives_register.md`; the spec + weights
`demolition_spec_weights_v3_2026-07-27.md`; the gate-9 package
`demolition_gate9_package_2026-07-28.md`; the parametric source of truth
`projects/design_robot/robot_params.csv`; and design axioms A1-A5 checked by
`check_axioms.py`.

## Steps

1. **Define the function, not the component.** State what the system must achieve
   ("move a load from A to B"), not a pre-chosen part -- so no concept is locked
   in prematurely.
2. **Generate concept alternatives in one fixed format** so dozens compare in the
   same table:
   ```
   ALT-<function>-<n>
     concept:         one-sentence description
     platform:        which platform / enclosure it belongs to
     parts:           what is needed to build it
     gates:           result at each gate (below)
     critical metric: the governing physical parameter vs its threshold
     cost:            $ estimate  |  % off-the-shelf
     risk:            the largest gap still open
   ```
   In this project both agents propose (Claude at step 2, the independent AI at
   step 3) and both feed the same register.
3. **Run alternatives through the ordered gate sequence, cheapest first.** In this
   project (approved algorithm): sanity / arithmetic -> statics / loads ->
   stability (low-resource kinematic, gate 9) -> structure (FEA -- `run-fea`) ->
   dynamics -> integration -> economics. **FAIL a gate = stop; do not carry a
   rejected alternative into the next, more expensive gate** -- that ordering is
   the entire point.
4. **Check the critical parameter against an explicit threshold with a source for
   each** (standard, design axiom, or computed value) -- never an invented number.
   Design axioms A1-A5 are enforced by `check_axioms.py`; frame-strength
   thresholds come from `run-fea`; stability from
   `kinematics-and-cog-tip-over-margin`. Every number goes through code
   (constitution iron rule), not agent estimation.
5. **Record every alternative and every gate result** in the living register
   (`demolition_alternatives_register.md` or equivalent) -- a column per gate,
   PASS / FAIL / gap per cell, and a rejection reason that keeps a killed option
   out. If *every* alternative fails a gate, return to step 1/2 and generate new
   concepts -- do not force a failed one through.
6. **Rank only the survivors** with `weighted-scoring` (against
   `demolition_spec_weights_v3`). A gate *filters*; scoring *ranks* -- never
   conflate the two.
7. **Unify winning functions into platforms.** Once each function has a winner,
   cross-check which alternatives share a platform / interface and minimize the
   variant count (`cross-discipline-coordination`; the approved algorithm does
   this at step 13a).
8. **Validate the unified system as a whole**, not just each function alone --
   separately-good alternatives can collide on envelope, combined load, or
   interface once assembled.
9. **Log final decisions.** Each locked choice gets a `decision-log` row and, if
   it moves derived tables (BOM, requirements spec), propagate via
   `design-change-sync`. Manager-gate approvals are recorded to `_meta/decisions/`.

## Expected output

A complete alternatives register with a gate result in every cell; a documented
winning alternative per function with its rationale; a list of still-open
gaps / risks not yet gated; and a `decision-log` row for each final decision.
State the number *and* its threshold -- never just "pass / fail".

## Scope / limits

This is the selection *method*, not the domain analyses it triggers. The actual
FEA, stability, and multi-body-dynamics numbers come from `run-fea`,
`kinematics-and-cog-tip-over-margin`, and `simulation-use`. This skill does not
run those solvers or invent thresholds; it orders them cheapest-first, records
results, ranks survivors, and stops at manager gates.

## Owner

Engineering Lead

---
*Cataloged in `_meta/skills.csv` -- the source of truth for skills (scope | owner |
status | confidence | created/updated | call_count | purpose); coverage enforced by R-004.*
