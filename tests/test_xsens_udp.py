"""Tests for the Xsens UDP source — packet-type dispatch and parsers.

The bridge listens on a single UDP port and MVN Analyze can emit multiple
packet types on it (MXTP02 pose, MXTP20 joint angles, and others). The
dispatcher must route correctly and never let one packet type corrupt
channels populated by another.
"""

from __future__ import annotations

import struct

import pytest

from gaitlab.hub import DataHub
from gaitlab.xsens.udp_source import (
    JOINT_LABELS,
    PACKET_ID_JOINT_ANGLES,
    PACKET_ID_POSE,
    XsensUdpSource,
    _be_f32,
)


def test_be_f32() -> None:
    raw = struct.pack(">f", 1.5)
    assert abs(_be_f32(raw, 0) - 1.5) < 1e-6


# ---------------------------------------------------------------- helpers


def _build_header(packet_id: bytes, number_of_items: int, character_id: int = 0) -> bytes:
    # 24-byte MVN header: 6B id, 4B sample_counter, 1B datagram_counter,
    # 1B number_of_items, 4B time_code, 1B character_id, 7B reserved.
    return struct.pack(
        ">6sIBBIB7s",
        packet_id,
        0,                # sample_counter
        0,                # datagram_counter
        number_of_items,
        0,                # time_code
        character_id,
        b"\x00" * 7,
    )


def _build_pose_packet(segments: list[tuple[float, float, float]]) -> bytes:
    body = b""
    for i, (x, y, z) in enumerate(segments):
        body += struct.pack(">I7f", i + 1, x, y, z, 1.0, 0.0, 0.0, 0.0)
    return _build_header(PACKET_ID_POSE, len(segments)) + body


def _build_joint_packet(joints: list[tuple[float, float, float]]) -> bytes:
    body = b""
    for i, (rx, ry, rz) in enumerate(joints):
        # 2× uint32 point IDs (unused by parser) + 3× float32 Euler.
        body += struct.pack(">II3f", 0, 0, rx, ry, rz)
    return _build_header(PACKET_ID_JOINT_ANGLES, len(joints)) + body


# ---------------------------------------------------------------- tests


def test_pose_packet_still_populates_segment_channels() -> None:
    """Regression: dispatch didn't break the existing pose path."""
    hub = DataHub()
    src = XsensUdpSource(hub)
    src._dispatch(_build_pose_packet([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]))
    assert hub.try_get("xsens.seg00.x") == pytest.approx(1.0)
    assert hub.try_get("xsens.seg00.z") == pytest.approx(3.0)
    assert hub.try_get("xsens.seg01.y") == pytest.approx(5.0)


def test_joint_angle_packet_ignored_when_toggle_off() -> None:
    """Silence a joint-angle packet when the user hasn't opted in."""
    hub = DataHub()
    src = XsensUdpSource(hub)
    assert src.publish_joint_angles is False
    src._dispatch(_build_joint_packet([(10.0, 20.0, 30.0)]))
    # No joint channels should have been created.
    assert hub.try_get("xsens.joint.jL5S1.z") is None
    assert src.joint_frames_received == 0


def test_joint_angle_packet_publishes_when_toggle_on() -> None:
    hub = DataHub()
    src = XsensUdpSource(hub)
    src.publish_joint_angles = True
    joints = [(float(i), float(i + 100), float(i + 200)) for i in range(len(JOINT_LABELS))]
    src._dispatch(_build_joint_packet(joints))

    assert src.joint_frames_received == 1
    # jRightKnee is index 15 in the MVN full-body model; verify by label.
    knee_idx = JOINT_LABELS.index("jRightKnee")
    assert hub.try_get("xsens.joint.jRightKnee.z") == pytest.approx(float(knee_idx + 200))
    assert hub.try_get("xsens.joint.jL5S1.x") == pytest.approx(0.0)
    assert hub.try_get(f"xsens.joint.{JOINT_LABELS[-1]}.y") == pytest.approx(float(21 + 100))


def test_joint_angle_extra_joints_fall_back_to_index_label() -> None:
    """More joints than the known label table (e.g. finger tracking) still
    get channels — just with a numeric fallback label."""
    hub = DataHub()
    src = XsensUdpSource(hub)
    src.publish_joint_angles = True
    over = len(JOINT_LABELS) + 3
    joints = [(1.0, 2.0, 3.0)] * over
    src._dispatch(_build_joint_packet(joints))
    # Overflow index goes to j<NN> fallback.
    fallback_idx = len(JOINT_LABELS)
    assert hub.try_get(f"xsens.joint.j{fallback_idx:02d}.x") == pytest.approx(1.0)


def test_unknown_packet_id_is_dropped_silently() -> None:
    """A packet with an unrecognised MXTP id must not touch any channel."""
    hub = DataHub()
    src = XsensUdpSource(hub)
    src.publish_joint_angles = True
    # Seed a pose channel so we can detect stray writes.
    src._dispatch(_build_pose_packet([(9.0, 9.0, 9.0)]))
    frames_before = src.frames_received
    joint_frames_before = src.joint_frames_received

    bogus = _build_header(b"MXTP99", 1) + b"\x00" * 32
    src._dispatch(bogus)

    assert src.frames_received == frames_before
    assert src.joint_frames_received == joint_frames_before
    # Existing values untouched.
    assert hub.try_get("xsens.seg00.x") == pytest.approx(9.0)


def test_configure_persists_joint_angle_toggle() -> None:
    hub = DataHub()
    src = XsensUdpSource(hub)
    src.configure(port=9763, publish_joint_angles=True)
    assert src.publish_joint_angles is True
    src.configure(port=9763, publish_joint_angles=False)
    assert src.publish_joint_angles is False
    # Omitting the arg preserves the current value.
    src.publish_joint_angles = True
    src.configure(port=9763)
    assert src.publish_joint_angles is True


def test_wrong_character_id_is_dropped() -> None:
    hub = DataHub()
    src = XsensUdpSource(hub)
    src.publish_joint_angles = True
    src.character_id = 3
    header = _build_header(PACKET_ID_JOINT_ANGLES, 1, character_id=0)
    body = struct.pack(">II3f", 0, 0, 1.0, 2.0, 3.0)
    src._dispatch(header + body)
    assert src.joint_frames_received == 0
    assert hub.try_get("xsens.joint.jL5S1.x") is None


def test_truncated_joint_packet_is_dropped() -> None:
    hub = DataHub()
    src = XsensUdpSource(hub)
    src.publish_joint_angles = True
    header = _build_header(PACKET_ID_JOINT_ANGLES, 5)  # claims 5 joints
    body = struct.pack(">II3f", 0, 0, 1.0, 2.0, 3.0)   # only 1 joint present
    src._dispatch(header + body)
    assert src.joint_frames_received == 0
