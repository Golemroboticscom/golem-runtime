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
import sys
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


def thread(run_id: str) -> dict[str, Any]:
    """Everything one product's run does, as ONE conversation in the viewer.

    Without a thread id LangSmith shows 48 unrelated traces and you have to reassemble
    the story yourself. With it, the run is a single thread you read top to bottom.
    """
    return {"configurable": {"thread_id": run_id}} if enabled() else {}


@contextlib.contextmanager
def tool_span(*, run_id: str, step: str, actor: str, tool: str, arguments: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """What the agent DID, next to what it said.

    Fifty tool calls in a run against thirty-eight model calls -- and until now only the
    model calls were visible. A trace that shows the thinking and hides the writing is
    half a trace.
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
    # Yields exactly once on every path -- see the note in `llm_span`.
    try:
        span = trace(name=f"{tool} · {actor} · step {step}", run_type="tool",
                     inputs={"tool": tool, "arguments": arguments},
                     metadata={"golem_run_id": run_id, "golem_step": step, "golem_actor": actor,
                               "golem_tool": tool})
        run = span.__enter__()
    except Exception:
        yield slot
        return
    try:
        yield slot
    finally:
        with contextlib.suppress(Exception):
            run.end(outputs=slot or {"result": "no result recorded"})
        with contextlib.suppress(Exception):
            span.__exit__(*sys.exc_info())


@contextlib.contextmanager
def gate_span(*, run_id: str, step: str, actor: str, question: str, deliverable_actor: str,
              deliverable_step: str) -> Iterator[dict[str, Any]]:
    """The human stop: the question that went out, and the decision that came back.

    A gate is the most important thing in a run and it was the only thing not recorded --
    the viewer showed the work stopping and nothing about why or who restarted it.
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
    # Yields exactly once on every path. A GATE is where this mattered most: LangGraph
    # suspends a gate by RAISING out of the node, so the exception is thrown into this
    # generator every single time a gate is asked and not yet answered. The old shape
    # yielded a second time and killed the run (2026-09-01, step 28 of a live 25-step run).
    try:
        span = trace(name=f"GATE {step} · asked {actor}", run_type="chain",
                     inputs={"question": question, "submitted_by": deliverable_actor,
                             "deliverable_from_step": deliverable_step},
                     metadata={"golem_run_id": run_id, "golem_step": step, "golem_gate": True,
                               "golem_actor": actor})
        run = span.__enter__()
    except Exception:
        yield slot
        return
    try:
        yield slot
    finally:
        with contextlib.suppress(Exception):
            run.end(outputs=slot or {"decision": "unanswered"})
            _score(run, slot)
        with contextlib.suppress(Exception):
            span.__exit__(*sys.exc_info())


def _score(run: Any, answer: dict[str, Any]) -> None:
    """Yakov's decision, recorded as a SCORE and not only as text.

    A gate answer is the only human judgement the system ever receives, and as prose it can
    only be read one run at a time. As a score it aggregates: which agents get rejected,
    which steps, how often, and whether that is getting better. `approve` is 1, `reject` is
    0, and what he chose or said rides along as the comment.

    Silent on failure by design -- a scoring call must never be able to affect a gate.
    """
    decision = (answer or {}).get("decision")
    if not decision:
        return
    try:
        from langsmith import Client

        Client().create_feedback(
            run_id=run.id,
            key="human-gate",
            score=1.0 if decision in ("approve", "received") else 0.0,
            value=decision,
            comment=(answer.get("chose") or answer.get("said") or "")[:1000] or None,
            source_info={"actor": answer.get("actor"), "provenance": answer.get("provenance")},
        )
    except Exception:
        pass


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
    # THIS GENERATOR MUST YIELD EXACTLY ONCE, ON EVERY PATH.
    #
    # It used to be wrapped in a `try/except Exception` that yielded a SECOND time when the
    # first yield had raised. A contextmanager that yields again after an exception was
    # thrown into it dies with "generator didn't stop after throw()" -- and on 2026-09-01
    # that killed a live 25-step run at a gate and put its own error where the real one
    # should have been. Watching a run must never break it, and it must never hide it
    # either. So: entering and ending the span are guarded; the BODY is not.
    try:
        span = trace(name=f"{actor} · step {step} · {provider}/{model}", run_type="llm",
                     inputs={"messages": messages}, metadata=metadata)
        run = span.__enter__()
    except Exception:
        yield slot
        return
    try:
        yield slot
    finally:
        usage = slot.get("usage") or {}
        with contextlib.suppress(Exception):
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
        with contextlib.suppress(Exception):
            span.__exit__(*sys.exc_info())
