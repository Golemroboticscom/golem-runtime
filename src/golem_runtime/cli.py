"""One entry point for everything the runtime does from a terminal."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import tables
from .engine import route_for
from .gates import AutoGate
from .paths import RUN_DIR
from .records import RecordSink
from .runner import Run, fixture_params
from .validate import validate_all_flows, validate_flow


def _params(pairs: list[str] | None, flow_name: str, fixtures: bool) -> dict[str, str]:
    params = fixture_params(flow_name) if fixtures else {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        key = key if key.startswith("${") else "${" + key + "}"
        params[key] = value
    return params


def _new_run_id(flow_name: str) -> str:
    return f"{flow_name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golem-runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("flows", help="list the flows the tables define")
    v = sub.add_parser("validate", help="preflight one flow, or all of them")
    v.add_argument("flow", nargs="?")
    r = sub.add_parser("routes", help="show the engine route each actor resolves to")
    r.add_argument("flow", nargs="?", default="design-robot")

    run = sub.add_parser("run", help="run a flow")
    run.add_argument("flow", nargs="?", default="design-robot")
    run.add_argument("--run-id")
    run.add_argument("--transport", choices=["echo", "bridge"], default="echo")
    run.add_argument("--gate", choices=["auto", "telegram"], default="auto")
    run.add_argument("--store", choices=["sqlite", "memory"], default="sqlite")
    run.add_argument("--param", action="append", help="name=value; repeatable")
    run.add_argument("--fixtures", action="store_true", help="fill every undeclared parameter with a placeholder")
    run.add_argument("--resume", action="store_true", help="continue an existing run from its checkpoint")

    show = sub.add_parser("record", help="print a run's structured record")
    show.add_argument("run_id")
    show.add_argument("--event")

    ex = sub.add_parser("export", help="write a finished run's outputs as readable files")
    ex.add_argument("run_id")
    ex.add_argument("--to", type=Path, required=True)

    sub.add_parser("runs", help="list finished runs")
    sub.add_parser("bridge-status", help="ask the secret bridge which providers it serves")

    args = parser.parse_args(argv)

    if args.command == "flows":
        print("\n".join(tables.flow_names()))
    elif args.command == "validate":
        report = validate_flow(args.flow) if args.flow else validate_all_flows()
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    elif args.command == "routes":
        actors = sorted({row["actor"] for row in tables.flow(args.flow)})
        for actor in actors:
            agent = tables.resolve_actor(actor)
            print(f"{actor:20s} -> {agent['agent']:20s} {[str(x) for x in route_for(actor)]}")
    elif args.command == "run":
        run_id = args.run_id or _new_run_id(args.flow)
        if args.gate == "telegram":
            from .gates import TelegramGate

            gate = TelegramGate()
        else:
            gate = AutoGate()
        runner = Run(run_id, args.flow, gate=gate, transport=args.transport, state_store=args.store)
        summary = runner.execute(_params(args.param, args.flow, args.fixtures), resume=args.resume)
        print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
        return 0 if summary["status"] == "completed" else 1
    elif args.command == "record":
        sink = RecordSink(args.run_id)
        for entry in sink.read():
            if args.event and entry["event"] != args.event:
                continue
            print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    elif args.command == "export":
        from .export import export

        print(json.dumps(export(args.run_id, args.to), indent=2, sort_keys=True))
    elif args.command == "runs":
        for path in sorted(Path(RUN_DIR).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            print(f"{data['run_id']:44s} {data['status']:10s} {str(data.get('terminal')):16s} steps={data['steps_executed']}")
    elif args.command == "bridge-status":
        from .secrets_bridge import BridgeClient

        print(json.dumps(BridgeClient().ping(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
