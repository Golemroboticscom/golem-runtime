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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tables
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


def route_for(actor: str) -> list[Route]:
    """The routing decision, read from the table. The caller does not get a say."""
    return parse_route(tables.resolve_actor(actor).get("engine", ""))


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

    # NOTE: no `provider`, no `model`, no `engine` parameter. That absence is the iron rule.
    def call(self, *, run_id: str, step: str, actor: str, purpose: str, prompt: str, system: str | None = None) -> dict[str, Any]:
        routes = route_for(actor)
        if not routes:
            raise EngineUnavailable(f"{actor} has no engine route in agents.csv")
        attempts: list[dict[str, Any]] = []
        for index, route in enumerate(routes):
            started = time.time()
            try:
                result = self._perform(route, prompt, system)
                error = None
            except Exception as exc:
                result, error = None, f"{type(exc).__name__}: {exc}"
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
                "prompt_sha256": _digest(prompt),
                "prompt_chars": len(prompt),
                **attempt,
            }
            if result is not None:
                record["usage"] = result.get("usage")
                record["response_chars"] = len(result.get("text", ""))
                record["response_sha256"] = _digest(result.get("text", ""))
                record["provider_response_id"] = result.get("provider_response_id")
            self.sink.emit("engine_call", **record)
            if result is not None:
                return {
                    "text": result.get("text", ""),
                    "provider": route.provider,
                    "model": route.model,
                    "transport": self.transport,
                    "usage": result.get("usage"),
                    "attempts": attempts,
                }
        raise EngineUnavailable(f"every route failed for {actor}: " + "; ".join(f"{a['provider']}/{a['model']}: {a['error']}" for a in attempts))

    def _perform(self, route: Route, prompt: str, system: str | None) -> dict[str, Any]:
        if self.transport == "echo":
            # Deterministic, offline, no credential anywhere. Used by the suite and by dry runs.
            return {
                "text": f"echo[{route}] {_digest(prompt)[:16]}",
                "usage": {"input_tokens": len(prompt) // 4, "output_tokens": 8},
                "provider_response_id": None,
            }
        assert self.client is not None
        answer = self.client.complete(route.provider, route.model, prompt, system, self.timeout)
        if not answer.get("ok"):
            raise RuntimeError(answer.get("error", "bridge refused the call"))
        return answer


def call_signature_forbids_model() -> bool:
    """The iron rule, checkable. Imported by the tests and by the push gates."""
    forbidden = {"model", "provider", "engine", "route"}
    return not (forbidden & set(inspect.signature(EngineWrapper.call).parameters))
