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
    assert [r.provider for r in routes][0] == "anthropic"
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


def test_the_human_owner_gets_no_container_boundary():
    access = containers.agent_access("Yakov")
    assert access["mounts"] == [] and access["network"] == "none" and access["secrets"] == "none"


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
