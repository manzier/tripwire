"""Watchlist config loading and hot-reload."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from tripwire.models import RuleConfig, SymbolConfig

log = logging.getLogger("tripwire.config")

VALID_RULES = {"stop_below", "target_above", "pct_move", "volume_spike"}
VALID_FEEDS = {"auto", "coinbase", "kraken", "finnhub"}


class ConfigError(ValueError):
    """Invalid watchlist configuration."""


def _parse_rule(raw: dict[str, Any], default_cooldown: float) -> RuleConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"Rule must be a mapping, got {type(raw).__name__}")

    kind: Optional[str] = None
    value: Optional[float] = None
    window_seconds = float(raw.get("window_seconds", raw.get("within_seconds", 60)))
    baseline_seconds = float(raw.get("baseline_seconds", 300))
    cooldown = float(raw.get("cooldown_seconds", default_cooldown))

    for k in VALID_RULES:
        if k in raw:
            kind = k
            value = float(raw[k])
            break

    if kind is None or value is None:
        raise ConfigError(
            f"Rule must include one of {sorted(VALID_RULES)}; got keys {list(raw.keys())}"
        )

    if kind == "pct_move" and "within_seconds" in raw:
        window_seconds = float(raw["within_seconds"])
    if kind == "pct_move" and "window_seconds" in raw:
        window_seconds = float(raw["window_seconds"])

    return RuleConfig(
        kind=kind,
        value=value,
        window_seconds=window_seconds,
        baseline_seconds=baseline_seconds,
        cooldown_seconds=cooldown,
    )


def _parse_symbol(item: dict[str, Any], default_cooldown: float) -> SymbolConfig:
    if "symbol" not in item:
        raise ConfigError(f"Watchlist entry missing 'symbol': {item}")
    symbol = str(item["symbol"]).strip()
    if not symbol:
        raise ConfigError("Empty symbol")

    feed = str(item.get("feed", "auto")).lower()
    if feed not in VALID_FEEDS:
        raise ConfigError(f"Invalid feed '{feed}' for {symbol}; expected {sorted(VALID_FEEDS)}")

    sym_cooldown = float(item.get("cooldown_seconds", default_cooldown))
    rules_raw = item.get("rules", [])
    if rules_raw is None:
        rules_raw = []
    if not isinstance(rules_raw, list):
        raise ConfigError(f"'rules' for {symbol} must be a list")

    # Also allow top-level shorthand rules on the symbol itself
    shorthand = {k: item[k] for k in VALID_RULES if k in item}
    if shorthand:
        # wrap as a single rule dict
        extra = dict(shorthand)
        if "within_seconds" in item:
            extra["within_seconds"] = item["within_seconds"]
        if "window_seconds" in item:
            extra["window_seconds"] = item["window_seconds"]
        if "baseline_seconds" in item:
            extra["baseline_seconds"] = item["baseline_seconds"]
        if "cooldown_seconds" in item:
            extra["cooldown_seconds"] = item["cooldown_seconds"]
        # If multiple shorthand keys, each becomes its own rule
        for k in VALID_RULES:
            if k in shorthand:
                r = {"cooldown_seconds": sym_cooldown}
                r[k] = shorthand[k]
                for wk in ("within_seconds", "window_seconds", "baseline_seconds"):
                    if wk in item:
                        r[wk] = item[wk]
                rules_raw = list(rules_raw) + [r]

    rules = [_parse_rule(r, sym_cooldown) for r in rules_raw]
    return SymbolConfig(symbol=symbol, feed=feed, rules=rules, cooldown_seconds=sym_cooldown)


def parse_watchlist(data: dict[str, Any] | list[Any]) -> list[SymbolConfig]:
    """Parse a loaded YAML/JSON document into SymbolConfig list."""
    if isinstance(data, list):
        entries = data
        default_cooldown = 60.0
    elif isinstance(data, dict):
        default_cooldown = float(data.get("default_cooldown_seconds", 60))
        entries = data.get("symbols") or data.get("watchlist") or []
        if not isinstance(entries, list):
            raise ConfigError("'symbols' / 'watchlist' must be a list")
    else:
        raise ConfigError(f"Watchlist root must be mapping or list, got {type(data).__name__}")

    symbols = [_parse_symbol(e, default_cooldown) for e in entries]
    seen: set[str] = set()
    for s in symbols:
        key = s.symbol.upper()
        if key in seen:
            raise ConfigError(f"Duplicate symbol: {s.symbol}")
        seen.add(key)
    return symbols


def load_watchlist(path: str | Path) -> list[SymbolConfig]:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Watchlist file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        # try YAML first, then JSON
        try:
            data = yaml.safe_load(text)
        except Exception:
            data = json.loads(text)
    if data is None:
        return []
    return parse_watchlist(data)


class WatchlistWatcher:
    """Hot-reload watchlist when mtime changes."""

    def __init__(
        self,
        path: str | Path,
        on_change: Optional[Callable[[list[SymbolConfig]], None]] = None,
        poll_interval: float = 1.0,
    ) -> None:
        self.path = Path(path)
        self.on_change = on_change
        self.poll_interval = poll_interval
        self._mtime: Optional[float] = None
        self._symbols: list[SymbolConfig] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def symbols(self) -> list[SymbolConfig]:
        with self._lock:
            return list(self._symbols)

    def load_once(self) -> list[SymbolConfig]:
        symbols = load_watchlist(self.path)
        with self._lock:
            self._symbols = symbols
            try:
                self._mtime = self.path.stat().st_mtime
            except OSError:
                self._mtime = None
        return symbols

    def start(self) -> None:
        self.load_once()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="watchlist-hotreload", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                continue
            if self._mtime is not None and mtime == self._mtime:
                continue
            try:
                symbols = load_watchlist(self.path)
            except Exception as exc:
                log.warning("Hot-reload failed (%s); keeping previous config: %s", self.path, exc)
                self._mtime = mtime
                continue
            with self._lock:
                self._symbols = symbols
                self._mtime = mtime
            log.info("Watchlist reloaded from %s (%d symbols)", self.path, len(symbols))
            if self.on_change:
                try:
                    self.on_change(symbols)
                except Exception:
                    log.exception("on_change callback failed")
