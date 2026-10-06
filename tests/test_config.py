"""Tests for watchlist config parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tripwire.config import ConfigError, load_watchlist, parse_watchlist


def test_parse_full_yaml_structure():
    data = {
        "default_cooldown_seconds": 45,
        "symbols": [
            {
                "symbol": "BTC-USD",
                "feed": "coinbase",
                "rules": [
                    {"stop_below": 50000, "cooldown_seconds": 120},
                    {"target_above": 100000},
                    {"pct_move": 2.5, "within_seconds": 180},
                    {
                        "volume_spike": 3.0,
                        "window_seconds": 60,
                        "baseline_seconds": 300,
                    },
                ],
            }
        ],
    }
    symbols = parse_watchlist(data)
    assert len(symbols) == 1
    s = symbols[0]
    assert s.symbol == "BTC-USD"
    assert s.feed == "coinbase"
    assert len(s.rules) == 4
    kinds = {r.kind for r in s.rules}
    assert kinds == {"stop_below", "target_above", "pct_move", "volume_spike"}
    stop = next(r for r in s.rules if r.kind == "stop_below")
    assert stop.value == 50000
    assert stop.cooldown_seconds == 120
    pct = next(r for r in s.rules if r.kind == "pct_move")
    assert pct.window_seconds == 180
    assert pct.cooldown_seconds == 45  # default


def test_shorthand_rules_on_symbol():
    data = {
        "symbols": [
            {
                "symbol": "ETH-USD",
                "stop_below": 1000,
                "target_above": 5000,
                "cooldown_seconds": 90,
            }
        ]
    }
    symbols = parse_watchlist(data)
    assert len(symbols[0].rules) == 2
    assert {r.kind for r in symbols[0].rules} == {"stop_below", "target_above"}
    assert all(r.cooldown_seconds == 90 for r in symbols[0].rules)


def test_list_root():
    symbols = parse_watchlist([{"symbol": "SOL-USD", "rules": [{"target_above": 1}]}])
    assert symbols[0].symbol == "SOL-USD"


def test_duplicate_symbol_rejected():
    with pytest.raises(ConfigError, match="Duplicate"):
        parse_watchlist(
            {
                "symbols": [
                    {"symbol": "BTC-USD", "rules": []},
                    {"symbol": "btc-usd", "rules": []},
                ]
            }
        )


def test_invalid_rule():
    with pytest.raises(ConfigError, match="one of"):
        parse_watchlist({"symbols": [{"symbol": "X", "rules": [{"nope": 1}]}]})


def test_invalid_feed():
    with pytest.raises(ConfigError, match="Invalid feed"):
        parse_watchlist({"symbols": [{"symbol": "X", "feed": "nasdaq"}]})


def test_load_yaml_and_json(tmp_path: Path):
    yml = tmp_path / "w.yaml"
    yml.write_text(
        yaml.dump({"symbols": [{"symbol": "BTC-USD", "rules": [{"stop_below": 1}]}]}),
        encoding="utf-8",
    )
    js = tmp_path / "w.json"
    js.write_text(
        json.dumps({"symbols": [{"symbol": "ETH-USD", "rules": [{"target_above": 9}]}]}),
        encoding="utf-8",
    )
    assert load_watchlist(yml)[0].symbol == "BTC-USD"
    assert load_watchlist(js)[0].symbol == "ETH-USD"


def test_empty_file(tmp_path: Path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    assert load_watchlist(p) == []


def test_example_file_loads():
    root = Path(__file__).resolve().parents[1]
    example = root / "config" / "watchlist.example.yaml"
    symbols = load_watchlist(example)
    assert len(symbols) >= 2
    assert any(s.symbol == "BTC-USD" for s in symbols)
