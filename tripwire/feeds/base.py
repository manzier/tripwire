"""Base WebSocket feed with auto-reconnect and exponential backoff."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Optional

from tripwire.models import Tick, utc_now

log = logging.getLogger("tripwire.feeds")

TickHandler = Callable[[Tick], Awaitable[None]]


@dataclass
class FeedStatus:
    name: str
    connected: bool = False
    last_tick_at: Optional[datetime] = None
    last_error: Optional[str] = None
    symbols: list[str] = field(default_factory=list)


class BaseFeed(ABC):
    name: str = "base"

    def __init__(
        self,
        on_tick: TickHandler,
        min_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        self.on_tick = on_tick
        self.min_backoff = min_backoff
        self.max_backoff = max_backoff
        self.status = FeedStatus(name=self.name)
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._symbols: list[str] = []

    def set_symbols(self, symbols: list[str]) -> None:
        self._symbols = list(symbols)
        self.status.symbols = list(symbols)

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name=f"feed-{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.status.connected = False

    async def _run_loop(self) -> None:
        backoff = self.min_backoff
        while not self._stop.is_set():
            if not self._symbols:
                self.status.connected = False
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                log.info("[%s] connecting for %s", self.name, self._symbols)
                await self._connect_and_listen()
                backoff = self.min_backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status.connected = False
                self.status.last_error = str(exc)
                log.warning("[%s] disconnected: %s; retry in %.1fs", self.name, exc, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, self.max_backoff)

    async def _emit(self, tick: Tick) -> None:
        self.status.last_tick_at = tick.timestamp or utc_now()
        self.status.connected = True
        self.status.last_error = None
        await self.on_tick(tick)

    @abstractmethod
    async def _connect_and_listen(self) -> None:
        ...
