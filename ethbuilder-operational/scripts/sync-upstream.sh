#!/usr/bin/env bash
# /root/.hermes/scripts/sync-upstream.sh
#
# Periodic upstream-sync workflow for our hermes-agent fork.
# Run when you want NousResearch's latest features incorporated.
#
# Flow:
#   1. Fetch NousResearch/hermes-agent (the upstream)
#   2. Merge upstream/main into our fork's main (we ALREADY have our patches there)
#   3. If conflicts, you resolve them ONCE — locally with $EDITOR
#   4. Push the merged main to our fork
#   5. Restart hermes-gateway
#
# Why this is the permanent fix:
#   - Our patches live on fork main alongside upstream history
#   - hermes update pulls origin/main = our patched + merged state
#   - No more per-update conflict surface; one conflict resolution per
#     time we choose to pull upstream (typically weeks apart)

set -euo pipefail

REPO=/root/.hermes/hermes-agent
cd "$REPO"

echo "[1/5] fetch upstream..."
git fetch upstream main 2>&1 | tail -3

echo "[2/5] checkout main..."
git checkout main
git pull origin main --ff-only 2>&1 | tail -2

echo "[3/5] merge upstream/main..."
if git merge upstream/main --no-edit -m "merge upstream/main"; then
    echo "      clean merge"
else
    echo
    echo "  *** CONFLICT *** resolve manually then run:"
    echo "    cd $REPO"
    echo "    \$EDITOR <conflicted-files>"
    echo "    git add <resolved-files>"
    echo "    git commit --no-edit"
    echo "    git push origin main"
    echo "    systemctl restart hermes-gateway"
    exit 1
fi

echo "[4/5] push merged main to fork..."
git push origin main 2>&1 | tail -3

echo "[5/5] restart gateway..."
systemctl restart hermes-gateway
sleep 2
echo "      gateway: $(systemctl is-active hermes-gateway)"

echo
echo "sync done. main is now: $(git log -1 --oneline main)"
