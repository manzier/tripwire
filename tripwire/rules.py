"""Rule evaluation against live ticks. Pure logic — no I/O."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Optional

from tripwire.models import Alert, RuleConfig, SymbolConfig, Tick, iso_utc, utc_now


@dataclass
class _Sample:
    ts: float  # epoch seconds
    price: float
    volume: float


@dataclass
class RuleEngine:
    """Tracks price/volume history and evaluates watchlist rules."""

    max_history_seconds: float = 600.0
    _history: dict[str, Deque[_Sample]] = field(default_factory=lambda: defaultdict(deque))
    _last_alert: dict[tuple[str, str], float] = field(default_factory=dict)

    def _key(self, symbol: str) -> str:
        return symbol.upper()

    def record(self, tick: Tick) -> None:
        key = self._key(tick.symbol)
        ts = tick.timestamp.timestamp() if tick.timestamp.tzinfo else tick.timestamp.replace(tzinfo=timezone.utc).timestamp()
        hist = self._history[key]
        hist.append(_Sample(ts=ts, price=tick.price, volume=tick.volume))
        cutoff = ts - self.max_history_seconds
        while hist and hist[0].ts < cutoff:
            hist.popleft()

    def _cooldown_ok(self, symbol: str, rule_kind: str, cooldown: float, now: float) -> bool:
        key = (self._key(symbol), rule_kind)
        last = self._last_alert.get(key)
        if last is not None and (now - last) < cooldown:
            return False
        return True

    def _mark_alert(self, symbol: str, rule_kind: str, now: float) -> None:
        self._last_alert[(self._key(symbol), rule_kind)] = now

    def evaluate(self, tick: Tick, symbol_cfg: SymbolConfig) -> list[Alert]:
        alerts: list[Alert] = []
        now = tick.timestamp.timestamp() if tick.timestamp.tzinfo else utc_now().timestamp()
        key = self._key(tick.symbol)
        hist = self._history[key]

        for rule in symbol_cfg.rules:
            alert = self._eval_one(tick, rule, hist, now)
            if alert is None:
                continue
            if not self._cooldown_ok(tick.symbol, rule.kind, rule.cooldown_seconds, now):
                continue
            self._mark_alert(tick.symbol, rule.kind, now)
            alerts.append(alert)
        return alerts

    def _eval_one(
        self,
        tick: Tick,
        rule: RuleConfig,
        hist: Deque[_Sample],
        now: float,
    ) -> Optional[Alert]:
        kind = rule.kind
        if kind == "stop_below":
            if tick.price <= rule.value:
                return Alert(
                    symbol=tick.symbol,
                    rule=kind,
                    trigger_value=rule.value,
                    last_price=tick.price,
                    timestamp=iso_utc(tick.timestamp),
                    source=tick.source,
                    detail={"condition": f"price <= {rule.value}"},
                )
            return None

        if kind == "target_above":
            if tick.price >= rule.value:
                return Alert(
                    symbol=tick.symbol,
                    rule=kind,
                    trigger_value=rule.value,
                    last_price=tick.price,
                    timestamp=iso_utc(tick.timestamp),
                    source=tick.source,
                    detail={"condition": f"price >= {rule.value}"},
                )
            return None

        if kind == "pct_move":
            window = rule.window_seconds
            cutoff = now - window
            # oldest sample in window
            base_price: Optional[float] = None
            for s in hist:
                if s.ts >= cutoff:
                    base_price = s.price
                    break
            if base_price is None or base_price == 0:
                return None
            pct = ((tick.price - base_price) / base_price) * 100.0
            if abs(pct) >= abs(rule.value):
                return Alert(
                    symbol=tick.symbol,
                    rule=kind,
                    trigger_value=rule.value,
                    last_price=tick.price,
                    timestamp=iso_utc(tick.timestamp),
                    source=tick.source,
                    detail={
                        "pct_change": round(pct, 6),
                        "base_price": base_price,
                        "window_seconds": window,
                    },
                )
            return None

        if kind == "volume_spike":
            window = rule.window_seconds
            baseline = rule.baseline_seconds
            recent_cut = now - window
            base_cut = now - baseline
            recent_vol = sum(s.volume for s in hist if s.ts >= recent_cut)
            # baseline excludes the recent window to avoid self-comparison inflation
            base_vol = sum(s.volume for s in hist if base_cut <= s.ts < recent_cut)
            base_span = max(baseline - window, 1e-9)
            # normalize baseline to same window length
            expected = base_vol * (window / base_span) if base_vol > 0 else 0.0
            if expected <= 0:
                return None
            multiple = recent_vol / expected
            if multiple >= rule.value:
                return Alert(
                    symbol=tick.symbol,
                    rule=kind,
                    trigger_value=rule.value,
                    last_price=tick.price,
                    timestamp=iso_utc(tick.timestamp),
                    source=tick.source,
                    detail={
                        "volume_multiple": round(multiple, 4),
                        "recent_volume": recent_vol,
                        "expected_volume": expected,
                        "window_seconds": window,
                        "baseline_seconds": baseline,
                    },
                )
            return None

        return None


def find_symbol_config(symbols: list[SymbolConfig], symbol: str) -> Optional[SymbolConfig]:
    key = symbol.upper()
    for s in symbols:
        if s.symbol.upper() == key:
            return s
    return None
