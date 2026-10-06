"""Kraken public WebSocket v2 — ticker channel."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from websockets.asyncio.client import connect

from tripwire.feeds.base import BaseFeed, TickHandler
from tripwire.models import Tick

log = logging.getLogger("tripwire.feeds.kraken")

KRAKEN_WS = "wss://ws.kraken.com/v2"


def to_kraken_symbol(symbol: str) -> str:
    """Map Coinbase-style BTC-USD to Kraken BTC/USD."""
    s = symbol.strip().upper().replace("-", "/").replace("_", "/")
    # common aliases
    aliases = {"BTC": "BTC", "XBT": "BTC", "ETH": "ETH"}
    if "/" in s:
        base, quote = s.split("/", 1)
        base = aliases.get(base, base)
        return f"{base}/{quote}"
    return s


def from_kraken_symbol(symbol: str) -> str:
    return symbol.replace("/", "-")


class KrakenFeed(BaseFeed):
    name = "kraken"

    def __init__(self, on_tick: TickHandler, url: str = KRAKEN_WS, **kwargs: Any) -> None:
        super().__init__(on_tick=on_tick, **kwargs)
        self.url = url
        self._sym_map: dict[str, str] = {}  # kraken -> original

    def set_symbols(self, symbols: list[str]) -> None:
        self._sym_map = {to_kraken_symbol(s): s for s in symbols}
        kraken_syms = list(self._sym_map.keys())
        super().set_symbols(kraken_syms)

    async def _connect_and_listen(self) -> None:
        async with connect(self.url, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
            sub = {
                "method": "subscribe",
                "params": {
                    "channel": "ticker",
                    "symbol": self._symbols,
                },
            }
            await ws.send(json.dumps(sub))
            self.status.connected = True
            log.info("[%s] subscribed to ticker: %s", self.name, self._symbols)

            async for raw in ws:
                if self._stop.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle(msg)

    async def _handle(self, msg: dict[str, Any]) -> None:
        if msg.get("method") in {"subscribe", "unsubscribe", "pong"}:
            if msg.get("error"):
                raise RuntimeError(str(msg["error"]))
            return
        if msg.get("channel") != "ticker":
            return
        if msg.get("type") not in {"update", "snapshot", None}:
            # still process data if present
            pass
        for item in msg.get("data") or []:
            ksym = item.get("symbol")
            if not ksym:
                continue
            # prefer last trade price
            price = item.get("last") or item.get("ask") or item.get("bid")
            if price is None:
                continue
            vol = item.get("volume") or item.get("volatility") or 0
            try:
                price_f = float(price)
                # volume may be a number or nested
                if isinstance(vol, dict):
                    vol_f = float(vol.get("qty") or vol.get("volume") or 0)
                else:
                    vol_f = float(vol or 0)
            except (TypeError, ValueError):
                continue
            original = self._sym_map.get(ksym, from_kraken_symbol(ksym))
            await self._emit(
                Tick(
                    symbol=original,
                    price=price_f,
                    volume=vol_f,
                    source=self.name,
                    timestamp=datetime.now(timezone.utc),
                )
            )
