"""pylsl inlet manager: discover, connect, pull, reconnect."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from gaitlab import channels
from gaitlab.hub import DataHub
from gaitlab.lsl.mappings import (
    ChannelMapEntry,
    StreamMapping,
    auto_map_channels,
    default_channel_id,
)

try:
    import pylsl  # type: ignore
except ImportError:  # pragma: no cover
    pylsl = None  # type: ignore


@dataclass
class DiscoveredStream:
    name: str
    type: str
    channel_count: int
    source_id: str = ""
    nominal_srate: float = 0.0


@dataclass
class ConnectedStream:
    mapping: StreamMapping
    channel_count: int
    inlet: object | None = None
    last_sample_time: float = 0.0
    connected: bool = False
    error: str = ""
    buffer: list[float] = field(default_factory=list)


StatusCallback = Callable[[str], None]


class LslManager:
    """Background pull loop publishing mapped channels into a DataHub."""

    def __init__(self, hub: DataHub) -> None:
        self.hub = hub
        self._lock = threading.RLock()
        self._connected: dict[str, ConnectedStream] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._status_cb: StatusCallback | None = None
        self.reconnect_interval_s = 3.0
        self.resolve_timeout_s = 2.0
        self.quick_resolve_s = 0.05

    def set_status_callback(self, cb: StatusCallback | None) -> None:
        self._status_cb = cb

    def _status(self, msg: str) -> None:
        if self._status_cb:
            try:
                self._status_cb(msg)
            except Exception:
                pass

    @staticmethod
    def available() -> bool:
        return pylsl is not None

    def list_streams(self, timeout: float = 1.0) -> list[DiscoveredStream]:
        if pylsl is None:
            return []
        results: list[DiscoveredStream] = []
        try:
            infos = pylsl.resolve_streams(wait_time=timeout)
        except Exception as exc:  # pragma: no cover
            self._status(f"LSL resolve failed: {exc}")
            return []
        for info in infos:
            try:
                results.append(
                    DiscoveredStream(
                        name=info.name(),
                        type=info.type(),
                        channel_count=int(info.channel_count()),
                        source_id=info.source_id(),
                        nominal_srate=float(info.nominal_srate()),
                    )
                )
            except Exception:
                continue
        return results

    @staticmethod
    def _key(name: str, typ: str) -> str:
        return f"{(name or '').lower()}|{(typ or '').lower()}"

    def connect(self, mapping: StreamMapping, channel_count_hint: int = 0) -> bool:
        if pylsl is None:
            self._status("pylsl not installed")
            return False
        if not mapping.stream_name and not mapping.stream_type:
            self._status("Need stream name and/or type")
            return False

        inlet, ch_count, err = self._open_inlet(mapping, self.resolve_timeout_s)
        if inlet is None:
            self._status(err or "connect failed")
            with self._lock:
                key = self._key(mapping.stream_name, mapping.stream_type)
                self._connected[key] = ConnectedStream(
                    mapping=mapping, channel_count=0, connected=False, error=err
                )
            return False

        maps = list(mapping.channels)
        if not maps:
            maps = auto_map_channels(mapping.stream_name or "stream", ch_count)
            mapping = StreamMapping(
                stream_name=mapping.stream_name,
                stream_type=mapping.stream_type,
                channels=maps,
                enabled=mapping.enabled,
            )

        for entry in maps:
            channels.register_live_channel(
                entry.hub_channel_id,
                entry.label or entry.hub_channel_id,
                entry.unit,
            )

        key = self._key(mapping.stream_name, mapping.stream_type)
        with self._lock:
            self._connected[key] = ConnectedStream(
                mapping=mapping,
                channel_count=ch_count,
                inlet=inlet,
                connected=True,
                buffer=[0.0] * ch_count,
            )
        self._status(f"Connected {mapping.stream_name or '*'} ({mapping.stream_type or '*'}) · {ch_count} ch")
        self.start()
        return True

    def disconnect(self, stream_name: str = "", stream_type: str = "") -> None:
        key = self._key(stream_name, stream_type)
        with self._lock:
            self._connected.pop(key, None)
            if not self._connected:
                self._stop.set()

    def disconnect_all(self) -> None:
        with self._lock:
            self._connected.clear()
        self.stop()

    def connected_summaries(self) -> list[dict]:
        with self._lock:
            out = []
            for cs in self._connected.values():
                out.append(
                    {
                        "name": cs.mapping.stream_name,
                        "type": cs.mapping.stream_type,
                        "channels": cs.channel_count,
                        "connected": cs.connected,
                        "error": cs.error,
                        "last_sample_age": (time.time() - cs.last_sample_time)
                        if cs.last_sample_time
                        else None,
                        "mapping": cs.mapping,
                    }
                )
            return out

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="LslPuller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.5)
        self._thread = None

    def _open_inlet(
        self, mapping: StreamMapping, timeout: float
    ) -> tuple[object | None, int, str]:
        assert pylsl is not None
        try:
            infos = pylsl.resolve_streams(wait_time=timeout)
        except Exception as exc:
            return None, 0, str(exc)

        want_name = (mapping.stream_name or "").lower()
        want_type = (mapping.stream_type or "").lower()
        match = None
        for info in infos:
            n = (info.name() or "").lower()
            t = (info.type() or "").lower()
            if want_name and n != want_name:
                continue
            if want_type and t != want_type:
                continue
            match = info
            break
        if match is None:
            return None, 0, f"No stream matching name={mapping.stream_name!r} type={mapping.stream_type!r}"

        try:
            inlet = pylsl.StreamInlet(match, max_buflen=360, processing_flags=0)
            ch = int(match.channel_count())
            return inlet, ch, ""
        except Exception as exc:
            return None, 0, str(exc)

    def _loop(self) -> None:
        last_reconnect = 0.0
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                items = list(self._connected.items())

            for key, cs in items:
                if not cs.mapping.enabled:
                    continue
                if not cs.connected or cs.inlet is None:
                    if now - last_reconnect >= self.reconnect_interval_s:
                        last_reconnect = now
                        inlet, ch, err = self._open_inlet(cs.mapping, self.quick_resolve_s)
                        if inlet is not None:
                            with self._lock:
                                if key in self._connected:
                                    self._connected[key].inlet = inlet
                                    self._connected[key].channel_count = ch
                                    self._connected[key].connected = True
                                    self._connected[key].error = ""
                                    self._connected[key].buffer = [0.0] * ch
                            self._status(f"Reconnected {cs.mapping.stream_name}")
                        else:
                            with self._lock:
                                if key in self._connected:
                                    self._connected[key].error = err
                    continue

                self._drain(cs)

            time.sleep(0.002)

    def _drain(self, cs: ConnectedStream) -> None:
        inlet = cs.inlet
        if inlet is None:
            return
        try:
            newest = None
            while True:
                sample, ts = inlet.pull_sample(timeout=0.0)
                if sample is None:
                    break
                newest = (sample, ts)
            if newest is None:
                return
            sample, ts = newest
            cs.last_sample_time = time.time()
            maps = cs.mapping.channels
            if not maps:
                maps = [
                    ChannelMapEntry(i, default_channel_id(cs.mapping.stream_name, i))
                    for i in range(len(sample))
                ]
            for entry in maps:
                idx = entry.sample_index
                if idx < 0 or idx >= len(sample):
                    continue
                try:
                    raw = float(sample[idx])
                except (TypeError, ValueError):
                    continue
                value = raw * (entry.scale if entry.scale else 1.0)
                self.hub.publish(entry.hub_channel_id, value, entry.unit)
        except Exception as exc:
            cs.connected = False
            cs.inlet = None
            cs.error = str(exc)
            self._status(f"Stream error {cs.mapping.stream_name}: {exc}")
