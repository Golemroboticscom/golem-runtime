"""Running a flow: start it, carry its questions to the surface, bring the answers back.

The runner owns the outside of the graph. The graph stops itself with an interrupt; the
runner is what turns that stop into a Telegram message and the reply into a resume. It
also holds the two ceilings that keep a bad run from eating the machine: the step ceiling
on every invoke, and the checkpoint size check between them.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from langgraph.types import Command

from . import store, tables
from .compiler import RunState, compile_flow
from .effects import EffectLog
from .engine import EngineWrapper
from .gates import AutoGate, GateChannel, GateRequest
from .paths import RUN_DIR, ensure_var_dirs
from .records import RecordSink
from .validate import validate_flow


def thread_id(run_id: str) -> str:
    return f"{run_id}-{hashlib.sha256(run_id.encode()).hexdigest()[:12]}"


def run_config(run_id: str) -> dict[str, Any]:
    """The step ceiling is not decoration: it is the backstop above every loop_ceiling."""
    return {
        "configurable": {"thread_id": thread_id(run_id)},
        "recursion_limit": tables.control_int("run_step_ceiling", "runtime"),
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
        self.config = run_config(run_id)

    def execute(self, params: dict[str, str] | None = None, resume: bool = False, **extra: Any) -> dict[str, Any]:
        validation = validate_flow(self.flow_name, params)
        self.sink.emit(
            "run_start",
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
        return summary

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
            )
            self.sink.emit("gate_asked", step=request.step, kind=request.kind, actor=request.actor, retry=bool(request.error))
            answer = self.gate.ask(request)
            self.sink.emit("gate_answered", step=request.step, kind=request.kind, **answer)
            result = app.invoke(Command(resume=answer), self.config)
        return result
