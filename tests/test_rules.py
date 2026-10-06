"""Tests for rule evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tripwire.models import RuleConfig, SymbolConfig, Tick
from tripwire.rules import RuleEngine


def _ts(offset_s: float = 0.0) -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


def _tick(price: float, t: float = 0.0, vol: float = 0.0, symbol: str = "BTC-USD") -> Tick:
    return Tick(symbol=symbol, price=price, volume=vol, source="test", timestamp=_ts(t))


def test_stop_below_triggers():
    eng = RuleEngine()
    cfg = SymbolConfig(
        symbol="BTC-USD",
        rules=[RuleConfig(kind="stop_below", value=100.0, cooldown_seconds=0)],
    )
    tick = _tick(99.0)
    eng.record(tick)
    alerts = eng.evaluate(tick, cfg)
    assert len(alerts) == 1
    assert alerts[0].rule == "stop_below"
    assert alerts[0].last_price == 99.0


def test_stop_below_no_trigger():
    eng = RuleEngine()
    cfg = SymbolConfig(
        symbol="BTC-USD",
        rules=[RuleConfig(kind="stop_below", value=100.0, cooldown_seconds=0)],
    )
    tick = _tick(101.0)
    eng.record(tick)
    assert eng.evaluate(tick, cfg) == []


def test_target_above_triggers():
    eng = RuleEngine()
    cfg = SymbolConfig(
        symbol="ETH-USD",
        rules=[RuleConfig(kind="target_above", value=2000.0, cooldown_seconds=0)],
    )
    tick = _tick(2000.0, symbol="ETH-USD")
    eng.record(tick)
    alerts = eng.evaluate(tick, cfg)
    assert len(alerts) == 1
    assert alerts[0].rule == "target_above"


def test_pct_move_triggers():
    eng = RuleEngine()
    cfg = SymbolConfig(
        symbol="BTC-USD",
        rules=[
            RuleConfig(
                kind="pct_move",
                value=1.0,
                window_seconds=60,
                cooldown_seconds=0,
            )
        ],
    )
    t0 = _tick(100.0, t=0)
    eng.record(t0)
    t1 = _tick(101.5, t=30)  # +1.5%
    eng.record(t1)
    alerts = eng.evaluate(t1, cfg)
    assert len(alerts) == 1
    assert alerts[0].rule == "pct_move"
    assert abs(alerts[0].detail["pct_change"] - 1.5) < 1e-6


def test_pct_move_no_trigger_small_move():
    eng = RuleEngine()
    cfg = SymbolConfig(
        symbol="BTC-USD",
        rules=[RuleConfig(kind="pct_move", value=2.0, window_seconds=60, cooldown_seconds=0)],
    )
    eng.record(_tick(100.0, t=0))
    t1 = _tick(100.5, t=10)
    eng.record(t1)
    assert eng.evaluate(t1, cfg) == []


def test_cooldown_suppresses_spam():
    eng = RuleEngine()
    cfg = SymbolConfig(
        symbol="BTC-USD",
        rules=[RuleConfig(kind="target_above", value=1.0, cooldown_seconds=60)],
    )
    t1 = _tick(2.0, t=0)
    eng.record(t1)
    assert len(eng.evaluate(t1, cfg)) == 1
    t2 = _tick(3.0, t=10)
    eng.record(t2)
    assert eng.evaluate(t2, cfg) == []  # within cooldown
    t3 = _tick(4.0, t=70)
    eng.record(t3)
    assert len(eng.evaluate(t3, cfg)) == 1


def test_volume_spike():
    eng = RuleEngine()
    cfg = SymbolConfig(
        symbol="BTC-USD",
        rules=[
            RuleConfig(
                kind="volume_spike",
                value=2.0,
                window_seconds=10,
                baseline_seconds=40,
                cooldown_seconds=0,
            )
        ],
    )
    # baseline window [t=0..30): low volume; recent [t=30..40]: high
    for i in range(0, 30, 5):
        eng.record(_tick(100.0, t=i, vol=1.0))
    for i in range(30, 40, 2):
        eng.record(_tick(100.0, t=i, vol=20.0))
    last = _tick(100.0, t=40, vol=20.0)
    eng.record(last)
    alerts = eng.evaluate(last, cfg)
    assert len(alerts) == 1
    assert alerts[0].rule == "volume_spike"
    assert alerts[0].detail["volume_multiple"] >= 2.0


def test_multiple_rules_same_tick():
    eng = RuleEngine()
    cfg = SymbolConfig(
        symbol="BTC-USD",
        rules=[
            RuleConfig(kind="stop_below", value=50, cooldown_seconds=0),
            RuleConfig(kind="target_above", value=150, cooldown_seconds=0),
        ],
    )
    # neither
    t = _tick(100)
    eng.record(t)
    assert eng.evaluate(t, cfg) == []
    # stop
    t2 = _tick(40, t=1)
    eng.record(t2)
    a = eng.evaluate(t2, cfg)
    assert [x.rule for x in a] == ["stop_below"]
