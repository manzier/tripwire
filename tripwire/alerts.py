"""Alert delivery: local JSONL log + optional webhook POST with retries."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import aiohttp

from tripwire.models import Alert, iso_utc, utc_now

log = logging.getLogger("tripwire.alerts")


class AlertSink:
    def __init__(
        self,
        log_path: str | Path = "logs/alerts.jsonl",
        webhook_url: Optional[str] = None,
        webhook_key: Optional[str] = None,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
    ) -> None:
        self.log_path = Path(log_path)
        self.webhook_url = webhook_url or os.environ.get("TRIPWIRE_WEBHOOK_URL") or None
        self.webhook_key = webhook_key or os.environ.get("TRIPWIRE_WEBHOOK_KEY") or None
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        if self.webhook_url and self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def append_local(self, alert: Alert) -> None:
        line = json.dumps(alert.to_dict(), separators=(",", ":"))
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def emit(self, alert: Alert) -> None:
        self.append_local(alert)
        log.info(
            "ALERT %s %s trigger=%s price=%s source=%s",
            alert.symbol,
            alert.rule,
            alert.trigger_value,
            alert.last_price,
            alert.source,
        )
        if not self.webhook_url:
            return
        await self._post_webhook(alert.to_dict())

    async def emit_raw(self, payload: dict[str, Any]) -> None:
        """Emit a pre-built payload (used by test-alert)."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
        log.info("ALERT (raw) %s", payload.get("symbol", "?"))
        if self.webhook_url:
            await self._post_webhook(payload)

    async def _post_webhook(self, payload: dict[str, Any]) -> None:
        assert self.webhook_url
        headers = {"Content-Type": "application/json"}
        if self.webhook_key:
            headers["Authorization"] = f"Bearer {self.webhook_key}"
            headers["X-Webhook-Key"] = self.webhook_key

        if self._session is None:
            await self.start()
        session = self._session
        assert session is not None

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.post(self.webhook_url, json=payload, headers=headers) as resp:
                    if 200 <= resp.status < 300:
                        log.debug("Webhook OK status=%s", resp.status)
                        return
                    body = await resp.text()
                    last_err = RuntimeError(f"HTTP {resp.status}: {body[:200]}")
                    log.warning(
                        "Webhook attempt %d/%d failed: %s",
                        attempt,
                        self.max_retries,
                        last_err,
                    )
            except Exception as exc:
                last_err = exc
                log.warning(
                    "Webhook attempt %d/%d error: %s",
                    attempt,
                    self.max_retries,
                    exc,
                )
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_backoff * attempt)
        log.error("Webhook failed after %d attempts: %s", self.max_retries, last_err)


def make_test_alert() -> Alert:
    return Alert(
        symbol="TEST-USD",
        rule="target_above",
        trigger_value=1.0,
        last_price=1.01,
        timestamp=iso_utc(utc_now()),
        source="test",
        detail={"note": "synthetic test alert"},
    )
