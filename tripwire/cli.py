"""CLI: python -m tripwire {run,test-alert,check-config}."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from tripwire import __version__
from tripwire.alerts import AlertSink, make_test_alert
from tripwire.config import ConfigError, load_watchlist


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def cmd_check_config(args: argparse.Namespace) -> int:
    path = Path(args.config)
    try:
        symbols = load_watchlist(path)
    except ConfigError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {path} — {len(symbols)} symbol(s)")
    for s in symbols:
        rules = ", ".join(f"{r.kind}={r.value}" for r in s.rules) or "(no rules)"
        print(f"  - {s.symbol} feed={s.feed} rules=[{rules}]")
    return 0


async def _test_alert(args: argparse.Namespace) -> int:
    sink = AlertSink(log_path=args.log)
    await sink.start()
    alert = make_test_alert()
    await sink.emit(alert)
    await sink.close()
    print(f"Test alert written to {sink.log_path}")
    if sink.webhook_url:
        print(f"Webhook POST attempted -> {sink.webhook_url}")
    else:
        print("No TRIPWIRE_WEBHOOK_URL set — log only")
    return 0


def cmd_test_alert(args: argparse.Namespace) -> int:
    return asyncio.run(_test_alert(args))


async def _run(args: argparse.Namespace) -> int:
    from tripwire.monitor import Monitor

    mon = Monitor(
        watchlist_path=args.config,
        alert_log=args.log,
        heartbeat_path=args.heartbeat,
        heartbeat_interval=args.heartbeat_interval,
    )
    await mon.run(duration=args.duration)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    return asyncio.run(_run(args))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tripwire",
        description="Tripwire — market monitor that alerts only (never places trades).",
    )
    p.add_argument("--version", action="version", version=f"tripwire {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Start the monitor")
    run_p.add_argument(
        "-c",
        "--config",
        default="config/watchlist.yaml",
        help="Watchlist path (default: config/watchlist.yaml)",
    )
    run_p.add_argument("--log", default="logs/alerts.jsonl", help="Alert JSONL path")
    run_p.add_argument("--heartbeat", default="logs/heartbeat.json", help="Heartbeat JSON path")
    run_p.add_argument(
        "--heartbeat-interval",
        type=float,
        default=60.0,
        help="Heartbeat interval seconds (default 60)",
    )
    run_p.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional run duration in seconds (for smoke tests)",
    )
    run_p.set_defaults(func=cmd_run)

    test_p = sub.add_parser("test-alert", help="Send a synthetic test alert")
    test_p.add_argument("--log", default="logs/alerts.jsonl", help="Alert JSONL path")
    test_p.set_defaults(func=cmd_test_alert)

    check_p = sub.add_parser("check-config", help="Validate watchlist config")
    check_p.add_argument(
        "-c",
        "--config",
        default="config/watchlist.yaml",
        help="Watchlist path",
    )
    check_p.set_defaults(func=cmd_check_config)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
