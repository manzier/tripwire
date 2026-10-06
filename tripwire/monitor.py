"""Main monitor: wires feeds, rules, alerts, hot-reload, and heartbeat."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from tripwire.alerts import AlertSink
from tripwire.config import WatchlistWatcher
from tripwire.feeds.coinbase import CoinbaseFeed
from tripwire.feeds.finnhub import FinnhubFeed
from tripwire.feeds.kraken import KrakenFeed
from tripwire.health import Heartbeat
from tripwire.models import SymbolConfig, Tick
from tripwire.rules import RuleEngine, find_symbol_config

log = logging.getLogger("tripwire.monitor")


def classify_symbols(symbols: list[SymbolConfig]) -> dict[str, list[str]]:
    """Assign watchlist symbols to feeds.

    feed=auto: crypto-looking (contains '-' or '/') -> coinbase (+ kraken backup),
    otherwise finnhub. Explicit feed overrides.
    """
    buckets: dict[str, list[str]] = {"coinbase": [], "kraken": [], "finnhub": []}
    for s in symbols:
        feed = s.feed
        sym = s.symbol
        if feed == "auto":
            if "-" in sym or "/" in sym:
                feed = "coinbase"
            else:
                feed = "finnhub"
        if feed == "coinbase":
            buckets["coinbase"].append(sym)
            # also mirror on kraken as fallback/alternative
            buckets["kraken"].append(sym)
        elif feed == "kraken":
            buckets["kraken"].append(sym)
        elif feed == "finnhub":
            buckets["finnhub"].append(sym)
        else:
            log.warning("Unknown feed %s for %s; skipping", feed, sym)
    # dedupe preserve order
    for k, v in buckets.items():
        seen: set[str] = set()
        out: list[str] = []
        for x in v:
            xu = x.upper()
            if xu not in seen:
                seen.add(xu)
                out.append(x)
        buckets[k] = out
    return buckets


class Monitor:
    def __init__(
        self,
        watchlist_path: str | Path = "config/watchlist.yaml",
        alert_log: str | Path = "logs/alerts.jsonl",
        heartbeat_path: str | Path = "logs/heartbeat.json",
        heartbeat_interval: float = 60.0,
    ) -> None:
        self.watchlist_path = Path(watchlist_path)
        self.engine = RuleEngine()
        self.alerts = AlertSink(log_path=alert_log)
        self._symbols: list[SymbolConfig] = []
        self._lock = asyncio.Lock()

        self.coinbase = CoinbaseFeed(on_tick=self._on_tick)
        self.kraken = KrakenFeed(on_tick=self._on_tick)
        self.finnhub = FinnhubFeed(on_tick=self._on_tick)
        self._feeds = [self.coinbase, self.kraken, self.finnhub]

        self.watcher = WatchlistWatcher(self.watchlist_path, on_change=self._on_config_change)
        self.heartbeat = Heartbeat(
            get_statuses=lambda: [f.status for f in self._feeds],
            path=heartbeat_path,
            interval=heartbeat_interval,
        )

    def _on_config_change(self, symbols: list[SymbolConfig]) -> None:
        # called from watcher thread — schedule onto event loop if running
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(lambda: asyncio.create_task(self.apply_symbols(symbols)))
        except RuntimeError:
            # no loop yet
            self._symbols = symbols

    async def apply_symbols(self, symbols: list[SymbolConfig]) -> None:
        async with self._lock:
            self._symbols = symbols
            buckets = classify_symbols(symbols)
            self.coinbase.set_symbols(buckets["coinbase"])
            self.kraken.set_symbols(buckets["kraken"])
            self.finnhub.set_symbols(buckets["finnhub"])
            log.info(
                "Applied watchlist: coinbase=%s kraken=%s finnhub=%s",
                buckets["coinbase"],
                buckets["kraken"],
                buckets["finnhub"],
            )

    async def _on_tick(self, tick: Tick) -> None:
        self.engine.record(tick)
        cfg = find_symbol_config(self._symbols, tick.symbol)
        if cfg is None:
            # try normalized dash form for kraken originals already mapped
            return
        alerts = self.engine.evaluate(tick, cfg)
        for alert in alerts:
            await self.alerts.emit(alert)

    async def run(self, duration: Optional[float] = None) -> None:
        if not self.watchlist_path.exists():
            raise FileNotFoundError(
                f"Watchlist not found: {self.watchlist_path}. "
                "Copy config/watchlist.example.yaml to config/watchlist.yaml"
            )
        self.watcher.start()
        await self.apply_symbols(self.watcher.symbols)
        await self.alerts.start()

        for feed in self._feeds:
            await feed.start()
        await self.heartbeat.start()

        log.info("Tripwire monitor running (alerts only — never places trades)")
        try:
            if duration is not None:
                await asyncio.sleep(duration)
            else:
                stop = asyncio.Event()
                await stop.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self.watcher.stop()
        await self.heartbeat.stop()
        for feed in self._feeds:
            await feed.stop()
        await self.alerts.close()
        log.info("Tripwire stopped")
