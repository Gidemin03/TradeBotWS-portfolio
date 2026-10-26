from __future__ import annotations

import asyncio
import json
import logging
from typing import Iterable

from fastapi.encoders import jsonable_encoder

from app.domain.models import Event, LiveSearchPreset

logger = logging.getLogger(__name__)


def _format_sse(event: str, data: str) -> str:
    """
    Приводит payload к SSE-формату: event + data + пустая строка.
    """
    return f"event: {event}\ndata: {data}\n\n"


class SSEManager:
    def __init__(self, heartbeat_interval: float = 15.0, queue_size: int = 128) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self.heartbeat_interval = heartbeat_interval
        self._queue_size = queue_size

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    def publish_raw(self, payload: str) -> None:
        """
        Публикует уже отформатированную SSE-строку всем подписчикам.
        Не блокирует writer: при переполнении дропаem старые элементы.
        """
        drop_count = 0
        for q in tuple(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                    drop_count += 1
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("SSE queue full, drop message")
        if drop_count:
            logger.debug("SSE dropped %s stale messages", drop_count)

    def publish(self, event: str, data: dict) -> None:
        payload_obj = jsonable_encoder(data)
        payload = json.dumps(payload_obj, ensure_ascii=False)
        formatted = _format_sse(event, payload)
        logger.debug("SSE publish event=%s size=%s", event, len(payload))
        self.publish_raw(formatted)

    def publish_status(self, bot_status: str, presets_count: int, events_count: int) -> None:
        self.publish(
            "status",
            {
                "bot_status": bot_status,
                "presets_count": presets_count,
                "events_count": events_count,
            },
        )

    def publish_event(self, event: Event) -> None:
        self.publish("event", event.dict())

    def publish_presets(self, presets: Iterable[LiveSearchPreset]) -> None:
        self.publish("presets", {"presets": [p.dict() for p in presets]})

    @staticmethod
    def heartbeat() -> str:
        # SSE комментарий: поддерживает соединение активным.
        return ": ping\n\n"
