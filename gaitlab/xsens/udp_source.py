"""Xsens / Movella MVN UDP (MXTP) raw pose listener → DataHub.

Mirrors Unity XsensGaitSource port binding (primary + fallbacks) but publishes
raw segment floats only — no avatar / gait derivation.
"""

from __future__ import annotations

import socket
import struct
import threading
from typing import Callable

from gaitlab import channels
from gaitlab.hub import DataHub

StatusCallback = Callable[[str], None]

DEFAULT_PORT = 9763
DEFAULT_FALLBACKS = (9764, 9765, 9766)


class XsensUdpSource:
    def __init__(self, hub: DataHub) -> None:
        self.hub = hub
        self.port = DEFAULT_PORT
        self.fallback_ports: list[int] = list(DEFAULT_FALLBACKS)
        self.character_id = 0
        self.publish_quaternions = False
        self._sock: socket.socket | None = None
        self._bound_port = -1
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._status_cb: StatusCallback | None = None
        self._last_error = ""
        self.connected = False
        self.frames_received = 0
        self._labels_registered = False

    def set_status_callback(self, cb: StatusCallback | None) -> None:
        self._status_cb = cb

    def _status(self, msg: str) -> None:
        self._last_error = msg
        if self._status_cb:
            try:
                self._status_cb(msg)
            except Exception:
                pass

    @property
    def bound_port(self) -> int:
        return self._bound_port

    def configure(self, port: int, fallbacks: list[int] | None = None, character_id: int = 0) -> None:
        was = self.connected
        if was:
            self.stop()
        self.port = int(port)
        if fallbacks is not None:
            self.fallback_ports = [int(p) for p in fallbacks if int(p) > 0]
        self.character_id = int(character_id)
        if was:
            self.start()

    def start(self) -> bool:
        self.stop()
        sock, bound, note = self._bind_any()
        if sock is None:
            self._status(note)
            self.connected = False
            return False
        self._sock = sock
        self._bound_port = bound
        self.connected = True
        self._labels_registered = False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="XsensUdp", daemon=True)
        self._thread.start()
        self._status(note)
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        self._bound_port = -1
        self.connected = False

    def summary(self) -> dict:
        return {
            "connected": self.connected,
            "bound_port": self._bound_port,
            "configured_port": self.port,
            "fallbacks": list(self.fallback_ports),
            "frames": self.frames_received,
            "error": self._last_error if not self.connected else "",
        }

    def _bind_any(self) -> tuple[socket.socket | None, int, str]:
        candidates = [self.port] + [p for p in self.fallback_ports if p != self.port]
        for port in candidates:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("0.0.0.0", port))
                sock.settimeout(0.5)
                if port == self.port:
                    note = f"Xsens UDP listening on primary port {port}."
                else:
                    note = (
                        f"Xsens UDP: primary {self.port} busy — using fallback {port}. "
                        f"Match this port in MVN Network Streamer."
                    )
                return sock, port, note
            except OSError:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
                continue
        return (
            None,
            -1,
            f"Could not bind UDP ports {candidates}. Close other listeners or change the port.",
        )

    def _loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    self.connected = False
                    self._status("Xsens UDP socket closed")
                break
            try:
                self._parse_pose(data)
            except Exception as exc:
                self._status(f"Xsens parse error: {exc}")

    def _parse_pose(self, data: bytes) -> None:
        if len(data) < 24:
            return
        segment_count = data[11]
        character_id = data[16]
        body = data[17]
        prop = data[18]
        finger = data[19]
        if segment_count == 0:
            segment_count = body + prop + finger
        if character_id != self.character_id or segment_count <= 0:
            return
        offset = 24
        bytes_per = 32
        if len(data) < offset + segment_count * bytes_per:
            return

        self.frames_received += 1
        register = not self._labels_registered
        for s in range(segment_count):
            base = offset + s * bytes_per
            raw_x = _be_f32(data, base + 4)
            raw_y = _be_f32(data, base + 8)
            raw_z = _be_f32(data, base + 12)
            cx = f"xsens.seg{s:02d}.x"
            cy = f"xsens.seg{s:02d}.y"
            cz = f"xsens.seg{s:02d}.z"
            self.hub.publish(cx, raw_x, "m")
            self.hub.publish(cy, raw_y, "m")
            self.hub.publish(cz, raw_z, "m")
            if register:
                channels.register_live_channel(cx, f"Xsens seg{s} X", "m")
                channels.register_live_channel(cy, f"Xsens seg{s} Y", "m")
                channels.register_live_channel(cz, f"Xsens seg{s} Z", "m")
            if self.publish_quaternions:
                self.hub.publish(f"xsens.seg{s:02d}.qw", _be_f32(data, base + 16))
                self.hub.publish(f"xsens.seg{s:02d}.qx", _be_f32(data, base + 20))
                self.hub.publish(f"xsens.seg{s:02d}.qy", _be_f32(data, base + 24))
                self.hub.publish(f"xsens.seg{s:02d}.qz", _be_f32(data, base + 28))
        if register:
            self._labels_registered = True


def _be_f32(data: bytes, offset: int) -> float:
    return struct.unpack(">f", data[offset : offset + 4])[0]
