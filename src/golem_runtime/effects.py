"""The idempotent side-effect boundary.

A checkpointed graph replays a node after a crash. Without a boundary that replay calls the
model again, sends the outbound message again and pays twice. Every effect is written here
under a key derived from the run and the step, and a replay reads the stored payload back
instead of performing it.

It is deliberately a separate store from the checkpointer: the state can be rebuilt, an
effect that already left the building cannot.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .paths import VAR_DIR
from .records import utc_now


class EffectLog:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or VAR_DIR / "effects.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS effects ("
                "effect_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, step TEXT NOT NULL, "
                "at TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def once(self, effect_key: str, run_id: str, step: str, perform: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        """Perform the effect exactly once per key. Returns `(payload, performed_now)`."""
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM effects WHERE effect_key=?", (effect_key,)).fetchone()
        if row:
            return json.loads(row[0]), False
        payload = perform()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO effects(effect_key,run_id,step,at,payload) VALUES (?,?,?,?,?)",
                (effect_key, run_id, step, utc_now(), encoded),
            )
            stored = db.execute("SELECT payload FROM effects WHERE effect_key=?", (effect_key,)).fetchone()
        return json.loads(stored[0]), True

    def count(self, run_id: str | None = None) -> int:
        with sqlite3.connect(self.path) as db:
            if run_id is None:
                return int(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0])
            return int(db.execute("SELECT COUNT(*) FROM effects WHERE run_id=?", (run_id,)).fetchone()[0])
