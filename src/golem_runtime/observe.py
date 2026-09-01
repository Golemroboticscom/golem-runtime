"""Watching a run from the outside: LangSmith, and nothing of ours to maintain.

Yakov's constraint (#6724): **a surface we build is a surface we maintain, and he does not
want that.** So the viewer is the vendor's own -- `smith.langchain.com`, a website with a
login -- and our whole contribution is this file.

Two halves, and both are small:

* **The flow.** LangGraph is built on langchain-core runnables, so once `LANGSMITH_TRACING`
  is on, every node of the graph reports itself with no code at all. The 48 steps, their
  order, their timing, the state going in and coming out, where a gate stopped it.
* **The model calls.** These are OURS -- our router picks the route, our secret bridge
  performs the HTTP -- so LangChain never sees them and nothing would report them. This
  module opens one span around each attempt, carrying the prompt, the answer, the model and
  the token counts. That is the entire instrumentation, and by the iron rule (the engine
  wrapper is the only way to reach a model) it can never need a second copy.

Confidentiality is NOT a consideration here, and that is a ruling, not an omission: Yakov,
#6730 -- "I have no problem with information leaving to an insured system, or with it
staying there a long time... there is a question of stability, of flexibility, of
permissions for agents so they do not change files, and of control. Those are the important
things." Judged on those four this costs nothing: tracing is fire-and-forget, so a LangSmith
outage cannot stop a run; it observes and never decides, so it touches no permission and no
routing decision.
"""
from __future__ import annotations

import contextlib
import json
import os
from typing import Any, Iterator

from .paths import SECRETS_DIR

PROVIDERS_JSON = SECRETS_DIR / "providers.json"
_STATE: dict[str, Any] = {"ready": None}


def configure() -> bool:
    """Put the key in the environment, once. Returns whether tracing is on.

    The key lives with the other credentials in `secrets/providers.json`, which is the
    holder ruling 15 already established; it is never a literal here and never in git.
    """
    if _STATE["ready"] is not None:
        return bool(_STATE["ready"])
    _STATE["ready"] = False
    try:
        block = json.loads(PROVIDERS_JSON.read_text(encoding="utf-8")).get("langsmith") or {}
    except Exception:
        return False
    key = str(block.get("api_key") or "").strip()
    if not key:
        return False
    os.environ.setdefault("LANGSMITH_API_KEY", key)
    os.environ.setdefault("LANGSMITH_ENDPOINT", str(block.get("endpoint") or "https://api.smith.langchain.com"))
    os.environ.setdefault("LANGSMITH_PROJECT", str(block.get("project") or "golem-runtime"))
    os.environ["LANGSMITH_TRACING"] = "true"
    _STATE["ready"] = True
    return True


def enabled() -> bool:
    return configure() and os.environ.get("LANGSMITH_TRACING") == "true"


@contextlib.contextmanager
def llm_span(*, run_id: str, step: str, actor: str, purpose: str, provider: str, model: str,
             prompt: str, system: str | None, tools: list[str] | None) -> Iterator[dict[str, Any]]:
    """One model call, as LangSmith understands one: messages in, a message out.

    Yields a dict the caller fills with `text` and `usage`; whatever is in it when the block
    ends becomes the span's output. A failure inside the block is recorded and re-raised --
    a route that failed is exactly what you want to SEE, since our cascade tries the next
    one and the run carries on.

    If tracing is off, or LangSmith itself misbehaves, this is a plain no-op. Watching a run
    must never be able to break it.
    """
    slot: dict[str, Any] = {}
    if not enabled():
        yield slot
        return
    try:
        from langsmith import trace
    except Exception:
        yield slot
        return

    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    metadata = {
        "ls_provider": provider,
        "ls_model_name": model,
        "golem_run_id": run_id,
        "golem_step": step,
        "golem_actor": actor,
        "golem_purpose": purpose,
        "golem_tools_offered": tools or [],
    }
    try:
        with trace(name=f"{actor} · step {step} · {provider}/{model}", run_type="llm",
                   inputs={"messages": messages}, metadata=metadata) as run:
            try:
                yield slot
            finally:
                usage = slot.get("usage") or {}
                run.end(
                    outputs={
                        "choices": [{"message": {"role": "assistant", "content": slot.get("text", "")}}],
                        "usage_metadata": {
                            "input_tokens": usage.get("input_tokens", 0),
                            "output_tokens": usage.get("output_tokens", 0),
                            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                        },
                    }
                )
    except Exception:
        # Already yielded? Then the body ran and only the reporting failed -- say nothing.
        if slot:
            return
        yield slot
