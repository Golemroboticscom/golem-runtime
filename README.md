# Golem Runtime — the lean rebuild

Branch `runtime-v3-langgraph` · parent issue `GOL-384` · decisions in [`rulings.csv`](rulings.csv)

**This is a TEST, not a decision** (ruling 1). Two independent consult rounds rejected the custom
dispatch engine of the old specification, so the question being answered here is whether the same
system can be built on LangGraph instead: the flow and the agent loop both running on it, the
definitions staying in tables, each agent in its own rootless container, and Telegram as the surface
Yakov approves from. The old system keeps running untouched beside it, and cutover is a decision
rather than an accident.

## What runs

The first flow is the **full design-robot flow, all 48 canonical steps** (ruling 17) — 31 agent
steps, 12 human gates, 2 external waits, 2 outbound sends and 1 script step, with three loops that
can send it back to step 9, 14 or 20.

```bash
export PYTHONPATH=/srv/runtime/lib:/srv/runtime/src

python3 -m golem_runtime validate design-robot     # preflight the table
python3 -m golem_runtime routes                    # which engine each actor resolves to
python3 -m golem_runtime run design-robot --fixtures            # offline, auto-answered gates
python3 -m golem_runtime run design-robot --transport bridge \
    --gate telegram --param mission_request=... --param capability_list=...
python3 -m golem_runtime record <run-id> --event engine_call   # the structured record
```

## The shape

| File | What it owns |
| --- | --- |
| `tables/` | The definitions. `flow.csv` is the flow, `agents.csv` is who runs it and how, `control_values.csv` is every tunable number. |
| `src/golem_runtime/tables.py` | Reading them. One source of truth per definition (ruling 4). |
| `src/golem_runtime/validate.py` | Preflight. A broken table is caught before a run, not halfway through twelve gates. |
| `src/golem_runtime/compiler.py` | The flow table becomes a LangGraph graph. There is no hand-written graph anywhere. |
| `src/golem_runtime/engine.py` | **The one engine wrapper.** No agent calls a provider directly, and no caller may name a model. |
| `src/golem_runtime/secrets_bridge.py` | The container holds no key: it asks a socket, the holder outside performs the call (ruling 15). |
| `src/golem_runtime/gates.py` | Where the graph stops and a human decides. `AutoGate` for tests, `TelegramGate` for real work (ruling 2). |
| `src/golem_runtime/store.py` | The state store behind a seam, and the checkpoint size ceiling (ruling 6). |
| `src/golem_runtime/records.py` | The structured record, emitted from day one even while nothing displays it (ruling 6). |
| `src/golem_runtime/effects.py` | The idempotent side-effect boundary. A replayed node does not pay twice. |
| `src/golem_runtime/artifacts.py` | Heavy files stay on the host; the run carries the pointer (ruling 8). |
| `src/golem_runtime/containers.py` | One rootless container per agent; its whole access is its mount list (ruling 10). |
| `src/golem_runtime/graph.py` + `langgraph.json` | The graph served behind the local agent server, so Studio can attach (ruling 14). |
| `checks/run_gates.py` | The gates. They run on every push and report; they do not block during the build (ruling 16). |

## The two rules that shape the code

**The iron rule.** The agent engine never asks for a specific model. `EngineWrapper.call` takes no
provider and no model argument *by construction*; the route comes from the actor's row in
`agents.csv`. A gate checks the signature on every push, because the rule is a prohibition and not a
convention.

**Untrusted content is never an instruction.** Everything a model returns is data the flow files, and
never a directive the runtime obeys. A routing answer is accepted only if it names a target the table
already permits.

## Running the server (Studio)

```bash
sudo -u golem-runtime env HOME=/srv/runtime XDG_RUNTIME_DIR=/srv/runtime/var/xdg \
  XDG_DATA_HOME=/srv/runtime/var/share XDG_CONFIG_HOME=/srv/runtime/var/config \
  podman run --rm --cgroup-manager=cgroupfs --events-backend=file \
  --name golem-runtime-server --network host \
  -v /srv/runtime/src:/opt/runtime/src:ro -v /srv/runtime/tables:/opt/runtime/tables:ro \
  -v /srv/runtime/langgraph.json:/opt/runtime/langgraph.json:ro \
  -v /srv/runtime/var/server:/opt/runtime/var:rw \
  golem-runtime-server:phase-a
```

The API answers on `http://127.0.0.1:2024`; Studio attaches with
`https://smith.langchain.com/studio/?baseUrl=http://<host>:2024`.

The server runs in a container because `langgraph-api` publishes no wheel for Python 3.10, which is
the host's only interpreter and needs root to change. See `Containerfile.server`.

## Install

```bash
pip install --target /srv/runtime/lib langgraph==1.2.11 langgraph-checkpoint-sqlite==3.1.1
```

`python3-venv` is absent and needs root, so packages go into `lib/` with `pip --target`. That
directory is also what the container mounts, so there is one copy and not two.

## What is deliberately not here

* **Credentials.** `secrets/` is gitignored and lives only on the host.
* **Heavy files.** `artifacts/` is gitignored; git keeps the pointer (ruling 8).
* **`lib/`.** Third-party packages are installed, not committed.
* **A run-viewer UI and live backup.** Allowed to be missing in phase one (ruling 6) — which is
  exactly why the record and the swappable store exist from the first commit.
