from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


EventHandler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(
        self,
        event_name: str,
        handler: EventHandler,
    ) -> None:
        with self._lock:
            if handler not in self._handlers[event_name]:
                self._handlers[event_name].append(handler)

    def unsubscribe(
        self,
        event_name: str,
        handler: EventHandler,
    ) -> None:
        with self._lock:
            handlers = self._handlers.get(event_name, [])

            if handler in handlers:
                handlers.remove(handler)

    def emit(
        self,
        event_name: str,
        **data: Any,
    ) -> None:
        event = Event(
            name=event_name,
            data=data,
        )

        with self._lock:
            handlers = list(
                self._handlers.get(event_name, [])
            )

        for handler in handlers:
            try:
                handler(event)
            except Exception as error:
                print(
                    f"[Event Bus Error] "
                    f"{event_name}: {error}"
                )