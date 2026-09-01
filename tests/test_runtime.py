"""The suite for the lean runtime.

Everything here runs offline: transport `echo`, gate channel `AutoGate`, tmp directories
for state, effects, records and artifacts. No test touches a provider, Telegram or the
real /srv/runtime/var.

The suite deliberately covers the four things the test has to show (ruling 1) -- the flow,
the gates, the engine calls and the record -- plus the two ceilings that exist because a
runaway loop wrote 90 GB of checkpoints on 2026-08-31.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from golem_runtime import artifacts, containers, engine, store, tables
from golem_runtime.effects import EffectLog
from golem_runtime.gates import AutoGate, GateRequest, validate_answer
from golem_runtime.records import RecordSink
from golem_runtime.runner import Run, fixture_params, run_config
from golem_runtime.validate import FlowInvalid, validate_all_flows, validate_flow

FLOW = "design-robot"


def make_run(tmp_path: Path, name: str, gate=None, **kwargs) -> Run:
    return Run(
        name,
        FLOW,
        gate=gate or AutoGate(),
        transport="echo",
        checkpoint_dir=tmp_path / "checkpoints",
        effects_path=tmp_path / "effects.sqlite",
        record_dir=tmp_path / "records",
        **kwargs,
    )


# ------------------------------------------------------------------ tables and preflight


def test_tables_are_the_source_and_the_flow_has_its_48_rows():
    assert FLOW in tables.flow_names()
    rows = tables.flow(FLOW)
    assert len(rows) == 48
    assert {"43a", "43b"}.issubset({r["step"] for r in rows})


def test_the_design_robot_flow_passes_preflight_and_the_report_names_any_flow_that_does_not():
    report = validate_all_flows()
    assert set(report) == set(tables.flow_names()) - {"any"}
    assert "error" not in report[FLOW]
    assert report[FLOW]["kinds"] == {"agent-step": 31, "human-gate": 12, "outbound-send": 2, "wait-external": 2, "script-step": 1}
    assert report[FLOW]["terminals"] == ["END:Validated"]
    # A flow that does not validate is REPORTED, never silently skipped.
    assert all("flow" in verdict for verdict in report.values())


def test_actor_resolves_to_exactly_one_agent_row():
    assert tables.resolve_actor("Interface")["agent"] == "Interface Lead"      # by team, lowest id
    assert tables.resolve_actor("Engineering Lead")["agent"] == "Engineering Lead"  # by name
    with pytest.raises(LookupError):
        tables.resolve_actor("Nobody At All")


def test_preflight_rejects_a_missing_runtime_parameter():
    params = fixture_params(FLOW)
    params.pop("${mission_request}")
    with pytest.raises(FlowInvalid, match="missing runtime parameters"):
        validate_flow(FLOW, params)


# ---------------------------------------------------------------------------- iron rule


def test_the_engine_wrapper_cannot_be_asked_for_a_model():
    """The agent engine never asks for a specific model. A prohibition, not a convention."""
    assert engine.call_signature_forbids_model()
    parameters = set(inspect.signature(engine.EngineWrapper.call).parameters)
    assert not ({"model", "provider", "engine", "route"} & parameters)


def test_the_route_comes_from_the_agents_table():
    routes = engine.route_for("Analyst")
    # The first hop is whatever the row says. It is NOT anthropic any more: the GOL-291
    # session confirmed no Anthropic API key exists under any spelling, so a route that
    # could never answer was taken out of the table rather than tolerated.
    assert [r.provider for r in routes][0] == "openai"
    assert "anthropic" not in [r.provider for r in routes]
    assert engine.parse_route("a/b>c/d") == [engine.Route("a", "b"), engine.Route("c", "d")]
    with pytest.raises(ValueError):
        engine.parse_route("noslash")


def test_a_human_gate_actor_has_no_engine_route():
    assert engine.route_for("Yakov") == []


# --------------------------------------------------------------- the flow, end to end


def test_the_full_design_robot_flow_runs_from_start_to_finish(tmp_path):
    summary = make_run(tmp_path, "full").execute(fixture_params(FLOW))
    assert summary["status"] == "completed"
    assert summary["terminal"] == "END:Validated"
    assert summary["distinct_steps"] == 48
    assert summary["approvals"] == 12
    assert summary["external_inputs"] == 2
    assert summary["artifacts"] == 1


def test_every_agent_step_left_an_engine_call_record(tmp_path):
    run = make_run(tmp_path, "records")
    run.execute(fixture_params(FLOW))
    calls = run.sink.of_event("engine_call")
    agent_steps = {r["step"] for r in tables.flow(FLOW) if r["kind"] in {"agent-step", "outbound-send"}}
    assert agent_steps <= {c["step"] for c in calls}
    assert all(c["transport"] == "echo" and c["ok"] for c in calls)
    assert all({"prompt_sha256", "route", "elapsed_ms", "usage"} <= set(c) for c in calls)
    assert run.sink.of_event("run_end")[0]["terminal"] == "END:Validated"


def test_a_gate_rejection_cancels_the_run_before_the_next_effect(tmp_path):
    gate = AutoGate(decisions={"4": "reject"})
    summary = make_run(tmp_path, "reject", gate=gate).execute(fixture_params(FLOW))
    assert summary["terminal"] == "END:Cancelled"
    assert summary["steps_executed"] == 3
    assert summary["approvals"] == 0


def test_an_external_wait_stops_and_resumes_with_its_provenance(tmp_path):
    run = make_run(tmp_path, "external")
    run.execute(fixture_params(FLOW))
    answered = {r["step"]: r for r in run.sink.of_event("gate_answered")}
    assert answered["41"]["decision"] == "received"
    assert answered["41"]["provenance"].startswith("auto-gate")
    assert answered["4"]["decision"] == "approve"


def test_the_gate_asks_before_every_gate_row(tmp_path):
    gate = AutoGate()
    make_run(tmp_path, "asked", gate=gate).execute(fixture_params(FLOW))
    gate_rows = [r["step"] for r in tables.flow(FLOW) if r["kind"] in {"human-gate", "wait-external"}]
    assert [r.step for r in gate.asked] == gate_rows


# ------------------------------------------------------------------------- gate answers


@pytest.mark.parametrize(
    "value, needle",
    [
        ({"decision": "approve"}, "requires non-empty"),
        ({"decision": "approve", "actor": "Yakov", "provenance": " "}, "requires non-empty"),
        ({"decision": "maybe", "actor": "Yakov", "provenance": "x"}, "invalid human-gate decision"),
        ("approve", "must be an object"),
    ],
)
def test_an_answer_without_actor_or_provenance_is_not_an_answer(value, needle):
    with pytest.raises(ValueError, match=needle):
        validate_answer(value, "human-gate", "run")


def test_an_external_wait_takes_a_different_vocabulary():
    assert validate_answer({"decision": "received", "actor": "Integration", "provenance": "p"}, "wait-external", "r")["run_id"] == "r"
    with pytest.raises(ValueError):
        validate_answer({"decision": "approve", "actor": "a", "provenance": "p"}, "wait-external", "r")


# ------------------------------------------------------------------------------- loops


@pytest.mark.parametrize("target", ["14", "9"])
def test_step_31_can_loop_back_to_either_permitted_target(tmp_path, target):
    run = make_run(tmp_path, f"loop31-{target}")
    summary = run.execute(fixture_params(FLOW), route_plan={"31": [target]})
    assert summary["terminal"] == "END:Validated"
    assert summary["loop_counts"]["31"] == 1
    trace = [r["step"] for r in run.sink.of_event("step_done")]
    assert trace[trace.index("31") + 1] == target


def test_a_loop_that_exceeds_its_ceiling_stops_the_run(tmp_path):
    summary = make_run(tmp_path, "ceiling").execute(fixture_params(FLOW), route_plan={"12": ["9"] * 4})
    assert summary["status"] == "failed"
    assert "loop ceiling 3 exceeded" in summary["error"]


def test_the_run_carries_a_step_ceiling_above_every_loop_ceiling():
    limit = run_config("x")["recursion_limit"]
    assert limit == tables.control_int("run_step_ceiling", "runtime")
    assert limit > 48


# ------------------------------------------------------------------- effects and replay


def test_an_effect_is_performed_once_per_key(tmp_path):
    log = EffectLog(tmp_path / "e.sqlite")
    calls = []
    payload, performed = log.once("k", "run", "1", lambda: (calls.append(1), {"text": "done"})[1])
    assert performed and payload["text"] == "done"
    payload, performed = log.once("k", "run", "1", lambda: (calls.append(1), {"text": "again"})[1])
    assert not performed and payload["text"] == "done"
    assert len(calls) == 1


def test_a_step_re_entered_after_a_gate_does_not_perform_its_effect_twice(tmp_path):
    """LangGraph re-runs a node from the top when its interrupt is answered. Every gate row
    therefore executes its body twice, and the effect log is what makes that harmless."""
    make_run(tmp_path, "once").execute(fixture_params(FLOW))
    assert EffectLog(tmp_path / "effects.sqlite").count("once") == 48


def test_a_run_that_dies_at_a_gate_resumes_from_its_checkpoint_and_finishes(tmp_path):
    class Deserter(AutoGate):
        def ask(self, request):
            if request.step == "17":
                raise RuntimeError("the operator walked away")
            return super().ask(request)

    first = make_run(tmp_path, "resume", gate=Deserter())
    assert first.execute(fixture_params(FLOW))["status"] == "failed"
    performed_before = EffectLog(tmp_path / "effects.sqlite").count("resume")
    assert 0 < performed_before < 48

    second = make_run(tmp_path, "resume")  # same run id, same checkpoint, same effect log
    summary = second.execute(fixture_params(FLOW), resume=True)
    assert summary["terminal"] == "END:Validated"
    assert EffectLog(tmp_path / "effects.sqlite").count("resume") == 48
    # The record continued its sequence rather than restarting it.
    assert [e["seq"] for e in second.sink.read()] == list(range(1, len(second.sink.read()) + 1))


# ---------------------------------------------------------------------------- ceilings


def test_the_checkpoint_ceiling_is_read_from_the_table_and_enforced(tmp_path):
    assert store.checkpoint_ceiling_bytes() == tables.control_int("checkpoint_max_mb", "runtime") * 1024 * 1024
    fat = tmp_path / "fat.sqlite"
    fat.write_bytes(b"0" * 16)
    assert store.assert_within_ceiling(fat) == 16
    fat.write_bytes(b"0" * (store.checkpoint_ceiling_bytes() + 1))
    with pytest.raises(store.CheckpointTooLarge):
        store.assert_within_ceiling(fat)


def test_the_state_store_is_swappable(tmp_path):
    with store.open_store("m", "memory") as (saver, path):
        assert path is None and saver is not None
    with store.open_store("s", "sqlite", tmp_path) as (saver, path):
        assert path is not None and path.exists()
    with pytest.raises(ValueError):
        with store.open_store("x", "postgres"):
            pass


def test_a_run_on_the_memory_store_still_finishes(tmp_path):
    summary = make_run(tmp_path, "mem", state_store="memory").execute(fixture_params(FLOW))
    assert summary["terminal"] == "END:Validated"


# --------------------------------------------------------------------------- artifacts


def test_a_heavy_file_becomes_a_pointer(tmp_path):
    source = tmp_path / "render.png"
    source.write_bytes(b"\x89PNG" + b"0" * 1000)
    pointer = artifacts.store(source, "run", "43b", root=tmp_path / "store")
    assert pointer["bytes"] == 1004 and len(pointer["sha256"]) == 64
    assert pointer["uri"].startswith("file://")
    assert artifacts.pointers("run", root=tmp_path / "store")[0]["sha256"] == pointer["sha256"]


def test_an_oversized_artifact_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "max_bytes", lambda: 10)
    source = tmp_path / "big.bin"
    source.write_bytes(b"0" * 100)
    with pytest.raises(artifacts.ArtifactTooLarge):
        artifacts.store(source, "run", "1", root=tmp_path / "store")


# -------------------------------------------------------------------------- containers


def test_an_agents_access_is_its_mount_list_and_nothing_else():
    params = {"${product_path}": "/srv/runtime/artifacts/demo"}
    assert containers.may_write("Engineering Lead", "/srv/runtime/artifacts/demo/x.step", params)
    assert not containers.may_write("Validator", "/srv/runtime/artifacts/demo/x.step", params)
    assert containers.may_read("Validator", "/srv/runtime/artifacts/demo/x.step", params)
    assert not containers.may_read("Rendering", "/srv/runtime/tables/flow.csv", params)
    assert not containers.may_read("Engineering Lead", "/srv/golem/CLAUDE.md", params)


def test_network_and_secrets_are_fields_on_the_row_not_facts_in_the_code():
    access = containers.agent_access("Analyst")
    assert access["network"] == "open" and access["secrets"] == "bridge"
    argv = containers.podman_argv("Analyst", ["python3", "-c", "pass"], {"${product_path}": "/tmp/p"})
    assert "--network" in argv and argv[argv.index("--network") + 1] == "host"
    assert any(a.endswith("secrets.sock:rw") for a in argv)
    assert not any("providers.json" in a for a in argv)  # the key never travels
    mounted = [a for a in argv if a.startswith("/srv/runtime/tables:")]
    assert mounted == ["/srv/runtime/tables:/srv/runtime/tables:ro"]  # same path, nothing else


def test_the_human_owner_gets_no_container_boundary():
    access = containers.agent_access("Yakov")
    assert access["mounts"] == [] and access["network"] == "none" and access["secrets"] == "none"
    assert access["image"] == ""  # a human does not run in a container at all


def test_the_container_image_is_a_field_like_network_and_secrets():
    """The Engineering Lead needs FreeCAD and CalculiX; the Analyst needs neither."""
    assert containers.agent_access("Engineering Lead")["image"] == "golem-runtime-cad:phase-a"
    assert containers.agent_access("Simulation")["image"] == "golem-runtime-cad:phase-a"
    assert containers.agent_access("Analyst")["image"] == containers.DEFAULT_IMAGE
    argv = containers.podman_argv("Engineering Lead", ["true"], {"${product_path}": "/tmp/p"})
    assert "golem-runtime-cad:phase-a" in argv


# ------------------------------------------------------------------------------ record


def test_the_record_is_append_only_jsonl(tmp_path):
    sink = RecordSink("r", tmp_path)
    sink.emit("a", x=1)
    sink.emit("b", x=2)
    entries = sink.read()
    assert [e["seq"] for e in entries] == [1, 2]
    assert entries[0]["event"] == "a" and entries[0]["run_id"] == "r"
    assert sink.of_event("b")[0]["x"] == 2


def test_a_gate_request_knows_which_decisions_it_accepts():
    assert GateRequest("r", "4", "human-gate", "Yakov").decisions == {"approve", "reject"}
    assert GateRequest("r", "41", "wait-external", "Integration").decisions == {"received", "cancel"}


# ---------------------------------------------------------------------------- tools


def test_a_tool_is_only_offered_if_the_catalogue_declares_it_and_the_row_grants_it():
    from golem_runtime import tools

    report = tools.coverage()
    assert report["asked_but_not_declared"] == []      # no row asks for a tool tools.csv does not know
    assert report["implemented_but_not_declared"] == []  # nothing exists outside the catalogue
    granted = [spec.name for spec in tools.granted("Data gathering")]
    assert "WebSearch" in granted and "Write" in granted
    assert "Consult" not in granted                    # its row does not grant it
    assert tools.granted("Yakov") == []                # the human owner runs no tools


def test_a_tool_cannot_reach_where_the_mount_list_does_not_reach(tmp_path):
    from golem_runtime import tools

    product = tmp_path / "product"
    product.mkdir()
    params = {"${product_path}": str(product)}

    writer = tools.ToolContext("r", "1", "Engineering Lead", params)
    assert "written" in tools.run_tool("Write", {"path": "note.md", "content": "hello"}, writer)
    assert (product / "note.md").read_text() == "hello"

    # Two independent layers refuse, and they refuse for different reasons.
    # Layer one, the GRANT: the Validator's row does not name Write at all.
    reader = tools.ToolContext("r", "1", "Validator", params)
    assert "no tool named 'Write'" in tools.run_tool("Write", {"path": "note.md", "content": "x"}, reader)["error"]
    assert tools.run_tool("Read", {"path": "note.md"}, reader)["content"] == "hello"

    # Layer two, the MOUNT LIST: Rendering has Write, but its row mounts only the product
    # path -- so the same tool cannot touch the tables.
    renderer = tools.ToolContext("r", "1", "Rendering", params)
    assert "written" in tools.run_tool("Write", {"path": "render.txt", "content": "x"}, renderer)
    assert "may not write" in tools.run_tool("Write", {"path": "/srv/runtime/tables/flow.csv", "content": "x"}, renderer)["error"]


def test_a_tool_may_not_escape_the_product_folder(tmp_path):
    from golem_runtime import tools

    params = {"${product_path}": str(tmp_path / "product")}
    ctx = tools.ToolContext("r", "1", "Engineering Lead", params)
    assert "may not read" in tools.run_tool("Read", {"path": "/etc/passwd"}, ctx)["error"]
    assert "may not write" in tools.run_tool("Write", {"path": "/srv/golem/CLAUDE.md", "content": "x"}, ctx)["error"]


def test_a_tool_failure_is_an_answer_and_not_a_crash(tmp_path):
    from golem_runtime import tools

    ctx = tools.ToolContext("r", "1", "Data gathering", {"${product_path}": str(tmp_path)})
    assert "error" in tools.run_tool("WebFetch", {"url": "ftp://example.com"}, ctx)
    assert "error" in tools.run_tool("Read", {"path": "missing.md"}, ctx)
    assert "wrong arguments" in tools.run_tool("Write", {"path": "a.md"}, ctx)["error"]


def test_a_step_with_no_tools_is_still_a_single_call(tmp_path):
    from golem_runtime.engine import EngineWrapper

    sink = RecordSink("noloop", tmp_path)
    answer = EngineWrapper(sink, transport="echo").work(
        run_id="noloop", step="1", actor="Orchestrator", purpose="agent-step", prompt="hello"
    )
    assert answer["turns"] == 1 and answer["tool_calls"] == []
    assert len(sink.of_event("engine_call")) == 1


def test_the_second_engine_asks_for_a_different_answer_without_naming_one():
    """`prefer_alternate` rotates the row's own preference order. It names no model."""
    import inspect as _inspect

    from golem_runtime.engine import EngineWrapper

    assert "prefer_alternate" in _inspect.signature(EngineWrapper.call).parameters
    assert engine.call_signature_forbids_model()
    routes = engine.route_for("Analyst")
    assert len(routes) >= 2  # there has to BE an alternative for one to be preferred
    assert len({r.provider for r in routes}) >= 2  # ...and it has to be a different house


