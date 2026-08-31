"""Every path the runtime uses, in one place.

Nothing here points into /srv/golem (ruling 3): the runtime is a separate tree with its
own tables, its own state and its own secrets. The only way to move it is the
GOLEM_RUNTIME_ROOT environment variable, which is what the container mount does.

**And that variable must be ABSOLUTE.** On 2026-08-31 the GOL-291 session found an empty
`artifacts/toolproof` directory inside `/srv/golem`, owned by `interface-lead`. The cause
was this file: a relative `GOLEM_RUNTIME_ROOT` was resolved with `.resolve()`, which
resolves against the current working directory -- and the working directory was
`/srv/golem`. The separation this module's own docstring promises was broken by an
environment variable, not by the code's intent.

So a relative override is now refused outright rather than resolved. A path that depends
on where a process happened to be standing is not a path; it is a coin toss, and the side
it lands on gets written under someone else's name.
"""
from __future__ import annotations

import os
from pathlib import Path


class RuntimePathInvalid(ValueError):
    """A path override was set to something relative. There is no safe way to guess."""


def _absolute(variable: str, default: str | Path) -> Path:
    """Read a path override. Absolute or nothing -- never resolved against the cwd."""
    raw = os.environ.get(variable)
    if raw is None or not raw.strip():
        return Path(default)
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        raise RuntimePathInvalid(
            f"{variable}={raw!r} is relative. It would be resolved against the current "
            f"working directory ({Path.cwd()}), which is how the runtime once wrote into "
            f"/srv/golem. Set it to an absolute path or leave it unset."
        )
    return candidate


RUNTIME_ROOT = _absolute("GOLEM_RUNTIME_ROOT", "/srv/runtime")

TABLES_DIR = _absolute("GOLEM_RUNTIME_TABLES", RUNTIME_ROOT / "tables")
VAR_DIR = _absolute("GOLEM_RUNTIME_VAR", RUNTIME_ROOT / "var")
ARTIFACTS_DIR = _absolute("GOLEM_RUNTIME_ARTIFACTS", RUNTIME_ROOT / "artifacts")
SECRETS_DIR = _absolute("GOLEM_RUNTIME_SECRETS", RUNTIME_ROOT / "secrets")

CHECKPOINT_DIR = VAR_DIR / "checkpoints"
RECORD_DIR = VAR_DIR / "records"
RUN_DIR = VAR_DIR / "runs"
# Inside a container the socket arrives at a mounted path, announced by the launcher.
SECRET_SOCKET = _absolute("GOLEM_SECRET_SOCKET", VAR_DIR / "secrets.sock")

RULINGS_CSV = RUNTIME_ROOT / "rulings.csv"


def ensure_var_dirs() -> None:
    for path in (VAR_DIR, CHECKPOINT_DIR, RECORD_DIR, RUN_DIR, ARTIFACTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
