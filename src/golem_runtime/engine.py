"""The ONE engine wrapper.

Two rules meet in this file and both are absolute.

**The iron rule.** The agent engine never asks for a specific model. `EngineWrapper.call`
takes no provider and no model argument, by construction: the route comes from the actor's
row in agents.csv and from nowhere else. A test asserts the signature, because the rule is
a prohibition and not a convention.

**Ruling 15.** Every engine call goes through this wrapper and no agent calls a provider
directly. Where the wrapper sends the request is a VALUE -- `transport` below -- which is
what makes swapping bridge for secret-inside cheap later.

Ruling 6 rides along: every call emits a structured record whether it succeeded or failed,
from the first commit, even while nothing displays it.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import observe, tables
from .records import RecordSink
from .secrets_bridge import BridgeClient


class EngineUnavailable(RuntimeError):
    """Every route on the actor's row failed."""


@dataclass(frozen=True)
class Route:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}"


def parse_route(spec: str) -> list[Route]:
    """`anthropic/claude-opus-5>openai/gpt-5.4-mini` -> preference order, first is primary."""
    routes: list[Route] = []
    for token in (spec or "").split(">"):
        token = token.strip()
        if not token:
            continue
        provider, _, model = token.partition("/")
        if not provider or not model:
            raise ValueError(f"malformed engine route {token!r}; expected provider/model")
        routes.append(Route(provider.strip(), model.strip()))
    return routes


def route_for(actor: str, step_engine: str = "") -> list[Route]:
    """The routing decision, read from the tables. The caller does not get a say.

    Two places may hold it, and the more specific wins:

      1. the `engine` column of the STEP's row in flow.csv -- empty on every row by default
      2. the `engine` column of the ACTOR's row in agents.csv

    That is the whole of it. `parse_route` already understood `a/b>c/d` cascades and
    `EngineWrapper.call` already walked them; this adds one lookup and no new machinery.
    A step that names an engine still names it in a TABLE, which is the routing layer
    speaking -- not the agent asking, which is what the iron rule forbids.
    """
    routes = parse_route(step_engine)
    return routes if routes else parse_route(tables.resolve_actor(actor).get("engine", ""))


