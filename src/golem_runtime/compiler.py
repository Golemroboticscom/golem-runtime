"""The compiler: a flow table becomes a LangGraph graph.

There is no hand-written graph anywhere in the runtime. `flow.csv` is the definition and
this file is the only thing that turns it into nodes and edges, which is what makes the
table the source of truth rather than a description of the code.

Five kinds of row, one node builder each:
  agent-step     -- an engine call through the single wrapper
  script-step    -- a deterministic local handler, no model
  outbound-send  -- an effect that leaves the system
  human-gate     -- stop, ask, resume with the decision and its provenance
  wait-external  -- stop, wait for something outside, resume the same way

A node that has a `loop_back_to` also has to choose. It asks the engine which target to
take and accepts only a target the table already permits, so a routing answer can never
invent an edge.
"""
from __future__ import annotations

import copy
import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from . import artifacts, tables
from .effects import EffectLog
from .engine import EngineWrapper
from .gates import validate_answer
from .validate import TERMINAL_PREFIX, is_exit, ref_to_step, split_targets

PARAM_RE = re.compile(r"\$\{[^}]+\}")


class RunState(TypedDict, total=False):
    run_id: str
    flow: str
    params: dict[str, str]
    current_step: str
    next_route: str
    terminal: str
    trace: list[str]
    outputs: dict[str, str]
    approvals: dict[str, dict[str, str]]
    external_inputs: dict[str, dict[str, str]]
    loop_counts: dict[str, int]
    route_plan: dict[str, list[str]]
    artifacts: list[dict[str, Any]]
    cancel_requested: bool


def substitute(text: str, params: dict[str, str]) -> str:
    return PARAM_RE.sub(lambda m: params.get(m.group(0), m.group(0)), text or "")


CARRY_STEPS = 3       # how many upstream outputs travel in full-ish
CARRY_CHARS = 1500    # how much of each


def build_prompt(row: dict[str, str], state: RunState) -> str:
    """The prompt carries a BOUNDED slice of the work so far.

    Measured on 2026-08-31: carrying the last six outputs whole pushed step 11's prompt to
    103,246 characters and still growing -- a 48-step flow would have ended in six figures
    of tokens per call. The flow is long, so what travels is an index of every prior step
    plus an excerpt of the last few. Token economy is an iron rule, not an optimisation.
    """
    params = state.get("params", {})
    outputs = state.get("outputs", {})
    done = list(outputs.items())
    index = ", ".join(step for step, _ in done)
    recent = [f"[{step}] {text[:CARRY_CHARS]}{' …(truncated)' if len(text) > CARRY_CHARS else ''}" for step, text in done[-CARRY_STEPS:]]
    upstream = (f"Steps already completed: {index}\n\n" + "\n\n".join(recent)) if done else ""
    return "\n".join(
        [
            f"Flow: {row['flow_name']} · step {row['step']} · phase {row.get('phase','')}",
            f"You are acting as: {row['actor']}",
            f"Action: {substitute(row.get('action',''), params)}",
            f"Input: {substitute(row.get('input',''), params)}",
            f"Declared output: {row.get('output','')}",
            "",
            "Work so far:" if upstream else "",
            upstream,
            "",
            "Answer with the declared output and nothing else.",
        ]
    ).strip()


def _routing_prompt(row: dict[str, str], allowed: list[str]) -> str:
    return (
        f"Step {row['step']} of flow {row['flow_name']} has finished. Decide where the flow goes next.\n"
        f"Continue forward to: {ref_to_step(row['next'], row['flow_name'])}\n"
        f"Or loop back to one of: {', '.join(split_targets(row.get('loop_back_to',''), row['flow_name']))}\n"
        f"Reply with exactly one of these step ids and nothing else: {', '.join(allowed)}"
    )


