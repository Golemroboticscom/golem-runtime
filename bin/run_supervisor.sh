#!/usr/bin/env bash
# Keep ONE flow run alive until it finishes.
#
# The bridge already has a supervisor for the same reason: this runtime has no root and
# cannot install a systemd unit, and a detached run that dies takes an evening of work with
# it. On 2026-09-01 a resumed run vanished between two gates with an empty log and no
# run_end record, and the only symptom Yakov saw was that the approve button did nothing --
# because nobody was listening any more.
#
# Liveness is tracked by PIDFILE, not by pgrep: a `pgrep -f launch_run.py` also matches the
# shell that is asking the question, so the supervisor concluded the run was alive when
# nothing was. A pid it started itself cannot lie to it.
#
# It restarts with --resume, so the checkpoint decides where work continues; it never
# repeats a step. It stops for good when the run's summary says completed.
set -uo pipefail
RID="${1:?usage: run_supervisor.sh <run-id>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="$ROOT/var/$RID.pid"

status_of() {
  python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['status'])" "$ROOT/var/runs/$RID.json" 2>/dev/null || echo unknown
}

while true; do
  if [ "$(status_of)" = "completed" ]; then
    echo "[$(date '+%F %T')] $RID completed - supervisor stopping"
    exit 0
  fi
  # NOT `echo 0` as the empty default: `kill -0 0` signals the whole process GROUP and
  # succeeds, so a missing pidfile read as "alive" and the supervisor never started anything.
  PID="$(cat "$PIDFILE" 2>/dev/null || echo -1)"
  if [ -z "$PID" ] || [ "$PID" -le 1 ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "[$(date '+%F %T')] $RID not running - resuming it"
    setsid nohup python3 -u /tmp/launch_run.py >> "$ROOT/var/run_$RID.log" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    sleep 25
  fi
  sleep 15
done
