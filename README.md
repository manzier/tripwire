# Tripwire

**Always-on market monitor and alert system.** Tripwire listens to real-time WebSocket price feeds and fires an alert the instant a configured condition trips.

> **Alerts only. Never places trades.**  
> Tripwire is intentionally one-way: it watches markets and notifies a webhook (or a local log) so a *separate* trading agent can decide what to do. It has no exchange order APIs, no private trading keys, and no execution path.

**Not financial advice.** This software is a developer tool for monitoring public market data. Use at your own risk. Past price moves do not predict future results. You are solely responsible for any trading decisions made by systems that consume these alerts.

## Features

- **Coinbase Advanced Trade** public WebSocket (`ticker` channel) — no API key
- **Kraken** public WebSocket v2 (`ticker`) — no API key; used as crypto fallback/alternative
- **Finnhub** stock trades WebSocket — only when `FINNHUB_API_KEY` is set; otherwise skipped cleanly
- Auto-reconnect with exponential backoff on every feed
- YAML/JSON watchlist with `stop_below`, `target_above`, `pct_move`, `volume_spike`
- Per-rule `cooldown_seconds` to avoid alert spam
- Hot-reload of the watchlist file (edit stops without restarting)
- Webhook POST (`TRIPWIRE_WEBHOOK_URL`) + local JSONL alert log
- Heartbeat file every minute (connected feeds + last tick times)
- Headless-friendly: `scripts/run.sh` (nohup) and an example systemd unit

## Requirements

- Python 3.11+
- Linux box recommended for always-on use

## Install

```bash
git clone https://github.com/manzier/tripwire.git
cd tripwire
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp config/watchlist.example.yaml config/watchlist.yaml
cp .env.example .env   # optional
```

## Configuration

### Watchlist (`config/watchlist.yaml`)

```yaml
default_cooldown_seconds: 60

symbols:
  - symbol: BTC-USD
    feed: coinbase          # auto | coinbase | kraken | finnhub
    rules:
      - stop_below: 50000
        cooldown_seconds: 120
      - target_above: 200000
      - pct_move: 2.0
        within_seconds: 300
      - volume_spike: 3.0
        window_seconds: 60
        baseline_seconds: 300

  - symbol: ETH-USD
    feed: auto
    stop_below: 1000        # shorthand also works
    target_above: 10000

  - symbol: AAPL
    feed: finnhub           # needs FINNHUB_API_KEY
    rules:
      - pct_move: 1.5
        window_seconds: 120
```

| Rule | Meaning |
|------|---------|
| `stop_below` | Alert when last price ≤ value |
| `target_above` | Alert when last price ≥ value |
| `pct_move` | Alert when \|% change\| over `within_seconds` / `window_seconds` ≥ value |
| `volume_spike` | Alert when recent-window volume ≥ `value` × baseline-normalized volume |

The watchlist is **hot-reloaded** when the file changes on disk.

`config/watchlist.yaml` is gitignored — commit only `config/watchlist.example.yaml`.

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TRIPWIRE_WEBHOOK_URL` | No | If set, each alert is POSTed as JSON. If unset, log only. |
| `TRIPWIRE_WEBHOOK_KEY` | No | Sent as `Authorization: Bearer <key>` and `X-Webhook-Key` |
| `FINNHUB_API_KEY` | No | Enables Finnhub stock trades. If unset, Finnhub is skipped with a log line. |

### Alert payload

```json
{
  "symbol": "BTC-USD",
  "rule": "stop_below",
  "trigger_value": 50000,
  "last_price": 49950.12,
  "timestamp": "2026-10-06T14:30:00.123456Z",
  "source": "coinbase",
  "detail": { "condition": "price <= 50000" }
}
```

Every alert is also appended to `logs/alerts.jsonl`. Webhook POSTs are retried a few times with short backoff.

## CLI

```bash
python -m tripwire check-config -c config/watchlist.yaml
python -m tripwire test-alert          # synthetic alert (webhook if configured)
python -m tripwire run                 # start monitor (foreground)
python -m tripwire run --duration 30   # smoke / timed run
```

## Ops

**nohup helper:**

```bash
./scripts/run.sh      # writes logs/tripwire.pid, logs/tripwire.out, logs/tripwire.err
./scripts/stop.sh
```

**systemd** (see `deploy/tripwire.service`): copy the unit, set `WorkingDirectory` / `EnvironmentFile`, then:

```bash
sudo systemctl enable --now tripwire
```

Heartbeat status is written about once a minute to `logs/heartbeat.json`.

## Design

```
WebSocket feeds ──► Rule engine ──► Alert sink ──► webhook + JSONL
                         ▲
                  watchlist.yaml (hot-reload)
```

Tripwire never imports exchange trading SDKs and never sends authenticated order requests. A separate agent should consume the webhook or tail `logs/alerts.jsonl`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
