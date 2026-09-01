---
name: verification
description: Verify that a value or conclusion about to be used is actually supported — traced to a ranked source, every mechanical number computed through code (not reasoning), independently reproduced, and confidence-tagged. Use before any value, calculation, or conclusion crosses from draft into a decision, a document, a downstream calculation, or an answer to a human — and whenever asked "is this correct?".
---

# Verification

## Purpose

The last gate before a value or conclusion is put into use. My question is not "does it look right?" but "is it *supported*, complete, and reliable enough?" A number that is merely *asserted* is not verified; a verified number is traced to a ranked source, computed through code, reproduced independently, and carries an honest confidence tag. I verify and correct — I do not gather data (Data-gathering) or interpret it (Analyst).

## When triggered

- A value, calculation, or conclusion is about to leave draft: enter a document sent to Yakov, a logged decision, a BOM line, or the input to another calculation.
- Another agent's conclusion is about to be marked valid downstream.
- Anyone asks explicitly "is this correct / verified?"

## Steps

1. **Locate the exact source.** File + line/column, not "I think I read it somewhere." If no source can be pointed to, tag it **no-source** and stop treating it as fact — do not launder an unsourced value into a verified one.

2. **Rank the source against the truth hierarchy.** Follow the binding order (`CLAUDE.md` §1): in design data the primary source (CAD / parameters) prevails, and anything contradicting it is wrong; git is the source of truth for data, Linear for tasks. A value from a lower-ranked source is not auto-rejected but drops in confidence.

3. **Mechanical calculation = code, never reasoning.** If the conclusion contains an engineering/mechanical number, confirm it was produced by a computational tool — `agents/engineering/lead/run_fea.py` (CalculiX FEA), `agents/engineering/lead/frame_beam_check.py` (beam solver), or `agents/engineering/lead/check_axioms.py` (CAD envelope + mass/BOM vs axioms) — not derived in my head. A number reached by reasoning alone is an automatic FAIL; re-run it through code before it goes anywhere. (Iron Rule: calculation only through code — internal arithmetic risks hallucination.)

4. **Reproduce independently.** Re-run the calculation/query myself and compare to the claimed result. For a structural value, agreement means the two solvers agree on the *governing location*, not only the peak number — a location mismatch signals a mis-defined model, not a rounding gap. A difference beyond a stated tolerance is a flag, not a silent acceptance.

5. **Sanity-check units and magnitude.** Route physical numbers through [[dimensional-and-units-sanity-check]] — catch mm/inch, N·m vs kg·cm, dry vs full mass before they propagate.

6. **Cross-reference tables line by line.** When a value is compared across tables/documents, match the actual rows, not the summaries — e.g. gap 428 vs 409.7 is a real discrepancy that only surfaces on a line-by-line pass. For requirement coverage, check the RTM (`projects/design_robot/knowledge/requirements_traceability.csv`): every CG requirement/axiom <-> a design/simulation decision (`mapped_decision` / `mapped_simulation` / `coverage_status`), no orphan requirements.

7. **Independent second engine for AI-derived claims.** If any part rests on an LLM's judgment, cross-check on a *different* provider per [[second-engine-crosscheck]] (`canonical/gemini.py`, `tools/ask_gpt.py`, `agents/integration/integrations/second_opinion.py`, or `tools/council.py` for a full debate). Compare — do not auto-adopt. This is *additional* to code verification for mechanical values, never a replacement.

8. **Tag confidence and record the result.** Apply [[confidence-tagging]]. Below 70 -> route to [[uncertainty-review]] instead of passing it as "verified." Document what was checked, against which source, and what was found.

9. **On failure, hand off — do not fix silently.** A failed value goes to [[correction]] with a source and rationale; never overwrite a value without both. If two sources contradict and neither is clearly higher-ranked, do not resolve it alone — present both values with their sources to a human (Iron Rule).

## Pitfalls

- **Mental math.** Judging "within range" by eye instead of running the solver — the single most common way a wrong number passes.
- **Source laundering.** Promoting an unsourced or low-ranked value to "verified" because it looks plausible or a confident model asserted it.
- **Summary-level cross-check.** Comparing two documents' totals and missing a row-level discrepancy (428 vs 409.7).
- **Scope contamination.** Verifying against draft/uncommitted state, or drifting into re-gathering data (Data-gathering) or re-interpreting it (Analyst) instead of checking the claim in front of me.
- **Silent correction.** Editing a failing value in place instead of routing to `correction` with source + rationale, or resolving a contradiction alone instead of escalating.
- **Unmarked uncertainty.** Passing something as valid when a real gap exists but was left untagged.

## Expected output

For each claim checked: a verdict (**verified / not-verified / partial**), the exact source it was checked against, and a confidence tag. A rejection carries a concrete reason ("mm vs inch in column X", "computed by reasoning, not code", "gap 428 vs 409.7 across tables"), never "something feels off." On contradiction: an escalation carrying both values and both sources.

## Owner

Validator

---
*Listed in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · created/updated dates · call_count · purpose); coverage enforced by R-004.*