def test_the_catalogue_and_the_implementations_agree():
    from golem_runtime import tools

    report = tools.coverage()
    assert report["asked_but_not_declared"] == []
    assert report["implemented_but_not_declared"] == []
    # Declared-but-unbuilt is allowed and REPORTED: it is the to-do list, not a lie.
    assert set(report["declared_but_not_implemented"]) <= {"NotebookEdit", "Skill", "Task", "TodoWrite"}


def test_bash_is_granted_only_where_a_step_has_to_run_something():
    from golem_runtime import tools

    for actor in ("Engineering Lead", "Simulation", "Rendering"):
        assert "Bash" in [s.name for s in tools.granted(actor)]
    for actor in ("Data gathering", "Validator", "Integration", "Interface"):
        assert "Bash" not in [s.name for s in tools.granted(actor)]


def test_bash_refuses_an_actor_with_no_writable_working_directory(tmp_path):
    from golem_runtime import tools

    ctx = tools.ToolContext("r", "1", "Validator", {"${product_path}": str(tmp_path)})
    assert "no tool named 'Bash'" in tools.run_tool("Bash", {"command": "ls"}, ctx)["error"]


def test_a_second_opinion_avoids_the_engine_that_just_answered(tmp_path):
    """Rotating the row's order was not enough; the wrapper remembers who actually served."""
    from golem_runtime.engine import EngineWrapper

    wrapper = EngineWrapper(RecordSink("alt", tmp_path), transport="echo")
    wrapper.call(run_id="r", step="1", actor="Analyst", purpose="primary", prompt="x")
    served = wrapper._last_served["Analyst"]
    assert served == "openai"
    calls = []
    original = wrapper._perform

    def watching(route, *rest):
        calls.append(route.provider)
        return original(route, *rest)

    wrapper._perform = watching
    wrapper.call(run_id="r", step="1", actor="Analyst", purpose="second", prompt="x", prefer_alternate=True)
    assert calls[0] != served


