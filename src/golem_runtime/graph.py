"""The graph, served rather than scripted.

Ruling 14: LangGraph Studio is part of phase A, it attaches to a graph running LOCALLY
through the agent server, and the design consequence is that phase A serves the graph
behind that server instead of running it as a bare script. `langgraph.json` points here.

The server owns the interrupt loop itself, so this module builds the graph with no gate
channel at all: Studio (or any API client) answers the interrupt.
"""
from __future__ import annotations

import os

from .compiler import compile_flow
from .effects import EffectLog
from .engine import EngineWrapper
from .paths import ensure_var_dirs
from .records import RecordSink

DEFAULT_FLOW = os.environ.get("GOLEM_RUNTIME_FLOW", "design-robot")
DEFAULT_TRANSPORT = os.environ.get("GOLEM_RUNTIME_TRANSPORT", "echo")


def build(flow_name: str = DEFAULT_FLOW, transport: str = DEFAULT_TRANSPORT):
    ensure_var_dirs()
    sink = RecordSink(f"served-{flow_name}")
    return compile_flow(flow_name, EngineWrapper(sink, transport=transport), EffectLog()).compile()


graph = build()
