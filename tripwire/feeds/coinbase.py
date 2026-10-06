"""Coinbase Advanced Trade public WebSocket — ticker channel."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.asyncio.client import connect

from tripwire.feeds.base import BaseFeed, TickHandler
from tripwire.models import Tick

log = logging.getLogger("tripwire.feeds.coinbase")

COINBASE_WS = "wss://advanced-trade-ws.coinbase.com"


class CoinbaseFeed(BaseFeed):
    name = "coinbase"

    def __init__(self, on_tick: TickHandler, url: str = COINBASE_WS, **kwargs: Any) -> None:
        super().__init__(on_tick=on_tick, **kwargs)
        self.url = url

    async def _connect_and_listen(self) -> None:
        async with connect(self.url, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
            sub = {
                "type": "subscribe",
                "product_ids": self._symbols,
                "channel": "ticker",
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
        channel = msg.get("channel") or msg.get("type")
        if channel == "subscriptions":
            return
        if msg.get("type") == "error":
            raise RuntimeError(msg.get("message") or str(msg))

        events = msg.get("events") or []
        # Advanced Trade ticker: {channel: ticker, events: [{tickers: [...]}]}
        if channel == "ticker" or events:
            for event in events:
                for t in event.get("tickers") or []:
                    product = t.get("product_id") or t.get("productId")
                    price_s = t.get("price")
                    if not product or price_s is None:
                        continue
                    vol_s = t.get("volume_24_h") or t.get("last_size") or 0
                    ts_raw = t.get("time") or msg.get("timestamp")
                    ts = _parse_ts(ts_raw)
                    try:
                        price = float(price_s)
                        volume = float(vol_s or 0)
                    except (TypeError, ValueError):
                        continue
                    await self._emit(
                        Tick(symbol=product, price=price, volume=volume, source=self.name, timestamp=ts)
                    )
            return

        # Legacy-style single ticker
        if msg.get("type") == "ticker":
            product = msg.get("product_id")
            price_s = msg.get("price")
            if product and price_s is not None:
                await self._emit(
                    Tick(
                        symbol=product,
                        price=float(price_s),
                        volume=float(msg.get("last_size") or 0),
                        source=self.name,
                        timestamp=_parse_ts(msg.get("time")),
                    )
                )


def _parse_ts(raw: Any) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    s = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return datetime.now(timezone.utc)
