"""One rootless container per agent, and its whole access is a mount list.

Ruling 10: each agent runs in its own rootless container and sees only the directories
mounted into it. The OS identity matrix -- users, groups, ACLs, sudoers tiers -- is not
ported. An agent is no longer an operating-system entity: it is a row in a table and a run
in a container.

Ruling 15: the container's network access and the way it holds secrets are FIELDS on the
agent's row, never fixed in code. `network` and `secrets` below are read, never decided
here. In phase A the rows say `open` and `bridge`; changing either is a table edit.

Measured on 2026-08-31: the host runs rootless podman as `golem-runtime` (subuid
200000:65536). `interface-lead` has no subuid range, so containers are launched through
`sudo -u golem-runtime`, which is a grant the Interface already holds.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tables
from .paths import RUNTIME_ROOT, SECRET_SOCKET, VAR_DIR

CONTAINER_USER = "golem-runtime"
DEFAULT_IMAGE = "docker.io/library/python:3.10-slim"
PARAM_RE = re.compile(r"\$\{[^}]+\}")

# The runtime itself, not the agent's data. Always mounted, always read-only.
RUNTIME_MOUNTS = ((RUNTIME_ROOT / "lib", "/opt/runtime/lib", "ro"), (RUNTIME_ROOT / "src", "/opt/runtime/src", "ro"))

PODMAN_ENV = {
    "HOME": str(RUNTIME_ROOT),
    "XDG_RUNTIME_DIR": str(VAR_DIR / "xdg"),
    "XDG_DATA_HOME": str(VAR_DIR / "share"),
    "XDG_CONFIG_HOME": str(VAR_DIR / "config"),
}


class MountDenied(PermissionError):
    """The agent's row does not grant this path."""


@dataclass(frozen=True)
class Mount:
    source: Path
    mode: str

    @property
    def target(self) -> str:
        """Mounted at the same path it has on the host: the agent sees the same names as the
        tables do, it simply sees nothing else."""
        return str(self.source)

    def to_arg(self) -> str:
        return f"{self.source}:{self.target}:{self.mode}"


def parse_mounts(spec: str, params: dict[str, str] | None = None) -> list[Mount]:
    """`/srv/runtime/tables:ro;${product_path}:rw` -> the agent's entire world."""
    params = params or {}
    mounts: list[Mount] = []
    for entry in (spec or "").split(";"):
        entry = PARAM_RE.sub(lambda m: params.get(m.group(0), m.group(0)), entry.strip())
        if not entry:
            continue
        source, _, mode = entry.rpartition(":")
        if mode not in {"ro", "rw"}:
            raise ValueError(f"mount {entry!r} must end in :ro or :rw")
        mounts.append(Mount(Path(source), mode))
    return mounts


def agent_access(actor: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """Everything the container boundary is, read off one row."""
    row = tables.resolve_actor(actor)
    return {
        "agent": row["agent"],
        "mounts": parse_mounts(row.get("mounts", ""), params),
        "network": row.get("network", "") or "none",
        "secrets": row.get("secrets", "") or "none",
    }


def may_read(actor: str, path: Path | str, params: dict[str, str] | None = None) -> bool:
    target = Path(path).resolve()
    for mount in agent_access(actor, params)["mounts"]:
        source = mount.source.resolve()
        if target == source or source in target.parents:
            return True
    return False


def may_write(actor: str, path: Path | str, params: dict[str, str] | None = None) -> bool:
    target = Path(path).resolve()
    for mount in agent_access(actor, params)["mounts"]:
        if mount.mode != "rw":
            continue
        source = mount.source.resolve()
        if target == source or source in target.parents:
            return True
    return False


def podman_argv(actor: str, command: list[str], params: dict[str, str] | None = None, image: str = DEFAULT_IMAGE) -> list[str]:
    access = agent_access(actor, params)
    argv = ["sudo", "-n", "-u", CONTAINER_USER, "env"]
    argv += [f"{k}={v}" for k, v in PODMAN_ENV.items()]
    argv += ["podman", "run", "--rm", "--name", f"golem-{re.sub(r'[^a-zA-Z0-9]+', '-', access['agent']).strip('-').lower()}"]
    argv += ["--network", "host" if access["network"] == "open" else "none"]
    for source, target, mode in RUNTIME_MOUNTS:
        argv += ["-v", f"{source}:{target}:{mode}"]
    for mount in access["mounts"]:
        argv += ["-v", mount.to_arg()]
    if access["secrets"] == "bridge":
        # The address travels, the key does not. That is the whole point of ruling 15.
        argv += ["-v", f"{SECRET_SOCKET}:/opt/runtime/secrets.sock:rw"]
        argv += ["-e", "GOLEM_SECRET_SOCKET=/opt/runtime/secrets.sock"]
    elif access["secrets"] not in {"", "none"}:
        raise ValueError(f"{access['agent']}: unsupported secrets mode {access['secrets']!r} in phase A")
    argv += ["-e", "PYTHONPATH=/opt/runtime/lib:/opt/runtime/src"]
    argv += ["-e", f"GOLEM_RUNTIME_TABLES={RUNTIME_ROOT / 'tables'}", "-e", "GOLEM_RUNTIME_VAR=/tmp/run"]
    argv += ["-w", "/tmp"]
    argv += [image, *command]
    return argv


def run(actor: str, command: list[str], params: dict[str, str] | None = None, image: str = DEFAULT_IMAGE, timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(podman_argv(actor, command, params, image), capture_output=True, text=True, timeout=timeout)


def describe(actor: str, params: dict[str, str] | None = None) -> str:
    access = agent_access(actor, params)
    lines = [f"{actor} -> {access['agent']}", f"  network: {access['network']}", f"  secrets: {access['secrets']}"]
    lines += [f"  mount:   {m.source} ({m.mode})" for m in access["mounts"]] or ["  mount:   (nothing)"]
    lines.append("  command: " + shlex.join(podman_argv(actor, ["python3", "-c", "..."], params)))
    return "\n".join(lines)
