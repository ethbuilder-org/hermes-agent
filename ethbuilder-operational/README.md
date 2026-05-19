# ethbuilder-operational

VPS-side operational files for the ethbuilder Hermes fork. Lives next to the
hermes-agent source in this repo so VPS death or reinstall doesn't lose them.

## What's in here

| Dir | Purpose |
|---|---|
| `systemd/` | Patched `hermes-gateway.service` + `permfix-sentinel.service`/`.timer` |
| `bin/chattr-wrapper` | Shadow for `/usr/local/bin/chattr` — refuses `-i` on protected files |
| `scripts/` | Drift sentinel, sync-upstream helper, reapply-permfix rescue, vision OCR, watchdog |
| `config/CLAUDE.md` | System prompt overlay (incl. HARD RULE block) — REDACTED |
| `config/config.yaml.template` | Hermes config template — REDACTED |

## Recovery on a fresh hermes-new VPS

```bash
git clone https://github.com/ethbuilder-org/hermes-agent /root/.hermes/hermes-agent
cd /root/.hermes/hermes-agent
./ethbuilder-operational/bootstrap.sh
# Then fill secrets in /root/.hermes/CLAUDE.md and /root/.hermes/config.yaml
systemctl start hermes-gateway
```

## Defense stack (10 layers)

| # | Where | Purpose |
|---|---|---|
| 1-5 | `gateway/platforms/_telegram_ratelimit.py` | Per-chat token bucket, dedup, global cap, reactive lockout, edit coalesce |
| 6 | `Bot._do_post` monkey-patch | Catches any code path bypassing 1-5 |
| 7 | `chattr +i` on unit/config | Filesystem immutable bit |
| 8 | `/usr/local/bin/chattr` wrapper | Refuses `-i` without `HERMES_PERMFIX_OVERRIDE=yes` |
| 9 | `permfix-sentinel.timer` (5min) | Alerts via tg-notify if values drift |
| 10 | `CLAUDE.md` HARD RULE | LLM system-prompt prohibition |

## How `hermes update` works with this fork

Our fork's `main` carries both NousResearch's upstream history AND our patches.
`hermes update` pulls `origin/main` — so updates "just work" with our patches
intact. To pull in new upstream features from NousResearch, run
`/root/.hermes/scripts/sync-upstream.sh` (merges `upstream/main` into our
`main`; resolve conflicts ONCE on github, push, then `hermes update`).
