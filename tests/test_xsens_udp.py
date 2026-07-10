"""Smoke test for Xsens UDP big-endian float helper."""

import struct

from gaitlab.xsens.udp_source import _be_f32


def test_be_f32():
    raw = struct.pack(">f", 1.5)
    assert abs(_be_f32(raw, 0) - 1.5) < 1e-6
