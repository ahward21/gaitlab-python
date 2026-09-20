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

# MVN Network Streamer packet identifiers (first 6 bytes of every datagram).
PACKET_ID_POSE = b"MXTP02"          # Pose data — segment position + quaternion.
PACKET_ID_JOINT_ANGLES = b"MXTP20"  # Joint angles — Euler XYZ per joint.

# Standard MVN full-body joint order (index → label). Used for channel names
# when MXTP20 packets are decoded. If a packet reports more joints than this
# table covers (e.g. finger tracking), extra joints fall back to `j<index>`.
JOINT_LABELS: tuple[str, ...] = (
    "jL5S1",
    "jL4L3",
    "jL1T12",
    "jT9T8",
    "jT1C7",
    "jC1Head",
    "jRightT4Shoulder",
    "jRightShoulder",
    "jRightElbow",
    "jRightWrist",
    "jLeftT4Shoulder",
    "jLeftShoulder",
    "jLeftElbow",
    "jLeftWrist",
    "jRightHip",
    "jRightKnee",
    "jRightAnkle",
    "jRightBallFoot",
    "jLeftHip",
    "jLeftKnee",
    "jLeftAnkle",
    "jLeftBallFoot",
)

_JOINT_RECORD_SIZE = 20  # 2× uint32 point IDs + 3× float32 Euler.


class XsensUdpSource:
    def __init__(self, hub: DataHub) -> None:
        self.hub = hub
        self.port = DEFAULT_PORT
        self.fallback_ports: list[int] = list(DEFAULT_FALLBACKS)
        self.character_id = 0
        self.publish_quaternions = False
        self.publish_joint_angles = False
        self._sock: socket.socket | None = None
        self._bound_port = -1
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._status_cb: StatusCallback | None = None
        self._last_error = ""
        self.connected = False
        self.frames_received = 0
        self.joint_frames_received = 0
        self._labels_registered = False
        self._joint_labels_registered = False

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

    def configure(
        self,
        port: int,
        fallbacks: list[int] | None = None,
        character_id: int = 0,
        publish_quaternions: bool | None = None,
        publish_joint_angles: bool | None = None,
    ) -> None:
        was = self.connected
        if was:
            self.stop()
        self.port = int(port)
        if fallbacks is not None:
            self.fallback_ports = [int(p) for p in fallbacks if int(p) > 0]
        self.character_id = int(character_id)
        if publish_quaternions is not None:
            self.publish_quaternions = bool(publish_quaternions)
        if publish_joint_angles is not None:
            self.publish_joint_angles = bool(publish_joint_angles)
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
        self._joint_labels_registered = False
        # Pre-register typical full-body segment channels so Channels/Metrics lists
        # show Xsens sensors immediately (updated when the first pose arrives).
        self._register_segment_channels(23)
        if self.publish_joint_angles:
            self._register_joint_channels(len(JOINT_LABELS))
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="XsensUdp", daemon=True)
        self._thread.start()
        ready_msg = " Channels: xsens.seg00…seg22 .x/.y/.z ready."
        if self.publish_joint_angles:
            ready_msg += " Joint angles: xsens.joint.<label>.x/.y/.z ready."
        self._status(note + ready_msg)
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
                self._dispatch(data)
            except Exception as exc:
                self._status(f"Xsens parse error: {exc}")

    def _dispatch(self, data: bytes) -> None:
        if len(data) < 24:
            return
        packet_id = bytes(data[0:6])
        if packet_id == PACKET_ID_POSE:
            self._parse_pose(data)
        elif packet_id == PACKET_ID_JOINT_ANGLES:
            if self.publish_joint_angles:
                self._parse_joint_angles(data)
        # Unknown MXTP* packets (position-only, CoM, tracker kin, etc.) are
        # silently ignored — a MVN Streamer misconfiguration should not
        # corrupt whatever channels are already publishing.

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
        if segment_count > 23 or not self._labels_registered:
            self._register_segment_channels(segment_count)
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
            if self.publish_quaternions:
                self.hub.publish(f"xsens.seg{s:02d}.qw", _be_f32(data, base + 16))
                self.hub.publish(f"xsens.seg{s:02d}.qx", _be_f32(data, base + 20))
                self.hub.publish(f"xsens.seg{s:02d}.qy", _be_f32(data, base + 24))
                self.hub.publish(f"xsens.seg{s:02d}.qz", _be_f32(data, base + 28))

    def _parse_joint_angles(self, data: bytes) -> None:
        joint_count = data[11]
        character_id = data[16]
        body = data[17]
        prop = data[18]
        finger = data[19]
        if joint_count == 0:
            joint_count = body + prop + finger
        if character_id != self.character_id or joint_count <= 0:
            return
        offset = 24
        if len(data) < offset + joint_count * _JOINT_RECORD_SIZE:
            return

        self.joint_frames_received += 1
        if joint_count > len(JOINT_LABELS) or not self._joint_labels_registered:
            self._register_joint_channels(joint_count)
        for j in range(joint_count):
            base = offset + j * _JOINT_RECORD_SIZE
            # Bytes 0..7 are the parent/child point IDs; skip them and read the
            # 3 float32 Euler components.
            rx = _be_f32(data, base + 8)
            ry = _be_f32(data, base + 12)
            rz = _be_f32(data, base + 16)
            label = JOINT_LABELS[j] if j < len(JOINT_LABELS) else f"j{j:02d}"
            self.hub.publish(f"xsens.joint.{label}.x", rx, "deg")
            self.hub.publish(f"xsens.joint.{label}.y", ry, "deg")
            self.hub.publish(f"xsens.joint.{label}.z", rz, "deg")

    def _register_joint_channels(self, joint_count: int) -> None:
        for j in range(max(0, joint_count)):
            label = JOINT_LABELS[j] if j < len(JOINT_LABELS) else f"j{j:02d}"
            for axis in ("x", "y", "z"):
                cid = f"xsens.joint.{label}.{axis}"
                channels.register_live_channel(cid, f"Xsens {label} {axis.upper()}", "deg")
                if self.hub.try_get(cid) is None:
                    self.hub.publish(cid, 0.0, "deg")
        self._joint_labels_registered = True

    def _register_segment_channels(self, segment_count: int) -> None:
        for s in range(max(0, segment_count)):
            channels.register_live_channel(f"xsens.seg{s:02d}.x", f"Xsens seg{s} X", "m")
            channels.register_live_channel(f"xsens.seg{s:02d}.y", f"Xsens seg{s} Y", "m")
            channels.register_live_channel(f"xsens.seg{s:02d}.z", f"Xsens seg{s} Z", "m")
            # Seed hub so Channels/Metrics lists show them before the first sample.
            if self.hub.try_get(f"xsens.seg{s:02d}.x") is None:
                self.hub.publish(f"xsens.seg{s:02d}.x", 0.0, "m")
                self.hub.publish(f"xsens.seg{s:02d}.y", 0.0, "m")
                self.hub.publish(f"xsens.seg{s:02d}.z", 0.0, "m")
            if self.publish_quaternions:
                for comp in ("qw", "qx", "qy", "qz"):
                    cid = f"xsens.seg{s:02d}.{comp}"
                    channels.register_live_channel(cid, f"Xsens seg{s} {comp}", "")
                    if self.hub.try_get(cid) is None:
                        # Seed with identity quaternion so joint-angle scripts don't
                        # see a garbage rotation on the first tick before real data.
                        self.hub.publish(cid, 1.0 if comp == "qw" else 0.0, "")
        self._labels_registered = True


def _be_f32(data: bytes, offset: int) -> float:
    return struct.unpack(">f", data[offset : offset + 4])[0]
