---
name: dimensional-and-units-sanity-check
description: Sanity-check the units and order of magnitude of every physical value before it enters a calculation or a document — catch mm/inch, N·m vs kg·cm, and dry-vs-full mass confusion. Use before any physical number (mass, force, torque, length, price) enters a downstream calculation, a BOM line, or a delivered document, or whenever a number looks surprisingly off.
---

# Dimensional & Units Sanity Check

## Purpose

A physical number with no unit check is a silent bomb: a diameter in mm read as inches, a torque in N·m compared against kg·cm, a mass "410" that is dry in one place and full in another. The goal is to catch this *before* the number enters a structural calculation, a BOM line, or a document that goes to Yakov. This is a fast front-line filter that feeds [[verification]] — it does not replace running the number through code.

## When triggered

- Before a physical value enters an FEA / beam-check input, a BOM line, or a delivered document.
- Whenever a number "looks off" — an order of magnitude that does not match physical intuition.

## Steps

1. **Confirm the actual unit, not the assumed one.** Read the header/column at the source — do not assume "probably mm" because it usually is. CSV/BOM files often carry the unit in a separate column; check it.

2. **Convert to one reference unit before comparing.** In SI that is mm / kg / N / N·m. Never eyeball raw values that are in different units.

3. **Order-of-magnitude check against a known physical anchor.** A person ~70 kg, an interior door ~800 mm, a full waste bag ~20 kg. A value that is an order of magnitude off its anchor is caught here, before it reaches a precise calculation.

4. **Mass — separate the definition scope explicitly.** Dry / with-fluids / with-payload are three different numbers, not the same number rounded differently. Do not compare "410" from two places without confirming both mean the same scope (`agents/engineering/lead/check_axioms.py` → `mass_breakdown()` decomposes exactly for this).

5. **Cross-check through an independent second calculation path, where one exists for the domain.** Structural — a beam solver against CalculiX (`agents/engineering/lead/frame_beam_check.py` vs `agents/engineering/lead/run_fea.py`); other domains — any second method that does not share code with the first. Two solvers must agree on the governing *location*, not only the number — a location mismatch means a mis-defined model. (Iron Rule: mechanical values are confirmed through code, not reasoning.)

6. **Flag the exact confusion caught** — "mm vs inch in column X", not "the number looks wrong."

## Pitfalls

- **Assumed units.** Trusting a convention instead of reading the source column — the classic mm/inch and N·m/kg·cm traps.
- **Scope collision on mass.** Comparing a dry mass against a full mass as if they were the same quantity (the 428 vs 409.7 pattern).
- **Eyeballing magnitude.** Judging "about right" without a real anchor, so a 10× error slips through.
- **Stopping at the sanity check.** Treating a passed unit check as full verification — it is only the front filter; the number still goes through code and [[verification]].

## Expected output

Pass / fail for each value checked, and on fail the specific unit- or scope-confusion caught ("mm vs inch in column X", "dry vs full mass"). A caught issue routes to [[correction]] with the source; it is never fixed silently.

## Owner

Validator

---
*Listed in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · created/updated dates · call_count · purpose); coverage enforced by R-004.*
