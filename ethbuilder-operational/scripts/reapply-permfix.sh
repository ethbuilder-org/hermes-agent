#!/usr/bin/env bash
# /root/.hermes/scripts/reapply-permfix.sh
#
# DEPRECATED for normal use. The permfix is now baked into fork main
# (ethbuilder-org/hermes-agent), so `hermes update` pulls origin/main
# and gets the patches automatically.
#
# This script remains as a manual rescue: if the working tree somehow
# gets out of sync with origin/main, re-apply the saved patch.
#
# Normal workflow:
#   1. `hermes update` (just works — pulls our patched main)
#   2. `/root/.hermes/scripts/sync-upstream.sh` (periodic, pulls
#      NousResearch features into our fork main)
#
# This file: only when both of the above misbehave.

set -euo pipefail
REPO=/root/.hermes/hermes-agent
PATCH=$(ls -1t /root/.hermes/patches/ethbuilder-permfix-*.patch | head -1)
[ -f "$PATCH" ] || { echo "no patch found"; exit 1; }
cd "$REPO"

# Last-ditch: hard reset to origin/main (which contains permfix), then verify.
git fetch origin main
git checkout main
git reset --hard origin/main
echo "reset to origin/main: $(git log -1 --oneline)"
echo

# Config + systemd unit (not under repo)
sed -i "s/^\(\s*restart_drain_timeout:\)\s*[0-9]\+\s*$/\1 120/" /root/.hermes/config.yaml
UNIT=/etc/systemd/system/hermes-gateway.service
sed -i "/^SendSIGKILLOnTimeout=/d" "$UNIT"
sed -i "s/^TimeoutStopSec=[0-9]\+/TimeoutStopSec=150/" "$UNIT"
# Ensure SuccessExitStatus=75 is present (clean drain ≠ failure)
grep -q 'SuccessExitStatus=75' "$UNIT" || sed -i '/^KillSignal=SIGTERM$/a SuccessExitStatus=75' "$UNIT"
mount -o remount,ro /etc 2>/dev/null || true
systemctl daemon-reload
echo "permfix applied: drain=120, TimeoutStopSec=150, SuccessExitStatus=75 (no restart)"