def _as_text(prompt: Any) -> str:
    """A prompt is either one string or a whole conversation. Both have to be measurable."""
    if isinstance(prompt, str):
        return prompt
    return json.dumps(prompt, ensure_ascii=False, sort_keys=True, default=str)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EngineWrapper:
    """The single door to every model call in the runtime."""

    def __init__(self, sink: RecordSink, transport: str = "echo", socket_path: Path | None = None, timeout: float | None = None):
        if transport not in {"echo", "bridge"}:
            raise ValueError(f"unknown transport {transport!r}; phase A has 'echo' and 'bridge'")
        self.sink = sink
        self.transport = transport
        self.client = BridgeClient(socket_path) if transport == "bridge" else None
        self.timeout = float(timeout if timeout is not None else tables.control_int("engine_timeout_seconds", "runtime"))
        # Which provider last actually SERVED each actor. Rotating the row's order was not
        # enough: the row's first route is unserved in phase A, so a rotation handed back the
        # very same provider and a "second opinion" was the same engine twice.
        self._last_served: dict[str, str] = {}

    # NOTE: no `provider`, no `model`, no `engine` parameter. That absence is the iron rule.
    #
    # `prefer_alternate` is NOT an exception to it. It asks for a DIFFERENT answer than the
    # primary route would give -- which is what a second-engine crosscheck means -- and the
    # routing layer still decides which route that is. The caller names nothing.
    def call(
        self,
        *,
        run_id: str,
        step: str,
        actor: str,
        purpose: str,
        prompt: Any,
        system: str | None = None,
        image: str | None = None,
        tool_declarations: list[dict[str, Any]] | None = None,
        prefer_alternate: bool = False,
        step_engine: str = "",
    ) -> dict[str, Any]:
        routes = route_for(actor, step_engine)
        if not routes:
            raise EngineUnavailable(f"{actor} has no engine route in agents.csv")
        if prefer_alternate:
            served = self._last_served.get(actor)
            if served:
                routes = [r for r in routes if r.provider != served] + [r for r in routes if r.provider == served]
        attempts: list[dict[str, Any]] = []
        for index, route in enumerate(routes):
            started = time.time()
            # The span is the ONLY instrumentation in the system. Our calls never pass
            # through LangChain, so nothing would report them otherwise; and the iron rule
            # -- this wrapper is the one way to reach a model -- guarantees one place is
            # enough, for ever. If tracing is off it costs a function call (#6739).
            with observe.llm_span(
                run_id=run_id, step=step, actor=actor, purpose=purpose,
                provider=route.provider, model=route.model,
                prompt=_as_text(prompt), system=system,
                tools=[t.get("name") or t.get("type") for t in (tool_declarations or [])],
            ) as span:
                try:
                    result = self._perform(route, prompt, system, tool_declarations, image)
                    error = None
                    span["text"] = result.get("text", "")
                    span["usage"] = result.get("usage") or {}
                except Exception as exc:
                    result, error = None, f"{type(exc).__name__}: {exc}"
                    span["text"] = f"[route failed] {error}"
            elapsed_ms = int((time.time() - started) * 1000)
            attempt = {
                "attempt": index + 1,
                "provider": route.provider,
                "model": route.model,
                "ok": error is None,
                "error": error,
                "elapsed_ms": elapsed_ms,
            }
            attempts.append(attempt)
            record = {
                "run_id": run_id,
                "step": step,
                "actor": actor,
                "purpose": purpose,
                "transport": self.transport,
                "route": [str(r) for r in routes],
                "prompt_sha256": _digest(_as_text(prompt)),
                "prompt_chars": len(_as_text(prompt)),
                "has_image": bool(image),
                "tools_offered": [t.get("name") or t.get("type") for t in (tool_declarations or [])],
                **attempt,
            }
            if result is not None:
                record["usage"] = result.get("usage")
                record["response_chars"] = len(result.get("text", ""))
                record["response_sha256"] = _digest(result.get("text", ""))
                record["provider_response_id"] = result.get("provider_response_id")
            self.sink.emit("engine_call", **record)
            if result is not None:
                if not prefer_alternate:
                    self._last_served[actor] = route.provider
                return {
                    "text": result.get("text", ""),
                    "provider": route.provider,
                    "model": route.model,
                    "transport": self.transport,
                    "usage": result.get("usage"),
                    "attempts": attempts,
                    "output": result.get("output", []),
                }
        raise EngineUnavailable(f"every route failed for {actor}: " + "; ".join(f"{a['provider']}/{a['model']}: {a['error']}" for a in attempts))

    def _perform(self, route: Route, prompt: Any, system: str | None,
                 tool_declarations: list[dict[str, Any]] | None = None, image: str | None = None) -> dict[str, Any]:
        text = _as_text(prompt)
        if self.transport == "echo":
            # Deterministic, offline, no credential anywhere. Used by the suite and by dry runs.
            return {
                "text": f"echo[{route}] {_digest(text)[:16]}",
                "usage": {"input_tokens": len(text) // 4, "output_tokens": 8},
                "provider_response_id": None,
                "output": [],
            }
        assert self.client is not None
        answer = self.client.complete(route.provider, route.model, prompt, system, self.timeout, tool_declarations, image)
        if not answer.get("ok"):
            raise RuntimeError(answer.get("error", "bridge refused the call"))
        return answer

    # ------------------------------------------------------------------ the agent loop

    def work(self, *, run_id: str, step: str, actor: str, purpose: str, prompt: str,
             params: dict[str, str] | None = None, system: str | None = None,
             step_engine: str = "") -> dict[str, Any]:
        """One agent step that may actually WORK, not just answer once.

        A step with no tools on its row is a single call and this is exactly `call`.
        A step WITH tools goes round a loop -- think, ask for a tool, read the result,
        think again -- until it answers or reaches `agent_loop_max_turns`. The ceiling is
        never silent: reaching it is a record.
        """
        from . import tools as toolbox

        specs = toolbox.granted(actor)
        if not specs:
            answer = self.call(run_id=run_id, step=step, actor=actor, purpose=purpose, prompt=prompt,
                               system=system, step_engine=step_engine)
            return {**answer, "turns": 1, "tool_calls": []}

        declarations = [spec.declaration() for spec in specs]
        context = toolbox.ToolContext(run_id=run_id, step=step, actor=actor, params=dict(params or {}), engine=self)
        conversation: list[Any] = [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]
        ceiling = tables.control_int("agent_loop_max_turns", "runtime")
        performed: list[dict[str, Any]] = []
        answer: dict[str, Any] = {}

        for turn in range(1, ceiling + 1):
            answer = self.call(
                run_id=run_id, step=step, actor=actor, purpose=f"{purpose}:turn-{turn}",
                prompt=conversation, system=system, tool_declarations=declarations,
                step_engine=step_engine,
            )
            requests = [item for item in answer.get("output", []) if item.get("type") == "function_call"]
            conversation.extend(answer.get("output", []))
            if not requests:
                return {**answer, "turns": turn, "tool_calls": performed}
            for request in requests:
                name = request.get("name", "")
                try:
                    arguments = json.loads(request.get("arguments") or "{}")
                except ValueError:
                    arguments = {}
                started = time.time()
                with observe.tool_span(run_id=run_id, step=step, actor=actor, tool=name,
                                       arguments={k: str(v)[:400] for k, v in arguments.items()}) as span:
                    result = toolbox.run_tool(name, arguments, context)
                    span.update({k: str(v)[:2000] for k, v in (result or {}).items()})
                record = {
                    "tool": name,
                    "arguments": {k: str(v)[:200] for k, v in arguments.items()},
                    "ok": "error" not in result,
                    "error": result.get("error"),
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "turn": turn,
                }
                # The path a Write actually produced. Without it a gate can only attach the
                # agent's ANSWER, and when the agent writes a file its answer is a pointer --
                # which is how Yakov received a 108-byte attachment holding a link while the
                # 15 KB of research sat on disk (#6628).
                if isinstance(result, dict) and result.get("written"):
                    record["wrote"] = result["written"]
                performed.append(record)
                self.sink.emit("tool_call", run_id=run_id, step=step, actor=actor, **record)
                conversation.append({
                    "type": "function_call_output",
                    "call_id": request.get("call_id"),
                    "output": toolbox.as_json(result),
                })

        self.sink.emit("agent_loop_ceiling", run_id=run_id, step=step, actor=actor, ceiling=ceiling, tool_calls=len(performed))
        return {**answer, "turns": ceiling, "tool_calls": performed, "ceiling_reached": True}


def call_signature_forbids_model() -> bool:
    """The iron rule, checkable. Imported by the tests and by the push gates."""
    forbidden = {"model", "provider", "engine", "route"}
    return not (forbidden & set(inspect.signature(EngineWrapper.call).parameters))
