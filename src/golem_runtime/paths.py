"""Every path the runtime uses, in one place.

Nothing here points into /srv/golem (ruling 3): the runtime is a separate tree with its
own tables, its own state and its own secrets. The only way to move it is the
GOLEM_RUNTIME_ROOT environment variable, which is what the container mount does.
"""
from __future__ import annotations

import os
from pathlib import Path

RUNTIME_ROOT = Path(os.environ.get("GOLEM_RUNTIME_ROOT", "/srv/runtime")).resolve()

TABLES_DIR = Path(os.environ.get("GOLEM_RUNTIME_TABLES", RUNTIME_ROOT / "tables"))
VAR_DIR = Path(os.environ.get("GOLEM_RUNTIME_VAR", RUNTIME_ROOT / "var"))
ARTIFACTS_DIR = Path(os.environ.get("GOLEM_RUNTIME_ARTIFACTS", RUNTIME_ROOT / "artifacts"))
SECRETS_DIR = Path(os.environ.get("GOLEM_RUNTIME_SECRETS", RUNTIME_ROOT / "secrets"))

CHECKPOINT_DIR = VAR_DIR / "checkpoints"
RECORD_DIR = VAR_DIR / "records"
RUN_DIR = VAR_DIR / "runs"
# Inside a container the socket arrives at a mounted path, announced by the launcher.
SECRET_SOCKET = Path(os.environ.get("GOLEM_SECRET_SOCKET", VAR_DIR / "secrets.sock"))

RULINGS_CSV = RUNTIME_ROOT / "rulings.csv"


def ensure_var_dirs() -> None:
    for path in (VAR_DIR, CHECKPOINT_DIR, RECORD_DIR, RUN_DIR, ARTIFACTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
