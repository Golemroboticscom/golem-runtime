#!/usr/bin/env bash
# Keep the secret bridge alive.
#
# The bridge is a plain background process, not a systemd unit, because this runtime has
# no root and cannot install one. On 2026-08-31 it died twice without warning and each
# time it took a 48-step run down with it at whatever step was in flight -- the run cannot
# reach any engine without it.
#
# So it gets a supervisor: check every few seconds, start it if the socket does not answer.
# Cheap, no privilege, and it survives whatever reaps a detached child.
#
#   setsid nohup bin/bridge_supervisor.sh > var/supervisor.log 2>&1 < /dev/null &
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/lib:$ROOT/src"

while true; do
  if ! timeout 15 python3 -m golem_runtime bridge-status >/dev/null 2>&1; then
    echo "[$(date '+%F %T')] bridge not answering - starting it"
    setsid nohup python3 -m golem_runtime.secrets_bridge >> "$ROOT/var/bridge.log" 2>&1 < /dev/null &
    sleep 5
  fi
  sleep 10
done
