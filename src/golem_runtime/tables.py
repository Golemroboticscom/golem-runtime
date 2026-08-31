"""The definitions, loaded from the tables that own them.

Ruling 4: one source of truth per definition. The flow lives in flow.csv, the agents in
agents.csv, the tunable numbers in control_values.csv. Nothing in this package hard-codes
a value that one of those tables already carries, and nothing re-declares a definition a
table already holds.
"""
from __future__ import annotations

import csv
import functools
from pathlib import Path

from .paths import TABLES_DIR

FLOW_CSV = TABLES_DIR / "flow.csv"
AGENTS_CSV = TABLES_DIR / "agents.csv"
PARAMS_CSV = TABLES_DIR / "flow_params.csv"
CONTROLS_CSV = TABLES_DIR / "control_values.csv"
TOOLS_CSV = TABLES_DIR / "tools.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return [{k: (v or "").strip() for k, v in row.items() if k is not None} for row in csv.DictReader(handle)]


@functools.lru_cache(maxsize=None)
def _flow_rows() -> tuple[dict[str, str], ...]:
    return tuple(read_csv(FLOW_CSV))


@functools.lru_cache(maxsize=None)
def _agent_rows() -> tuple[dict[str, str], ...]:
    return tuple(read_csv(AGENTS_CSV))


@functools.lru_cache(maxsize=None)
def _param_rows() -> tuple[dict[str, str], ...]:
    return tuple(read_csv(PARAMS_CSV))


@functools.lru_cache(maxsize=None)
def _control_rows() -> tuple[dict[str, str], ...]:
    return tuple(read_csv(CONTROLS_CSV))


@functools.lru_cache(maxsize=None)
def _tool_rows() -> tuple[dict[str, str], ...]:
    return tuple(read_csv(TOOLS_CSV))


def reload() -> None:
    """Drop the cached tables. Used by the tests and by anything that edits a table."""
    for cached in (_flow_rows, _agent_rows, _param_rows, _control_rows, _tool_rows):
        cached.cache_clear()


def flow_names() -> list[str]:
    return sorted({row["flow_name"] for row in _flow_rows()})


def flow(flow_name: str) -> list[dict[str, str]]:
    rows = [dict(row) for row in _flow_rows() if row["flow_name"] == flow_name]
    if not rows:
        raise KeyError(f"no flow named {flow_name!r} in {FLOW_CSV}")
    return rows


def agents() -> list[dict[str, str]]:
    return [dict(row) for row in _agent_rows()]


def declared_params(flow_name: str) -> set[str]:
    return {row["param"] for row in _param_rows() if row["flow_name"] in {flow_name, "any"}}


def tools() -> list[dict[str, str]]:
    return [dict(row) for row in _tool_rows()]


def control(name: str, branch: str | None = None, default: str | None = None) -> str:
    """One tunable number, read from control_values.csv rather than written in code."""
    matches = [r for r in _control_rows() if r["control"] == name and (branch is None or r["branch"] == branch)]
    if not matches:
        if default is not None:
            return default
        raise KeyError(f"no control value named {name!r}" + (f" on branch {branch!r}" if branch else ""))
    return matches[0]["value"]


def control_int(name: str, branch: str | None = None, default: int | None = None) -> int:
    raw = control(name, branch, None if default is None else str(default))
    return int(str(raw).strip())


def resolve_actor(actor: str) -> dict[str, str]:
    """Turn a flow row's `actor` into exactly one row of agents.csv.

    The flow names roles ("Interface", "Engineering Lead", "Yakov"); agents.csv names
    identities ("Interface Lead", "— (human owner)"). An exact agent name wins. Failing
    that the actor is read as a team, and the team's lowest id is its lead. Anything that
    does not resolve to exactly one row is a preflight error, never a runtime surprise.
    """
    rows = _agent_rows()
    exact = [r for r in rows if r["agent"] == actor]
    if len(exact) == 1:
        return dict(exact[0])
    if len(exact) > 1:
        raise LookupError(f"actor {actor!r} matches {len(exact)} agent rows by name")
    team = [r for r in rows if r["team"] == actor]
    if not team:
        raise LookupError(f"actor {actor!r} matches no agent and no team in agents.csv")
    lead = min(team, key=lambda r: int(r["id"]))
    return dict(lead)
