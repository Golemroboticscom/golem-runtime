"""Golem Runtime — the lean rebuild (GOL-384, branch runtime-v3-langgraph).

The flow and the agent loop run on LangGraph, the definitions stay in tables, each agent
runs in its own rootless container, and Telegram is the surface Yakov approves from.

This package is a TEST, not a decision (ruling 1). The old system keeps running beside it.
"""
__version__ = "0.1.0"
