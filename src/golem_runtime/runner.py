"""Running a flow: start it, carry its questions to the surface, bring the answers back.

The runner owns the outside of the graph. The graph stops itself with an interrupt; the
runner is what turns that stop into a Telegram message and the reply into a resume. It
also holds the two ceilings that keep a bad run from eating the machine: the step ceiling
on every invoke, and the checkpoint size check between them.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from langgraph.types import Command

from . import observe, store, tables
from .compiler import RunState, compile_flow
from .effects import EffectLog
from .engine import EngineWrapper
from .gates import AutoGate, GateChannel, GateRequest, parse_shape
from .paths import RUN_DIR, ensure_var_dirs
from .records import RecordSink
from .validate import validate_flow


def thread_id(run_id: str) -> str:
    return f"{run_id}-{hashlib.sha256(run_id.encode()).hexdigest()[:12]}"


def run_config(run_id: str, flow_name: str = "") -> dict[str, Any]:
    """The step ceiling is not decoration: it is the backstop above every loop_ceiling.

    The metadata is what makes the viewer readable rather than merely full: `thread_id`
    groups all 48 steps of one product into ONE conversation instead of 48 unrelated
    traces, and the tags become one-click filters.
    """
    return {
        "configurable": {"thread_id": thread_id(run_id)},
        "recursion_limit": tables.control_int("run_step_ceiling", "runtime"),
        "metadata": {"thread_id": run_id, "golem_run_id": run_id, "golem_flow": flow_name},
        "tags": [t for t in ("golem-runtime", flow_name, run_id) if t],
    }


def initial_state(run_id: str, flow_name: str, params: dict[str, str] | None = None, **extra: Any) -> RunState:
    state: RunState = {
        "run_id": run_id,
        "flow": flow_name,
        "params": dict(params or {}),
        "trace": [],
        "outputs": {},
        "approvals": {},
        "external_inputs": {},
        "loop_counts": {},
        "route_plan": {},
        "artifacts": [],
    }
    state.update(extra)  # type: ignore[typeddict-item]
    return state


def fixture_params(flow_name: str) -> dict[str, str]:
    """Opaque placeholders for every declared parameter. Dry runs only."""
    return {p: f"fixture:{p.strip('${}')}" for p in tables.declared_params(flow_name)}


class Run:
    def __init__(
        self,
        run_id: str,
        flow_name: str,
        gate: GateChannel | None = None,
        transport: str = "echo",
        state_store: str = "sqlite",
        checkpoint_dir: Path | None = None,
        effects_path: Path | None = None,
        record_dir: Path | None = None,
    ):
        ensure_var_dirs()
        self.run_id = run_id
        self.flow_name = flow_name
        self.gate = gate or AutoGate()
        self.transport = transport
        self.state_store = state_store
        self.checkpoint_dir = checkpoint_dir
        self.sink = RecordSink(run_id, record_dir)
        self.engine = EngineWrapper(self.sink, transport=transport)
        self.effects = EffectLog(effects_path)
        self.config = run_config(run_id, flow_name)

    def execute(self, params: dict[str, str] | None = None, resume: bool = False, **extra: Any) -> dict[str, Any]:
        validation = validate_flow(self.flow_name, params)
        # Turning tracing on here, not at import, means a run either reports itself entirely
        # or not at all -- never half. The graph's own nodes come free once this is set,
        # because LangGraph is a langchain-core runnable; only our model calls need the span.
        traced = observe.configure()
        self.sink.emit(
            "run_start",
            traced=traced,
            flow=self.flow_name,
            transport=self.transport,
            state_store=self.state_store,
            gate_channel=type(self.gate).__name__,
            validation=validation,
            resumed=resume,
        )
        started = time.time()
        graph = compile_flow(self.flow_name, self.engine, self.effects)
        with store.open_store(self.run_id, self.state_store, self.checkpoint_dir) as (checkpointer, path):
            app = graph.compile(checkpointer=checkpointer)
            first: Any = None if resume else initial_state(self.run_id, self.flow_name, params, **extra)
            try:
                result = app.invoke(first, self.config)
                result = self._drive(app, result, path)
                status = "completed"
                error = None
            except Exception as exc:
                result, status, error = {}, "failed", f"{type(exc).__name__}: {exc}"
            snapshot = app.get_state(self.config).values if not result else result
        summary = {
            "run_id": self.run_id,
            "flow": self.flow_name,
            "status": status,
            "error": error,
            "terminal": snapshot.get("terminal"),
            "steps_executed": len(snapshot.get("trace", [])),
            "distinct_steps": len(set(snapshot.get("trace", []))),
            "flow_rows": validation["rows"],
            "approvals": len(snapshot.get("approvals", {})),
            "external_inputs": len(snapshot.get("external_inputs", {})),
            "loop_counts": snapshot.get("loop_counts", {}),
            "artifacts": len(snapshot.get("artifacts", [])),
            "engine_calls": len(self.sink.of_event("engine_call")),
            "elapsed_s": round(time.time() - started, 1),
            "transport": self.transport,
        }
        self.sink.emit("run_end", **summary)
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        (RUN_DIR / f"{self.run_id}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._retrospective(snapshot, summary)
        return summary

    # ------------------------------------------------------------------ the closing report

    def _retrospective(self, snapshot: dict[str, Any], summary: dict[str, Any]) -> None:
        """What the whole run has to say about itself, once, at the end (Yakov #6847).

        Three things, and he named all three: what to change in the FLOW - the table, the
        rows, the order, the gates; what to change in the CONTENT - where the engineering
        was thin and what evidence was missing; and the whole thing summed up with every
        improvement worth making. It is written from what the run already produced, so it
        costs one call and invents nothing: the running critique the reviews wrote between
        steps, the trace, and what the record measured.

        It runs on the high-difficulty route, deliberately: this is the one artefact a human
        actually reads end to end, and it is a single call.
        """
        if tables.control("closing_report", "runtime", default="on").strip().lower() != "on":
            return
        notes = snapshot.get("notes", {})
        if not snapshot.get("trace"):
            return
        rows = {r["step"]: r for r in tables.flow(self.flow_name)}
        walked = "\n".join(
            f"  {st} · {rows.get(st, {}).get('actor', '?')} · {rows.get(st, {}).get('action', '')[:110]}"
            for st in dict.fromkeys(snapshot.get("trace", []))
        )
        critique = "\n".join(f"  [after {st}] {note}" for st, note in notes.items()) or "  (none - every step read clean)"
        calls = self.sink.of_event("engine_call")
        spent = sum(int((c.get("usage") or {}).get("output_tokens") or 0) for c in calls)
        failed = [c for c in calls if not c.get("ok")]
        denied = [t for t in self.sink.of_event("tool_call") if not t.get("ok")]
        prompt = (
            f"You are the Interface. The run has just ended. Write its closing report — the one document a human "
            f"reads to decide what to change before the next run.\n\n"
            f"THE RUN: flow {self.flow_name}, {summary['distinct_steps']} of {summary['flow_rows']} rows walked, "
            f"terminal {summary.get('terminal')}, {summary['approvals']} human approvals, {len(calls)} model calls, "
            f"{spent} output tokens, {len(failed)} failed calls, {len(denied)} refused tool calls, "
            f"{summary['elapsed_s']}s.\n\n"
            f"THE STEPS THAT RAN:\n{walked}\n\n"
            f"WHAT THE REVIEW SAID BETWEEN STEPS — this is the run's own running critique, use it as evidence:\n"
            f"{critique}\n\n"
            f"Write EXACTLY three sections, in this order:\n"
            f"1. THE FLOW ITSELF — what to change in the flow TABLE: rows that earned nothing, rows that are "
            f"missing, an order that fought the work, a gate that added nothing or one that was needed and absent, "
            f"a loop that never fired. Name step numbers.\n"
            f"2. THE CONTENT — what to change in what the agents actually produced: where the engineering was thin, "
            f"what was asserted without evidence, what was assumed and never checked, which deliverable would not "
            f"survive a reader who knows the subject.\n"
            f"3. THE WHOLE THING — what this run is worth, and EVERY improvement worth making, most valuable first, "
            f"each with what it would cost.\n\n"
            f"Be specific and be blunt. Do not praise. If the run failed to produce something, say what and why."
        )
        # The closing report is ONE call at the end of the whole run and it is the artefact a
        # human reads, so it is worth asking for the seat more than once. It still never
        # substitutes another engine for it (#6881).
        answer, last = None, ""
        for attempt in range(1, 4):
            try:
                answer = self.engine.call(
                    run_id=self.run_id, step="closing", actor="Interface", purpose="closing-report",
                    prompt=prompt, difficulty="high",
                    step_engine=tables.control("engine_for_judgement", "routing", default=""),
                )
                break
            except Exception as exc:
                last = str(exc)[:200]
                self.sink.emit("closing_report", ok=False, attempt=attempt, error=last)
        if answer is None:
            self.sink.emit("closing_report", ok=False, verdict="missed",
                           reason="the Max seat was not free after three tries", error=last)
            return
        text = (answer.get("text") or "").strip()
        if not text:
            self.sink.emit("closing_report", ok=False, error="empty")
            return
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        path = RUN_DIR / f"{self.run_id}-closing-report.md"
        path.write_text(f"# Closing report — {self.run_id} ({self.flow_name})\n\n{text}\n", encoding="utf-8")
        self.sink.emit("closing_report", ok=True, chars=len(text), path=str(path))
        with contextlib.suppress(Exception):
            self.gate.announce(self.run_id, f"Closing report for {self.run_id}: {path}")

    def _drive(self, app, result: dict[str, Any], checkpoint_path: Path | None) -> dict[str, Any]:
        while "__interrupt__" in result:
            if checkpoint_path is not None:
                store.assert_within_ceiling(checkpoint_path)
            payload = result["__interrupt__"][0].value
            request = GateRequest(
                run_id=payload["run_id"],
                step=payload["step"],
                kind=payload["kind"],
                actor=payload["actor"],
                action=payload.get("action", ""),
                output=payload.get("output", ""),
                error=payload.get("error", ""),
                deliverable=payload.get("deliverable", ""),
                deliverable_step=payload.get("deliverable_step", ""),
                deliverable_actor=payload.get("deliverable_actor", ""),
                deliverable_files=list(payload.get("deliverable_files") or []),
            )
            request.shape, request.options, request.question = parse_shape(payload.get("must_answer", ""))
            self.sink.emit("gate_asked", step=request.step, kind=request.kind, actor=request.actor,
                           retry=bool(request.error), deliverable_step=request.deliverable_step,
                           deliverable_actor=request.deliverable_actor,
                           deliverable_chars=len(request.deliverable), shape=request.shape,
                           options=len(request.options))
            with observe.gate_span(run_id=request.run_id, step=request.step, actor=request.actor,
                                   question=request.question or request.action,
                                   deliverable_actor=request.deliverable_actor,
                                   deliverable_step=request.deliverable_step) as span:
                answer = self.gate.ask(request)
                span.update(answer)
            self.sink.emit("gate_answered", step=request.step, kind=request.kind, shape=request.shape, **answer)
            result = app.invoke(Command(resume=answer), self.config)
        return result
