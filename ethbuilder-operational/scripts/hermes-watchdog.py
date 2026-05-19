#!/usr/bin/env python3
"""Hermes gateway heartbeat watchdog — auto-restarts dead/stuck gateway.

Runs every 2 minutes via cron. Tracks consecutive failures in state file.
After 3 consecutive failures (~6 min of unresponsiveness), restarts gateway
and notifies Telegram.
"""

import os
import sys
import subprocess
import time

STATE_FILE = "/tmp/hermes-watchdog-state.json"
GATEWAY_LOG = "/root/.hermes/logs/gateway.log"
TG_CMD = ["/root/tg-notify"]
MAX_FAILURES = 2  # 2 × 1min = 2 min before restart
CHECK_INTERVAL = 120  # seconds between checks (cron interval)

def load_state():
    import json
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"consecutive_failures": 0, "last_restart": 0, "total_restarts": 0}

def save_state(state):
    import json
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def telegram(msg):
    try:
        subprocess.run(TG_CMD, input=msg, text=True, timeout=10, capture_output=True)
    except Exception:
        pass

def is_gateway_alive():
    """Check if gateway is active AND processing (log was written in last 5 min)."""
    # Check 1: systemd says active
    try:
        r = subprocess.run(["systemctl", "is-active", "hermes-gateway"],
                         capture_output=True, text=True, timeout=5)
        if r.stdout.strip() != "active":
            return False
    except Exception:
        return False

    # Check 2: Log was written in last 10 minutes (gateway is doing work, not zombie)
    try:
        mtime = os.path.getmtime(GATEWAY_LOG)
        if time.time() - mtime > 600:
            return False  # No log activity for 2+ min — may be frozen
    except Exception:
        pass

    return True

def main():
    state = load_state()
    alive = is_gateway_alive()

    if alive:
        if state["consecutive_failures"] > 0:
            # Recovery detected
            telegram(f"✅ Hermes gateway recovered after {state['consecutive_failures']} failures")
        state["consecutive_failures"] = 0
        save_state(state)
        sys.exit(0)

    # Gateway is dead or frozen
    state["consecutive_failures"] += 1
    save_state(state)

    if state["consecutive_failures"] < MAX_FAILURES:
        sys.exit(0)  # Wait for more failures

    # Threshold reached — RESTART
    telegram(f"🚨 Hermes gateway DEAD ({state['consecutive_failures']} consecutive failures). Restarting...")
    try:
        subprocess.run(["systemctl", "restart", "hermes-gateway"], timeout=30, capture_output=True)
        time.sleep(3)
        r = subprocess.run(["systemctl", "is-active", "hermes-gateway"], capture_output=True, text=True, timeout=5)
        if r.stdout.strip() == "active":
            state["consecutive_failures"] = 0
            state["last_restart"] = int(time.time())
            state["total_restarts"] += 1
            save_state(state)
            telegram(f"✅ Hermes gateway restarted (total restarts: {state['total_restarts']})")
        else:
            telegram(f"❌ Hermes gateway restart FAILED. Manual intervention needed.")
    except Exception as e:
        telegram(f"❌ Hermes gateway restart ERROR: {e}")

if __name__ == "__main__":
    main()
