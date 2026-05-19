#!/usr/bin/env bash
# Runs every 30 min. Cleans up Hermes's stale tmp files and idle chromium.
set -uo pipefail

# 1) Stale tmp leftovers > 24h
find /tmp -maxdepth 1 -type f -mtime +1 \( \
    -name "hermes-cwd-*.txt" -o \
    -name "hermes-snap-*.sh" -o \
    -name "hermes-audio-test.*" \
\) -delete 2>/dev/null || true

# 2) Idle chromium > 1h with <1% CPU avg (heuristic: ETIME > 3600 AND %CPU < 1)
ps -eo pid,etimes,pcpu,cmd | awk '
    $4 ~ /chromium|chrome-linux64\/chrome/ && $2 > 3600 && $3 < 1.0 { print $1 }
' | while read -r pid; do
    # Don't kill if it's a child of an active hermes-gateway (legitimate session)
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    if [ -n "$ppid" ]; then
        pcmd=$(ps -o cmd= -p "$ppid" 2>/dev/null)
        if echo "$pcmd" | grep -q "hermes gateway run"; then
            continue  # alive gateway owns it; leave alone
        fi
    fi
    kill "$pid" 2>/dev/null && logger -t hermes-janitor "killed idle chromium pid=$pid"
done

# 3) Orphan chrome profile dirs in /tmp older than 7 days
find /tmp -maxdepth 1 -type d -name "chrome-*-profile" -mtime +7 \
    -exec rm -rf {} \; 2>/dev/null || true