# ------------------------------------------------------------------ the path boundary


def test_a_relative_path_override_is_refused_not_resolved(monkeypatch, tmp_path):
    """A relative override once made the runtime write into /srv/golem.

    `Path.resolve()` resolves against the CURRENT WORKING DIRECTORY, so the same variable
    meant a different tree depending on where the process was standing. The GOL-291 session
    found the result: an empty `artifacts/toolproof` inside /srv/golem, owned by
    interface-lead. Refusing is the only answer -- there is nothing safe to guess.
    """
    import importlib

    monkeypatch.chdir(tmp_path)
    for variable in ("GOLEM_RUNTIME_ROOT", "GOLEM_RUNTIME_TABLES", "GOLEM_RUNTIME_VAR",
                     "GOLEM_RUNTIME_ARTIFACTS", "GOLEM_RUNTIME_SECRETS", "GOLEM_SECRET_SOCKET"):
        monkeypatch.setenv(variable, "artifacts/somewhere")
        with pytest.raises(Exception) as raised:
            importlib.reload(importlib.import_module("golem_runtime.paths"))
        assert "is relative" in str(raised.value)
        monkeypatch.delenv(variable)
    importlib.reload(importlib.import_module("golem_runtime.paths"))


def test_an_absolute_override_is_honoured(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("GOLEM_RUNTIME_ROOT", str(tmp_path))
    paths = importlib.reload(importlib.import_module("golem_runtime.paths"))
    assert paths.RUNTIME_ROOT == tmp_path
    assert paths.TABLES_DIR == tmp_path / "tables"
    monkeypatch.delenv("GOLEM_RUNTIME_ROOT")
    importlib.reload(importlib.import_module("golem_runtime.paths"))


def test_the_default_tree_is_absolute_and_is_not_golem():
    from golem_runtime import paths

    assert paths.RUNTIME_ROOT.is_absolute()
    for path in (paths.RUNTIME_ROOT, paths.TABLES_DIR, paths.VAR_DIR,
                 paths.ARTIFACTS_DIR, paths.SECRETS_DIR, paths.SECRET_SOCKET):
        assert path.is_absolute()
        assert "/srv/golem" not in str(path)


def test_the_step_may_name_its_own_engine_and_the_agent_is_the_fallback():
    """The simplest routing there is: one column on the flow row, more specific wins.

    Nothing new was built for it -- `parse_route` already read `a/b>c/d` cascades and the
    wrapper already walked them. This is one lookup ahead of the one that existed.
    """
    agent_route = [str(r) for r in engine.route_for("Analyst")]
    assert agent_route and "openai" in agent_route[0]

    assert [str(r) for r in engine.route_for("Analyst", "xai/grok-4.5")] == ["xai/grok-4.5"]
    assert [str(r) for r in engine.route_for("Analyst", "google/gemini-2.5-flash>openai/gpt-5.6-luna")] == [
        "google/gemini-2.5-flash", "openai/gpt-5.6-luna"
    ]
    # An empty cell is not a choice: it falls through to the agent's row.
    assert [str(r) for r in engine.route_for("Analyst", "")] == agent_route
    assert [str(r) for r in engine.route_for("Analyst", "   ")] == agent_route


def test_the_flow_table_carries_the_column_and_it_is_empty_by_default():
    rows = tables.flow(FLOW)
    assert all("engine" in row for row in rows)
    assert not any(row["engine"] for row in rows), "filling it is Yakov's decision, not the build's"


def test_the_iron_rule_survives_the_per_step_engine():
    """A step naming an engine is a TABLE speaking. The caller still may not."""
    assert engine.call_signature_forbids_model()


def test_a_gate_carries_the_deliverable_it_is_asking_about(tmp_path):
    """Yakov pressed approve on "Approve the mission spec" with no spec attached (#6598).

    A gate that shows only its own headline asks a human to sign an unread page. It must
    carry the output of the step that just finished.
    """
    gate = AutoGate()
    make_run(tmp_path, "deliverable", gate=gate).execute(fixture_params(FLOW))
    asked = {request.step: request for request in gate.asked}

    first = asked["4"]
    assert first.deliverable_step == "3", "gate 4 decides on what step 3 produced"
    assert first.deliverable, "the gate was asked with nothing to decide on"

    for step, request in asked.items():
        assert request.deliverable_step, f"gate {step} names no deliverable"
        assert request.deliverable, f"gate {step} carries no deliverable"
        assert request.deliverable_actor, f"gate {step} does not say who submitted it"

    # And the submitting agent is the actor of the step that produced it, not the gate's.
    assert first.deliverable_actor == next(r["actor"] for r in tables.flow(FLOW) if r["step"] == "3")
    assert first.actor == "Yakov"


def test_a_press_that_belongs_to_no_open_gate_is_answered_out_loud():
    """Silence on a stray press leaves the button spinning and the person thinking they
    answered. Yakov pressed a layout sample with live-looking buttons and waited forty
    minutes (#6610)."""
    from golem_runtime.gates import GateRequest, TelegramGate

    answered = []

    class Mute:
        chat_id = "1"

        def answer_callback(self, callback_id, text):
            answered.append(text)

    channel = TelegramGate.__new__(TelegramGate)
    channel.telegram = Mute()
    request = GateRequest("carrier-2", "4", "human-gate", "Yakov")

    assert channel._read({"callback_query": {"id": "1", "data": "demo|x|a", "from": {}}}, request) is None
    assert channel._read({"callback_query": {"id": "2", "data": "g|9|approve", "from": {}}}, request) is None
    assert len(answered) == 2, "both strays must be told, not ignored"
    assert "gate 4" in answered[0] and "carrier-2" in answered[0]

    # The real one still passes, and is not answered with a refusal.
    real = channel._read({"callback_query": {"id": "3", "data": "g|4|approve", "from": {"username": "Ja_Jake"}}}, request)
    assert real is None or real["decision"] == "approve"


def test_the_gate_question_shows_the_deliverable_and_says_so_when_truncated():
    from golem_runtime.gates import GateRequest, TelegramGate

    channel = TelegramGate.__new__(TelegramGate)  # no network, no token
    short = GateRequest("r", "4", "human-gate", "Yakov", action="Approve the mission spec",
                        deliverable="mass budget: 50 kg", deliverable_step="3")
    short.deliverable_actor = "Interface"
    text = channel._question(short)
    assert "Approve the mission spec" in text
    assert "mass budget: 50 kg" in text
    assert "step 3" in text
    assert "<b>Interface</b>" in text, "the submitting agent must be named, and in bold"

    # An expandable blockquote, never <pre>: Telegram's code block is translucent and the
    # chat wallpaper bleeds through it as a white band (#6605).
    assert "<blockquote expandable>" in text
    assert "<pre>" not in text

    long = GateRequest("r", "4", "human-gate", "Yakov", deliverable="x" * 9000, deliverable_step="3")
    assert "cut here" in channel._question(long)

    empty = GateRequest("r", "4", "human-gate", "Yakov")
    assert "no deliverable" in channel._question(empty)
