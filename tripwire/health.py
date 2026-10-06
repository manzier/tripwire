"""Heartbeat: write status file / log line every minute."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from tripwire.feeds.base import FeedStatus
from tripwire.models import iso_utc, utc_now

log = logging.getLogger("tripwire.health")


class Heartbeat:
    def __init__(
        self,
        get_statuses,
        path: str | Path = "logs/heartbeat.json",
        interval: float = 60.0,
    ) -> None:
        self.get_statuses = get_statuses
        self.path = Path(path)
        self.interval = interval
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="heartbeat")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def snapshot(self) -> dict:
        statuses: list[FeedStatus] = self.get_statuses()
        feeds = []
        for s in statuses:
            feeds.append(
                {
                    "name": s.name,
                    "connected": s.connected,
                    "symbols": s.symbols,
                    "last_tick_at": iso_utc(s.last_tick_at) if s.last_tick_at else None,
                    "last_error": s.last_error,
                }
            )
        return {
            "timestamp": iso_utc(utc_now()),
            "feeds": feeds,
        }

    async def _loop(self) -> None:
        while not self._stop.is_set():
            data = self.snapshot()
            self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            connected = [f["name"] for f in data["feeds"] if f["connected"]]
            ticks = {
                f["name"]: f["last_tick_at"] for f in data["feeds"] if f["last_tick_at"]
            }
            log.info(
                "heartbeat connected=%s last_ticks=%s",
                connected or "none",
                ticks or "{}",
            )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass
