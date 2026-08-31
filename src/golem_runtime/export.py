"""Turn a finished run into something a human can read.

The flow table says `output: file` on 47 of the 48 design-robot rows, and the runtime does
not honour that yet -- the text lives in the effect store and the record, which are the
right places for a machine and the wrong ones for Yakov. This is the bridge between them:
one readable file per step, plus an index, written into the run's product folder.

It reads the run that already happened. It re-runs nothing and calls no engine.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .paths import VAR_DIR
from .records import RecordSink


def export(run_id: str, destination: Path, effects_path: Path | None = None) -> dict[str, object]:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    database = Path(effects_path or VAR_DIR / "effects.sqlite")

    with sqlite3.connect(database) as db:
        rows = db.execute(
            "SELECT step, at, payload FROM effects WHERE run_id=? ORDER BY rowid", (run_id,)
        ).fetchall()

    record = RecordSink(run_id).read()
    done = {r["step"]: r for r in record if r["event"] == "step_done"}
    calls = [r for r in record if r["event"] == "engine_call" and r.get("ok")]
    tools_used = [r for r in record if r["event"] == "tool_call"]

    index = ["# " + run_id, "", f"{len(rows)} steps · {len(calls)} engine calls · {len(tools_used)} tool calls", ""]
    written = []
    for step, at, payload in rows:
        data = json.loads(payload)
        text = data.get("text", "")
        name = f"step-{step.zfill(3) if step.isdigit() else step}.md"
        served = f"{data.get('provider','-')}/{data.get('model','-')}"
        body = [
            f"# step {step}",
            f"*{at}* · engine: {served} · turns: {data.get('turns', 1)}",
            "",
            text,
        ]
        for call in data.get("tool_calls", []) or []:
            body.append(f"\n> tool `{call['tool']}` — {'ok' if call['ok'] else call.get('error')}")
        (destination / name).write_text("\n".join(body) + "\n", encoding="utf-8")
        written.append(name)
        index.append(f"- [{name}]({name}) — {len(text)} chars, {served}")

    (destination / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return {"run_id": run_id, "files": len(written), "destination": str(destination)}
