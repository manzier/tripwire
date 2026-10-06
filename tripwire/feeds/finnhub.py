"""Finnhub stock trades WebSocket — enabled only when FINNHUB_API_KEY is set."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from websockets.asyncio.client import connect

from tripwire.feeds.base import BaseFeed, TickHandler
from tripwire.models import Tick

log = logging.getLogger("tripwire.feeds.finnhub")

FINNHUB_WS = "wss://ws.finnhub.io"


class FinnhubFeed(BaseFeed):
    name = "finnhub"

    def __init__(
        self,
        on_tick: TickHandler,
        api_key: Optional[str] = None,
        url: str = FINNHUB_WS,
        **kwargs: Any,
    ) -> None:
        super().__init__(on_tick=on_tick, **kwargs)
        self.api_key = api_key if api_key is not None else os.environ.get("FINNHUB_API_KEY")
        self.url = url

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def start(self) -> None:
        if not self.enabled:
            log.info(
                "[%s] FINNHUB_API_KEY not set — skipping Finnhub stock feed",
                self.name,
            )
            self.status.connected = False
            self.status.last_error = "disabled: FINNHUB_API_KEY not set"
            return
        await super().start()

    async def _connect_and_listen(self) -> None:
        assert self.api_key
        url = f"{self.url}?token={self.api_key}"
        async with connect(url, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
            for sym in self._symbols:
                await ws.send(json.dumps({"type": "subscribe", "symbol": sym}))
            self.status.connected = True
            log.info("[%s] subscribed to trades: %s", self.name, self._symbols)

            async for raw in ws:
                if self._stop.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle(msg)

    async def _handle(self, msg: dict[str, Any]) -> None:
        if msg.get("type") == "ping":
            return
        if msg.get("type") != "trade":
            return
        for t in msg.get("data") or []:
            sym = t.get("s")
            price = t.get("p")
            if sym is None or price is None:
                continue
            vol = float(t.get("v") or 0)
            ms = t.get("t")
            if ms:
                ts = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
            else:
                ts = datetime.now(timezone.utc)
            await self._emit(
                Tick(symbol=str(sym), price=float(price), volume=vol, source=self.name, timestamp=ts)
            )
