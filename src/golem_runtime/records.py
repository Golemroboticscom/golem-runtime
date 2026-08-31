"""The structured record, written from day one.

Ruling 6: a run-viewer UI may be missing in phase one, but every engine call emits a
structured record from the first commit even while nothing displays it. This module is
that record. It is append-only JSONL, one file per run, and it is deliberately dumb: a
viewer built in phase two reads these files and needs nothing from the runtime.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import RECORD_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RecordSink:
    """Append-only structured record for one run."""

    def __init__(self, run_id: str, directory: Path | None = None):
        self.run_id = run_id
        self.directory = Path(directory or RECORD_DIR)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{run_id}.jsonl"
        self._lock = threading.Lock()
        # A resumed run appends to the same file; the sequence continues rather than restarts.
        self._seq = sum(1 for _ in self.path.open(encoding="utf-8")) if self.path.exists() else 0

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        """`event` names the record; every other field is the record's own. The name is
        `event` and not `kind` on purpose: `kind` is the flow table's word for a step's
        type, and several records carry both."""
        with self._lock:
            self._seq += 1
            record = {"seq": self._seq, "at": utc_now(), "run_id": self.run_id, "event": event, "pid": os.getpid()}
            record.update(fields)
            line = json.dumps(record, ensure_ascii=False, sort_keys=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return record

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def of_event(self, event: str) -> list[dict[str, Any]]:
        return [r for r in self.read() if r["event"] == event]
