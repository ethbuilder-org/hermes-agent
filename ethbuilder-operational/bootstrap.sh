#!/usr/bin/env bash
# ethbuilder-operational bootstrap — idempotent installer.
#
# Run on a fresh hermes-new VPS after `git clone https://github.com/ethbuilder-org/hermes-agent`.
# Recreates: systemd units, chattr wrapper, scripts, sentinel timer.
# Does NOT touch: /root/.hermes/config.yaml or CLAUDE.md (you must manually
# fill in your tokens/keys from the .template companions).

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "[1/6] systemd units..."
install -m 644 "$HERE/systemd/hermes-gateway.service"     /etc/systemd/system/
install -m 644 "$HERE/systemd/permfix-sentinel.service"   /etc/systemd/system/
install -m 644 "$HERE/systemd/permfix-sentinel.timer"     /etc/systemd/system/

echo "[2/6] chattr wrapper..."
install -m 755 "$HERE/bin/chattr-wrapper" /usr/local/bin/chattr

echo "[3/6] scripts..."
mkdir -p /root/.hermes/scripts
install -m 755 "$HERE/scripts/"*.sh /root/.hermes/scripts/
install -m 755 "$HERE/scripts/"*.py /root/.hermes/scripts/

echo "[4/6] config templates (NOT live-installed; you must merge in secrets)..."
if [ ! -f /root/.hermes/CLAUDE.md ]; then
    cp "$HERE/config/CLAUDE.md" /root/.hermes/CLAUDE.md
    echo "   warning: CLAUDE.md installed with <REDACTED-VPS-PASSWORD> placeholder"
fi
if [ ! -f /root/.hermes/config.yaml ]; then
    cp "$HERE/config/config.yaml.template" /root/.hermes/config.yaml
    echo "   warning: config.yaml installed from template — fill in api_key, bot token, etc."
fi

echo "[5/6] enable + lock immutables..."
systemctl daemon-reload
systemctl enable --now permfix-sentinel.timer
# Only re-chattr if not already immutable
lsattr /etc/systemd/system/hermes-gateway.service 2>/dev/null | grep -q '^----i' || \
    /usr/bin/chattr +i /etc/systemd/system/hermes-gateway.service
lsattr /root/.hermes/config.yaml 2>/dev/null | grep -q '^----i' || \
    /usr/bin/chattr +i /root/.hermes/config.yaml

echo "[6/6] done."
echo
echo "Next steps:"
echo "  - Edit /root/.hermes/CLAUDE.md (replace <REDACTED-VPS-PASSWORD>)"
echo "  - Edit /root/.hermes/config.yaml (fill api keys, bot token)"
echo "  - Start gateway: systemctl start hermes-gateway"
