#!/usr/bin/env python3
"""The gates. They run on every push from the first commit; during the build they do not block.

Ruling 16 draws a line that is easy to blur: branch protection BLOCKS a merge, a gate CHECKS
a change. During the build there is no protection and no approval -- direct pushes to `main`
with the deploy key -- and the gates still run, still report, and what they flag is still
fixed. Discipline without a lock.

Report-only is the default. `--blocking` is the switch that arms them, and it is thrown at
the END OF THE BUILD, not at the end of phase A.

    python3 checks/run_gates.py             # report, always exit 0
    python3 checks/run_gates.py --blocking  # fail the push on any FAIL
"""
from __future__ import annotations

import argparse
import inspect
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "lib"))

RESULTS: list[tuple[str, bool, str]] = []

# A provider endpoint may appear in exactly one file: the bridge that performs the call.
PROVIDER_ENDPOINTS = re.compile(r"api\.openai\.com|generativelanguage\.googleapis\.com|api\.anthropic\.com")
BRIDGE_FILE = "src/golem_runtime/secrets_bridge.py"
SECRET_SHAPES = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "an OpenAI-shaped key"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "a Google-shaped key"),
    (re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}"), "a Telegram bot token"),
    # Caught a real near-miss on 2026-08-31: `git add -A` staged the repository's own deploy key,
    # because HOME for the container tooling is /srv/runtime and .ssh sits inside the tree.
    (re.compile(r"-----BEGIN (OPENSSH|RSA|EC|PGP) PRIVATE KEY"), "a private key"),
]


def gate(name: str):
    def wrap(fn):
        def run():
            try:
                ok, detail = fn()
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            RESULTS.append((name, ok, detail))

        run.__name__ = fn.__name__
        return run

    return wrap


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True)
    if out.returncode != 0:
        return [p for p in ROOT.rglob("*") if p.is_file() and not any(x in p.parts for x in ("var", "lib", "secrets", ".git", "artifacts"))]
    return [ROOT / line for line in out.stdout.splitlines() if line.strip()]


@gate("tables-preflight")
def check_tables():
    from golem_runtime.validate import validate_all_flows

    report = validate_all_flows()
    if "error" in report.get("design-robot", {}):
        return False, f"design-robot does not validate: {report['design-robot']['error']}"
    broken = sorted(name for name, verdict in report.items() if "error" in verdict)
    detail = f"design-robot: {report['design-robot']['rows']} rows, {report['design-robot']['kinds']}"
    if broken:
        detail += f" · other flows still failing preflight: {broken}"
    return True, detail


@gate("iron-rule")
def check_iron_rule():
    """The agent engine never asks for a specific model, and only the bridge calls a provider."""
    from golem_runtime import engine

    if not engine.call_signature_forbids_model():
        return False, "EngineWrapper.call accepts a model/provider argument"
    offenders = []
    for path in tracked_files():
        if path.suffix != ".py" or path.relative_to(ROOT).as_posix() == BRIDGE_FILE:
            continue
        if PROVIDER_ENDPOINTS.search(path.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(path.relative_to(ROOT).as_posix())
    if offenders:
        return False, f"a provider endpoint appears outside the bridge: {offenders}"
    return True, f"no model argument on the wrapper; provider endpoints confined to {BRIDGE_FILE}"


@gate("file-size")
def check_file_size():
    from golem_runtime import tables

    ceiling = tables.control_int("commit_file_max_kb", "runtime") * 1024
    heavy = [(p.relative_to(ROOT).as_posix(), p.stat().st_size) for p in tracked_files() if p.exists() and p.stat().st_size >= ceiling]
    if heavy:
        return False, f"heavy files must be artifacts, not commits: {heavy}"
    return True, f"every tracked file is under {ceiling // 1024} KB"


@gate("no-secrets")
def check_no_secrets():
    findings = []
    for path in tracked_files():
        if not path.exists() or path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, what in SECRET_SHAPES:
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT).as_posix()}: {what}")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").exists() else ""
    for must in ("secrets/", ".ssh/"):
        if must not in ignore:
            findings.append(f".gitignore does not exclude {must}")
    if findings:
        return False, "; ".join(findings)
    return True, "no credential shape in a tracked file; secrets/ and .ssh/ are ignored"


@gate("one-source")
def check_one_source():
    """One source of truth per definition (ruling 4): no table exists twice in the tree."""
    names = {}
    for path in tracked_files():
        if path.suffix == ".csv":
            names.setdefault(path.name, []).append(path.relative_to(ROOT).as_posix())
    duplicates = {name: places for name, places in names.items() if len(places) > 1}
    if duplicates:
        return False, f"the same table appears in more than one place: {duplicates}"
    return True, f"{len(names)} tables, each in exactly one place"


@gate("record-from-day-one")
def check_record():
    """Ruling 6: every engine call emits a structured record, even while nothing displays it.

    Checked by BEHAVIOUR, not by reading the source. An earlier version of this gate matched
    a literal line of `EngineWrapper.call` and broke the moment that line moved -- a check
    that fails when the code is merely rearranged is noise, and one that passes because the
    string happens to be present is worse.
    """
    import tempfile

    from golem_runtime.engine import EngineUnavailable, EngineWrapper
    from golem_runtime.records import RecordSink

    with tempfile.TemporaryDirectory() as tmp:
        sink = RecordSink("gate-check", Path(tmp))
        # A bridge transport pointed at a socket that does not exist: every route must FAIL.
        wrapper = EngineWrapper(sink, transport="bridge", socket_path=Path(tmp) / "absent.sock")
        try:
            wrapper.call(run_id="gate-check", step="1", actor="Analyst", purpose="gate", prompt="x")
            return False, "a call with no bridge somehow succeeded"
        except EngineUnavailable:
            pass
        records = sink.of_event("engine_call")
    if not records:
        return False, "a failed engine call emitted no record at all"
    if any(record["ok"] for record in records):
        return False, "a failed call recorded itself as ok"
    if not all({"prompt_sha256", "route", "elapsed_ms", "error"} <= set(record) for record in records):
        return False, "an engine_call record is missing its fields"
    return True, f"a call that failed every route still emitted {len(records)} engine_call records"


@gate("tests")
def check_tests():
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{ROOT}/lib:{ROOT}/src", "HOME": str(Path.home())},
    )
    tail = (out.stdout + out.stderr).strip().splitlines()
    return out.returncode == 0, tail[-1] if tail else "no output"


GATES = [check_tables, check_iron_rule, check_file_size, check_no_secrets, check_one_source, check_record, check_tests]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocking", action="store_true", help="fail the push on any FAIL (end of build, ruling 16)")
    args = parser.parse_args()
    for check in GATES:
        check()
    width = max(len(name) for name, _, _ in RESULTS)
    for name, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name:{width}}  {detail}")
    failed = [name for name, ok, _ in RESULTS if not ok]
    mode = "BLOCKING" if args.blocking else "report-only (ruling 16: the build regime)"
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} gates passed · mode: {mode}")
    if failed and not args.blocking:
        print(f"flagged, not blocked: {failed} — ruling 16 says what they flag is fixed")
    return 1 if (failed and args.blocking) else 0


if __name__ == "__main__":
    sys.exit(main())
