"""Pushover client and broadcaster."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import Any

import httpx

from binance_monitor.db.repositories import SubscriberRepository

log = logging.getLogger(__name__)


class PushoverResult(StrEnum):
    OK = "ok"
    INVALID_USER = "invalid_user"
    ERROR = "error"


class PushoverClient:
    def __init__(self, http: httpx.AsyncClient, app_token: str, base_url: str) -> None:
        self._http = http
        self._token = app_token
        self._base = base_url.rstrip("/")

    async def _post(self, path: str, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
        response = await self._http.post(
            f"{self._base}{path}", data={"token": self._token, **data}, timeout=15.0
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    @staticmethod
    def _classify(status: int, body: dict[str, Any]) -> PushoverResult:
        if status == 200 and body.get("status") == 1:
            return PushoverResult.OK
        if body.get("user") == "invalid":
            return PushoverResult.INVALID_USER
        return PushoverResult.ERROR

    async def validate(self, user_key: str) -> PushoverResult:
        """POST /1/users/validate.json. Network errors are returned as ERROR."""
        try:
            status, body = await self._post("/1/users/validate.json", {"user": user_key})
        except httpx.HTTPError as exc:
            log.warning("Pushover validate failed: %s", type(exc).__name__)
            return PushoverResult.ERROR
        return self._classify(status, body)

    async def send(self, user_key: str, title: str, message: str) -> PushoverResult:
        try:
            status, body = await self._post(
                "/1/messages.json", {"user": user_key, "title": title, "message": message}
            )
        except httpx.HTTPError as exc:
            log.warning("Pushover send failed: %s", type(exc).__name__)
            return PushoverResult.ERROR
        result = self._classify(status, body)
        if result is PushoverResult.ERROR:
            log.warning("Pushover send error: HTTP %s %s", status, body.get("errors"))
        return result


class PushoverBroadcaster:
    """Send a notification to every active subscriber, concurrently, each isolated."""

    def __init__(self, client: PushoverClient, subscribers: SubscriberRepository) -> None:
        self._client = client
        self._subscribers = subscribers

    async def broadcast(self, title: str, message: str) -> None:
        subscribers = await self._subscribers.list_active()
        if not subscribers:
            return

        async def one(user_id: int, key: str) -> None:
            try:
                result = await self._client.send(key, title, message)
            except Exception:
                log.exception("Pushover delivery to subscriber %s crashed", user_id)
                return
            if result is PushoverResult.INVALID_USER:
                log.warning(
                    "Pushover reports invalid key for subscriber %s: marking inactive", user_id
                )
                await self._subscribers.mark_inactive(user_id)

        await asyncio.gather(*(one(s.telegram_user_id, s.pushover_key) for s in subscribers))
