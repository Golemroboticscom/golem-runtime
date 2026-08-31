#!/usr/bin/env python3
"""Prove the container boundary on the live machine, not on paper.

Rulings 10 and 15 make two claims that are only worth anything if they are true of a real
process: an agent sees ONLY what its row mounts, and it holds NO credential -- it asks a
socket and the holder outside performs the call.

This runs one agent step inside its own rootless container and checks both:

  1. the paths its row grants are readable inside
  2. the paths its row does not grant are absent
  3. `secrets/providers.json` is not visible at all
  4. an engine call through the mounted socket returns a real provider answer
  5. the engine wrapper inside the container still refuses to be told a model

    python3 checks/container_proof.py "Engineering Lead"
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "lib"))

from golem_runtime import containers  # noqa: E402
from golem_runtime.paths import SECRETS_DIR  # noqa: E402

INSIDE = r"""
import json, os, pathlib, sys
from golem_runtime import containers, engine
from golem_runtime.records import RecordSink

actor = sys.argv[1]
params = json.loads(sys.argv[2])
report = {"actor": actor, "uid": os.getuid(), "hostname": os.uname().nodename}

access = containers.agent_access(actor, params)
report["network_field"] = access["network"]
report["secrets_field"] = access["secrets"]
report["granted_visible"] = {str(m.source): pathlib.Path(m.source).exists() for m in access["mounts"]}
report["ungranted_absent"] = {p: not pathlib.Path(p).exists() for p in [
    "/srv/runtime/secrets", "/srv/runtime/var", "/srv/runtime/rulings.csv", "/srv/golem", "/home/interface-lead",
] if not containers.may_read(actor, p, params)}
report["holds_no_credential"] = not pathlib.Path("/srv/runtime/secrets/providers.json").exists()
report["socket_present"] = pathlib.Path(os.environ.get("GOLEM_SECRET_SOCKET", "")).exists()
report["iron_rule_holds"] = engine.call_signature_forbids_model()

sink = RecordSink("container-proof", pathlib.Path("/tmp/run/records"))
wrapper = engine.EngineWrapper(sink, transport="bridge")
answer = wrapper.call(
    run_id="container-proof", step="proof", actor=actor, purpose="container-proof",
    prompt="Reply with exactly this word and nothing else: contained",
)
report["engine_answer"] = answer["text"].strip()[:60]
report["engine_route_used"] = f"{answer['provider']}/{answer['model']}"
report["engine_attempts"] = [f"{a['provider']}/{a['model']}:{'ok' if a['ok'] else 'failed'}" for a in answer["attempts"]]
report["records_written"] = len(sink.read())
print("GOLEM-PROOF " + json.dumps(report, sort_keys=True))
"""


def main() -> int:
    actor = sys.argv[1] if len(sys.argv) > 1 else "Engineering Lead"
    params = {"${product_path}": str(ROOT / "artifacts" / "rooftop-rover")}
    Path(params["${product_path}"]).mkdir(parents=True, exist_ok=True)

    print(containers.describe(actor, params))
    print()
    command = ["sh", "-c", "mkdir -p /tmp/run && exec python3 -c " + shlex.quote(INSIDE) + " " + shlex.quote(actor) + " " + shlex.quote(json.dumps(params))]
    result = subprocess.run(containers.podman_argv(actor, command, params), capture_output=True, text=True, timeout=900)
    line = next((l for l in result.stdout.splitlines() if l.startswith("GOLEM-PROOF ")), None)
    if line is None:
        print(result.stdout[-2000:])
        print(result.stderr[-2000:], file=sys.stderr)
        return 1
    report = json.loads(line[len("GOLEM-PROOF ") :])
    print(json.dumps(report, indent=2, sort_keys=True))

    failures = []
    if not all(report["granted_visible"].values()):
        failures.append("a granted path is not visible inside the container")
    if not all(report["ungranted_absent"].values()):
        failures.append("a path the row does not grant is visible inside the container")
    if not report["holds_no_credential"]:
        failures.append("the container can see the credential file")
    if not report["iron_rule_holds"]:
        failures.append("the wrapper inside the container accepts a model argument")
    if report["engine_answer"].lower().strip(".") != "contained":
        failures.append(f"the engine answered {report['engine_answer']!r}")
    # The host holds the credential; the container proved it does not.
    if not (SECRETS_DIR / "providers.json").exists():
        failures.append("the host has no credential file, so the proof proves nothing")

    print()
    for line in failures:
        print(f"FAIL  {line}")
    if not failures:
        print("PASS  the agent saw only its mounts, held no key, and still made a real engine call")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
