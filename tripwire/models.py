"""Shared data models for ticks, rules, and alerts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: Optional[datetime] = None) -> str:
    d = dt or utc_now()
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Tick:
    symbol: str
    price: float
    volume: float = 0.0
    source: str = ""
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "volume": self.volume,
            "source": self.source,
            "timestamp": iso_utc(self.timestamp),
        }


@dataclass
class RuleConfig:
    """One rule attached to a watchlist symbol."""

    kind: str  # stop_below | target_above | pct_move | volume_spike
    value: float
    window_seconds: float = 60.0  # for pct_move / volume_spike
    baseline_seconds: float = 300.0  # for volume_spike baseline
    cooldown_seconds: float = 60.0


@dataclass
class SymbolConfig:
    symbol: str
    feed: str = "auto"  # auto | coinbase | kraken | finnhub
    rules: list[RuleConfig] = field(default_factory=list)
    cooldown_seconds: float = 60.0  # default cooldown for rules without override


@dataclass
class Alert:
    symbol: str
    rule: str
    trigger_value: float
    last_price: float
    timestamp: str
    source: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "rule": self.rule,
            "trigger_value": self.trigger_value,
            "last_price": self.last_price,
            "timestamp": self.timestamp,
            "source": self.source,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload
