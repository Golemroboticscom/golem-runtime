"""The state store, behind a seam.

Ruling 6 binds phase one to a swappable store: the graph never names SQLite, it asks this
module for a checkpointer. Replacing SQLite with Postgres in phase two is a new branch of
`open_store` and nothing else.

The size guard lives here too. On 2026-08-31 a runaway loop wrote 90 GB of checkpoint
databases and filled /tmp; `checkpoint_max_mb` in control_values.csv is the ceiling and
this is the only place that enforces it.
"""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from . import tables
from .paths import CHECKPOINT_DIR


class CheckpointTooLarge(RuntimeError):
    """The run's checkpoint store crossed `checkpoint_max_mb`."""


def checkpoint_ceiling_bytes() -> int:
    return tables.control_int("checkpoint_max_mb", "runtime") * 1024 * 1024


def store_size_bytes(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            total += candidate.stat().st_size
    return total


def assert_within_ceiling(path: Path) -> int:
    size = store_size_bytes(path)
    ceiling = checkpoint_ceiling_bytes()
    if size > ceiling:
        raise CheckpointTooLarge(f"checkpoint store {path} is {size} bytes, over the {ceiling}-byte ceiling")
    return size


@contextlib.contextmanager
def open_store(run_id: str, kind: str = "sqlite", directory: Path | None = None) -> Iterator[tuple[object, Path | None]]:
    """Yield `(checkpointer, path)`. `path` is None for stores with no file."""
    if kind == "memory":
        yield InMemorySaver(), None
        return
    if kind != "sqlite":
        raise ValueError(f"unknown state store {kind!r}; phase A has 'sqlite' and 'memory'")
    base = Path(directory or CHECKPOINT_DIR)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{run_id}.sqlite"
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        yield SqliteSaver(conn), path
    finally:
        conn.close()