def compile_flow(flow_name: str, engine: EngineWrapper, effects: EffectLog, gate_context: dict[str, Any] | None = None) -> StateGraph:
    rows = tables.flow(flow_name)
    by_step = {row["step"]: row for row in rows}
    exits = {
        target
        for row in rows
        for target in split_targets(row.get("next", ""), flow_name) + split_targets(row.get("loop_back_to", ""), flow_name)
        if is_exit(target, flow_name)
    }
    crossings = {t for t in exits if not t.startswith(TERMINAL_PREFIX)}
    if crossings:
        raise NotImplementedError(f"{flow_name} calls another flow ({sorted(crossings)}); phase A compiles one flow at a time")
    graph = StateGraph(RunState)

    def make_node(row: dict[str, str]):
        step, kind = row["step"], row["kind"]
        normal = ref_to_step(row.get("next", ""), flow_name)
        loops = split_targets(row.get("loop_back_to", ""), flow_name)
        ceiling = int(row["loop_ceiling"]) if loops and row.get("loop_ceiling", "").isdigit() else 0

        def node(state: RunState) -> dict[str, Any]:
            run_id = state["run_id"]
            trace = list(state.get("trace", []))
            outputs = dict(state.get("outputs", {}))
            approvals = copy.deepcopy(state.get("approvals", {}))
            external = copy.deepcopy(state.get("external_inputs", {}))
            loop_counts = dict(state.get("loop_counts", {}))
            route_plan = copy.deepcopy(state.get("route_plan", {}))
            stored_artifacts = list(state.get("artifacts", []))
            terminal = "END:Cancelled" if state.get("cancel_requested") else ""

            visit = sum(1 for s in trace if s == step)
            effect_key = f"{run_id}:{step}:{visit}"

            gate_answer: dict[str, str] | None = None
            if not terminal and kind in {"human-gate", "wait-external"}:
                answer = gate_answer = _ask(state, row, kind)
                if answer["decision"] in {"reject", "cancel"}:
                    terminal = "END:Cancelled"
                elif kind == "human-gate":
                    approvals[step] = answer
                else:
                    external[step] = answer

            if not terminal:
                payload, performed = effects.once(effect_key, run_id, step, lambda: _perform(row, state, effect_key, gate_answer))
                outputs[step] = payload.get("text", "")
                if payload.get("artifact"):
                    stored_artifacts.append(payload["artifact"])
                trace.append(step)
                engine.sink.emit(
                    "step_done",
                    step=step,
                    kind=kind,
                    actor=row["actor"],
                    visit=visit,
                    effect_key=effect_key,
                    replayed=not performed,
                    output_chars=len(payload.get("text", "")),
                )

            target = terminal or _choose(row, state, outputs, normal, loops, route_plan)
            if not terminal:
                allowed = {normal, *loops}
                if target not in allowed:
                    raise ValueError(f"{step}: route {target!r} is not one of {sorted(allowed)}")
                if target in loops:
                    count = loop_counts.get(step, 0) + 1
                    if count > ceiling:
                        raise RuntimeError(f"{step}: loop ceiling {ceiling} exceeded")
                    loop_counts[step] = count
                if target.startswith(TERMINAL_PREFIX):
                    terminal = target

            update: dict[str, Any] = {
                "current_step": step,
                "next_route": target,
                "trace": trace,
                "outputs": outputs,
                "approvals": approvals,
                "external_inputs": external,
                "loop_counts": loop_counts,
                "route_plan": route_plan,
                "artifacts": stored_artifacts,
            }
            if terminal:
                update["terminal"] = terminal
            return update

        def _ask(state: RunState, row: dict[str, str], kind: str) -> dict[str, str]:
            """Stop the graph. The runner carries the question to the surface and comes back."""
            question = {
                "gate": kind,
                "step": row["step"],
                "kind": kind,
                "actor": row["actor"],
                "run_id": state["run_id"],
                "action": substitute(row.get("action", ""), state.get("params", {})),
                "output": row.get("output", ""),
                "flow": row["flow_name"],
            }
            while True:
                try:
                    return validate_answer(interrupt(question), kind, state["run_id"])
                except ValueError as exc:
                    question = {**question, "error": str(exc)}

        def _perform(row: dict[str, str], state: RunState, effect_key: str, gate_answer: dict[str, str] | None) -> dict[str, Any]:
            step, kind = row["step"], row["kind"]
            if gate_answer is not None:
                # A gate's work IS the human decision. There is no engine call here, and
                # Yakov has no engine route in agents.csv precisely because he is not one.
                return {
                    "text": f"{gate_answer['decision']} by {gate_answer['actor']} ({gate_answer['provenance']})",
                    "decision": gate_answer["decision"],
                    "actor": gate_answer["actor"],
                    "provenance": gate_answer["provenance"],
                }
            if kind == "script-step":
                return _run_script(row, state, effect_key)
            purpose = "route" if kind == "outbound-send" else kind
            answer = engine.call(
                run_id=state["run_id"],
                step=step,
                actor=row["actor"],
                purpose=purpose,
                prompt=build_prompt(row, state),
            )
            payload = {"text": answer["text"], "provider": answer["provider"], "model": answer["model"]}
            if kind == "outbound-send":
                engine.sink.emit(
                    "outbound_send",
                    step=step,
                    actor=row["actor"],
                    destination=row.get("destination", ""),
                    effect_key=effect_key,
                    chars=len(answer["text"]),
                )
            return payload

        def _run_script(row: dict[str, str], state: RunState, effect_key: str) -> dict[str, Any]:
            """A script-step runs local code, never a model. Its heavy output becomes a pointer."""
            import tempfile
            from pathlib import Path as _Path

            body = "\n".join(
                [
                    f"# {row['flow_name']} step {row['step']} — {row['actor']}",
                    f"# {substitute(row.get('action',''), state.get('params', {}))}",
                    f"run: {state['run_id']}",
                    f"inputs seen: {', '.join(list(state.get('outputs', {}))[-6:])}",
                ]
            )
            with tempfile.TemporaryDirectory() as tmp:
                produced = _Path(tmp) / f"step-{row['step']}.txt"
                produced.write_text(body + "\n", encoding="utf-8")
                pointer = artifacts.store(produced, state["run_id"], row["step"], kind=row.get("output", "artifact"))
            engine.sink.emit("script_step", step=row["step"], actor=row["actor"], effect_key=effect_key, artifact=pointer)
            return {"text": f"artifact stored: {pointer['uri']} ({pointer['bytes']} bytes)", "artifact": pointer}

        def _choose(row, state, outputs, normal, loops, route_plan) -> str:
            if not loops:
                return normal
            step = row["step"]
            planned = route_plan.get(step)
            if planned:
                return ref_to_step(planned.pop(0), row["flow_name"])
            allowed = [normal, *loops]
            answer = engine.call(
                run_id=state["run_id"],
                step=step,
                actor=row["actor"],
                purpose="routing-decision",
                prompt=_routing_prompt(row, allowed),
            )
            chosen = answer["text"].strip().splitlines()[0].strip() if answer["text"].strip() else ""
            chosen = ref_to_step(chosen, row["flow_name"])
            if chosen not in allowed:
                engine.sink.emit("routing_fallback", step=step, answered=chosen[:80], chose=normal, allowed=allowed)
                return normal
            engine.sink.emit("routing_decision", step=step, chose=chosen, allowed=allowed)
            return chosen

        return node

    def router(state: RunState) -> str:
        """Purely reads the route the finished node already persisted."""
        target = state.get("next_route")
        if not target:
            raise RuntimeError("node finished without persisting next_route")
        return target

    for step, row in by_step.items():
        graph.add_node(step, make_node(row))
        # Only the edges this row actually declares, plus the cancel exit every gate can take.
        # Wiring every node to every node would compile the same, and draw an unreadable picture.
        targets = {ref_to_step(row.get("next", ""), flow_name), *split_targets(row.get("loop_back_to", ""), flow_name)}
        targets |= {t for t in exits if t.startswith(TERMINAL_PREFIX)}
        targets |= {"END:Cancelled"}
        graph.add_conditional_edges(step, router, {t: (END if t.startswith(TERMINAL_PREFIX) else t) for t in sorted(targets)})
    graph.set_entry_point(rows[0]["step"])
    return graph
