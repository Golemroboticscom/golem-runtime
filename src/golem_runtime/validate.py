"""Preflight: everything that can be wrong with a flow is found before it runs.

A flow row that names a step that does not exist, an actor that resolves to no agent, a
loop without a ceiling or a parameter nobody declared is a table error. It is caught here,
once, at load time -- never discovered halfway through a run with twelve gates behind it.
"""
from __future__ import annotations

import re
from typing import Any

from . import tables

SUPPORTED_KINDS = {"agent-step", "human-gate", "wait-external", "outbound-send", "script-step"}
TERMINAL_PREFIX = "END:"
PARAM_RE = re.compile(r"\$\{[^}]+\}")


class FlowInvalid(ValueError):
    """The flow table does not describe a runnable graph."""


def ref_to_step(value: str, flow_name: str | None = None) -> str:
    """`design-robot:12` -> `12` inside design-robot. A reference to ANOTHER flow keeps its
    prefix, because it is an exit from this graph and not a node in it."""
    value = (value or "").strip()
    prefix, sep, rest = value.partition(":")
    if not sep or prefix == "END":
        return value
    if flow_name is None or prefix == flow_name:
        return rest
    return value


def split_targets(value: str, flow_name: str | None = None) -> list[str]:
    return [ref_to_step(x, flow_name) for x in re.split(r"\s*(?:\||/)\s*", (value or "").strip()) if x.strip()]


def is_exit(target: str, flow_name: str) -> bool:
    """A target that leaves this graph: a terminal, or a step of a different flow."""
    if target.startswith(TERMINAL_PREFIX):
        return True
    prefix, sep, _ = target.partition(":")
    return bool(sep) and prefix != flow_name


def required_params(rows: list[dict[str, str]]) -> set[str]:
    return {p for row in rows for p in PARAM_RE.findall(row.get("input", ""))}


def terminals(rows: list[dict[str, str]]) -> set[str]:
    found = set()
    for row in rows:
        for target in split_targets(row.get("next", "")) + split_targets(row.get("loop_back_to", "")):
            if target.startswith(TERMINAL_PREFIX):
                found.add(target)
    return found


def validate_flow(flow_name: str, runtime_params: dict[str, str] | None = None, expect_rows: int | None = None) -> dict[str, Any]:
    rows = tables.flow(flow_name)
    declared = tables.declared_params(flow_name)
    runtime_params = runtime_params if runtime_params is not None else {}
    errors: list[str] = []

    ids = [row["step"] for row in rows]
    known = set(ids)
    if len(known) != len(ids):
        errors.append("duplicate step IDs")
    if expect_rows is not None and len(rows) != expect_rows:
        errors.append(f"expected {expect_rows} rows, got {len(rows)}")

    ends = terminals(rows)
    for row in rows:
        step = row["step"]
        if row["kind"] not in SUPPORTED_KINDS:
            errors.append(f"{step}: unsupported kind {row['kind']!r}")
        if PARAM_RE.fullmatch(row["actor"]):
            pass  # the actor is a run parameter: F2-single is dispatched to whoever the run names
        else:
            try:
                tables.resolve_actor(row["actor"])
            except LookupError as exc:
                errors.append(f"{step}: {exc}")
        for target in split_targets(row.get("next", ""), flow_name):
            if target in known:
                continue
            if target.startswith(TERMINAL_PREFIX):
                continue
            other, _, other_step = target.partition(":")
            if other and other_step:
                try:
                    if other_step in {r["step"] for r in tables.flow(other)}:
                        continue
                except KeyError:
                    pass
            errors.append(f"{step}: invalid next target {target!r}")
        loops = split_targets(row.get("loop_back_to", ""), flow_name)
        for target in loops:
            if target not in known:
                errors.append(f"{step}: invalid loop target {target!r}")
        if loops:
            ceiling = row.get("loop_ceiling", "")
            if not ceiling.isdigit() or int(ceiling) < 1:
                errors.append(f"{step}: loop ceiling must be a positive integer, got {ceiling!r}")

    needed = required_params(rows)
    undeclared = needed - declared
    if undeclared:
        errors.append(f"undeclared parameters: {sorted(undeclared)}")
    if runtime_params:
        missing = needed - set(runtime_params)
        if missing:
            errors.append(f"missing runtime parameters: {sorted(missing)}")

    # Reachability over every declared edge, and proof that a terminal can be reached.
    adjacency = {
        row["step"]: split_targets(row.get("next", ""), flow_name) + split_targets(row.get("loop_back_to", ""), flow_name)
        for row in rows
    }
    # An EXIT is a door out of a step, not a step that follows it, so no `next` points at one
    # and reachability could never see it (Yakov #6834; F2-single's five E-rows were reported
    # unreachable for exactly this reason). `exit_from` is where that edge now lives: the exit
    # row names the step it may leave, and the edge is read in the direction the run travels.
    for row in rows:
        for source in split_targets(row.get("exit_from", ""), flow_name):
            if source not in known:
                errors.append(f"{row['step']}: exit_from names an unknown step {source!r}")
            else:
                adjacency.setdefault(source, []).append(row["step"])
    seen: set[str] = set()
    stack = [ids[0]] if ids else []
    terminal_seen = False
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for target in adjacency.get(node, []):
            if is_exit(target, flow_name):
                terminal_seen = True
            elif target in known:
                stack.append(target)
    if seen != known:
        errors.append(f"unreachable steps: {sorted(known - seen)}")
    if not terminal_seen:
        errors.append("no terminal reachable")

    if errors:
        raise FlowInvalid("; ".join(errors))

    kinds: dict[str, int] = {}
    for row in rows:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    return {
        "flow": flow_name,
        "rows": len(rows),
        "entry": ids[0],
        "kinds": kinds,
        "terminals": sorted(ends),
        "actors": sorted({row["actor"] for row in rows}),
        "required_params": sorted(needed),
    }


def validate_all_flows() -> dict[str, Any]:
    """Every flow the table defines, each with its own verdict.

    One invalid flow does not hide the others: the report carries a per-flow error instead
    of raising, so a check can say exactly which flow is broken.
    """
    report: dict[str, Any] = {}
    for name in tables.flow_names():
        if name == "any":
            continue
        try:
            report[name] = validate_flow(name)
        except FlowInvalid as exc:
            report[name] = {"flow": name, "error": str(exc)}
    return report
