"""In-memory channel hub (Python counterpart to Unity LabDataHub)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ChannelSample:
    channel_id: str
    value: float
    unit: str = ""
    timestamp: float = 0.0


Listener = Callable[[str, float], None]


class DataHub:
    """Thread-safe float channel store with ordered ids and change listeners."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, float] = {}
        self._units: dict[str, str] = {}
        self._order: list[str] = []
        self._key_map: dict[str, str] = {}  # lower -> canonical id
        self._listeners: list[Listener] = []
        self._updated_at: dict[str, float] = {}

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def publish(self, channel_id: str, value: float, unit: str = "") -> None:
        if not channel_id:
            return
        listeners: list[Listener]
        with self._lock:
            key = channel_id.lower()
            if key in self._key_map:
                canon = self._key_map[key]
            else:
                canon = channel_id
                self._key_map[key] = canon
                self._order.append(canon)
            self._values[canon] = float(value)
            if unit:
                self._units[canon] = unit
            elif canon not in self._units:
                self._units[canon] = ""
            self._updated_at[canon] = time.time()
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(canon, float(value))
            except Exception:
                pass

    def try_get(self, channel_id: str) -> float | None:
        with self._lock:
            canon = self._key_map.get((channel_id or "").lower())
            if canon is None:
                return None
            return self._values.get(canon)

    def get_or_default(self, channel_id: str, default: float = 0.0) -> float:
        val = self.try_get(channel_id)
        return default if val is None else val

    def get_unit(self, channel_id: str) -> str:
        with self._lock:
            canon = self._key_map.get((channel_id or "").lower())
            if canon is None:
                return ""
            return self._units.get(canon, "")

    def channel_ids(self) -> list[str]:
        with self._lock:
            return list(self._order)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return {cid: self._values[cid] for cid in self._order if cid in self._values}

    def snapshot_with_units(self) -> dict[str, tuple[float, str]]:
        with self._lock:
            return {
                cid: (self._values[cid], self._units.get(cid, ""))
                for cid in self._order
                if cid in self._values
            }

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._units.clear()
            self._order.clear()
            self._key_map.clear()
            self._updated_at.clear()
