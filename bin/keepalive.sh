#!/usr/bin/env bash
# Keep the secret bridge and the current flow run alive. Driven by CRON, once a minute.
#
# WHY CRON AND NOT A DETACHED LOOP. Both were tried on 2026-09-01 and both died silently:
# the secret bridge and its own supervisor vanished together, and so did the run, each time
# with an empty log and no record. A process started from an interactive session is a child
# of that session however hard you detach it, and something upstream reaps it. Cron is not:
# it is started by the system, once a minute, with no parent to lose.
#
# The run id it should keep alive is a file, not an argument: var/CURRENT_RUN. Empty or
# missing means "no run to watch", which is the correct state between runs.
set -uo pipefail
ROOT="/srv/runtime"
export PYTHONPATH="$ROOT/lib:$ROOT/src"
cd "$ROOT" || exit 0

# --- the secret bridge: without it every engine call is Connection refused
if ! timeout 15 python3 -m golem_runtime bridge-status >/dev/null 2>&1; then
  echo "[$(date '+%F %T')] bridge not answering - starting it"
  rm -f "$ROOT/var/secrets.sock"
  setsid nohup python3 -m golem_runtime.secrets_bridge >> "$ROOT/var/bridge.log" 2>&1 < /dev/null &
  sleep 6
fi

# --- the run
RID="$(cat "$ROOT/var/CURRENT_RUN" 2>/dev/null || true)"
[ -n "$RID" ] || exit 0
STATUS="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['status'])" "$ROOT/var/runs/$RID.json" 2>/dev/null || echo unknown)"
if [ "$STATUS" = "completed" ]; then
  echo "[$(date '+%F %T')] $RID completed - nothing to keep alive"
  rm -f "$ROOT/var/CURRENT_RUN"
  exit 0
fi
PID="$(cat "$ROOT/var/$RID.pid" 2>/dev/null || echo -1)"
if [ -z "$PID" ] || [ "$PID" -le 1 ] || ! kill -0 "$PID" 2>/dev/null; then
  echo "[$(date '+%F %T')] $RID not running (status=$STATUS) - resuming it"
  setsid nohup python3 -u /tmp/launch_run.py >> "$ROOT/var/run_$RID.log" 2>&1 < /dev/null &
  echo $! > "$ROOT/var/$RID.pid"
fi
