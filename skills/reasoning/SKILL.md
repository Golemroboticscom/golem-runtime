---
name: reasoning
description: An explicit inference chain (cited fact → inference step → conclusion) that never goes beyond what the data supports. Use when Analyst must turn cited facts from Data gathering's package into a usable "so what" conclusion for downstream agents, not just restate the facts.
---

# Reasoning (Inference Chain)

## Purpose
This is Analyst's core verb: to interpret — not to collect (that is Data gathering) and not to perform final correctness verification (that is Validator). The skill guarantees that every conclusion passes through a visible chain from a cited fact to an inference to a decision — and never jumps straight to a gut feeling. Analyst's iron rule applies in full: no source, no conclusion; uncertainty is marked explicitly; no inference is stretched beyond what the data supports.

## When It Runs
Whenever Analyst is asked to answer "so what" about a data package — not merely to pass along a list of facts, but to produce a conclusion that another agent (Engineering, Orchestrator) can act on.

## Steps
1. **Separate three layers explicitly** in every conclusion:
   - (a) raw fact + source citation,
   - (b) inference step,
   - (c) conclusion.
   (b)/(c) must never masquerade as (a). Whoever reads the conclusion should be able to point to the layer each sentence belongs to.
2. **State the inference rule explicitly.** "If X holds across n sources and no source contradicts it — then Y" — not a hidden intuition. A visible inference rule can be reviewed; a hidden rule cannot.
3. **Actively search for the contradicting case before closing a conclusion.** One reliable source that contradicts lowers confidence or adds a caveat — it does not quietly disappear.
4. **Distinguish "the data does not support" from "the data contradicts."** The first halts the inference chain here and waits for more collection (back to Data gathering); the second is a finding in its own right and the chain continues.
5. **Limit the conclusion to what the data supports.** Do not round up to a claim stronger than the evidence — this is the iron rule stated explicitly in Analyst's core file.
6. **Tag confidence** according to the confidence-tagging skill: high only when the inference rule is simple and the support is broad; every additional inference step in the chain lowers the ceiling, even if each step is reasonable on its own.
7. **Record in the decision-log** every conclusion that changes an existing design assumption or closes an open point — not only conclusions that feel "big."

## Expected Output
A conclusion with a visible fact→inference→conclusion chain, a citation in the fact layer, a confidence tag, and a decision-log entry if the conclusion touches a design assumption.

## Owner
Analyst

---
*Registered in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage enforced by R-004.*
