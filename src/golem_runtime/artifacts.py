"""Heavy files live on the host; the system keeps the pointer.

Ruling 8: CAD output, renders and any large binary go to a directory on the host with a
backup, and what the run carries is the pointer and the metadata -- never the bytes. The
file-size gate in `checks/` is what stops one slipping into a commit; `artifact_max_mb`
is what stops one slipping into the store.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
from pathlib import Path
from typing import Any

from . import tables
from .paths import ARTIFACTS_DIR
from .records import utc_now


class ArtifactTooLarge(ValueError):
    """Bigger than `artifact_max_mb`."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def max_bytes() -> int:
    return tables.control_int("artifact_max_mb", "runtime") * 1024 * 1024


def store(source: Path, run_id: str, step: str, kind: str = "output", root: Path | None = None) -> dict[str, Any]:
    """Move a heavy file into the artifact store and return its pointer."""
    source = Path(source)
    size = source.stat().st_size
    if size > max_bytes():
        raise ArtifactTooLarge(f"{source} is {size} bytes, over the {max_bytes()}-byte artifact ceiling")
    base = Path(root or ARTIFACTS_DIR) / run_id
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"{step}-{source.name}"
    shutil.copy2(source, target)
    pointer = {
        "uri": f"file://{target}",
        "run_id": run_id,
        "step": step,
        "kind": kind,
        "filename": source.name,
        "bytes": size,
        "sha256": _sha256(target),
        "media_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
        "stored_at": utc_now(),
    }
    target.with_suffix(target.suffix + ".pointer.json").write_text(
        json.dumps(pointer, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return pointer


def pointers(run_id: str, root: Path | None = None) -> list[dict[str, Any]]:
    base = Path(root or ARTIFACTS_DIR) / run_id
    if not base.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(base.glob("*.pointer.json"))]
