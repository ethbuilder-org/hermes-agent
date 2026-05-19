#!/usr/bin/env bash
# Runs every 5 min. Alerts Telegram if anyone (Hermes, human, anything)
# changes TimeoutStopSec or restart_drain_timeout away from the permfix values.
TS=$(grep "^TimeoutStopSec=" /etc/systemd/system/hermes-gateway.service | cut -d= -f2)
RD=$(grep "^\s*restart_drain_timeout:" /root/.hermes/config.yaml | awk '{print $2}')
if [ "$TS" != "30" ] || [ "$RD" != "30" ]; then
    msg="⚠️ PERMFIX DRIFT: TimeoutStopSec=$TS (want 30), restart_drain_timeout=$RD (want 30)"
    logger -t permfix-sentinel "$msg"
    # If user has tg-notify, also ping Telegram
    [ -x /root/tg-notify ] && /root/tg-notify "$msg" 2>/dev/null || true
fi
