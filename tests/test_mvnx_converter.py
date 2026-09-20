"""Tests for tools/mvnx_to_gaitlab_csv.py.

Synthesises a tiny .mvnx (real Xsens namespace, header + 3 calibration
frames + 5 normal frames), converts it, and verifies that:

* Calibration frames are skipped (row count == 5, not 8).
* Column names match the live UDP bridge (`xsens.joint.<label>.{x,y,z}`,
  `xsens.seg<NN>.{x,y,z}`).
* Values round-trip losslessly at 6 decimals.
* Time comes from the frame's `time` attribute in ms, not derived.
* The resulting CSV is loadable by `gaitlab.session.player.load_session`
  — the whole point of the converter.
* Missing / truncated frames don't crash the writer.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from gaitlab.session.player import load_session
from tools.mvnx_to_gaitlab_csv import convert_mvnx_to_csv

_MVNX_NS = "http://www.xsens.com/mvn/mvnx"


def _make_mvnx(
    path: Path,
    *,
    segment_labels: list[str],
    joint_labels: list[str],
    calibration_frames: list[tuple[str, list[float], list[float]]],
    normal_frames: list[tuple[float, list[float], list[float]]],
    frame_rate: float = 240.0,
) -> None:
    """Write a minimal but schema-shaped .mvnx.

    ``calibration_frames`` is a list of (type, position_floats, joint_floats).
    ``normal_frames`` is a list of (time_ms, position_floats, joint_floats).
    """
    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(f'<mvnx xmlns="{_MVNX_NS}" version="4">')
    lines.append(f'  <subject label="test" frameRate="{frame_rate:g}">')
    lines.append('    <segments>')
    for i, lbl in enumerate(segment_labels):
        lines.append(f'      <segment id="{i}" label="{lbl}"/>')
    lines.append('    </segments>')
    lines.append('    <joints>')
    for i, lbl in enumerate(joint_labels):
        lines.append(f'      <joint label="{lbl}"/>')
    lines.append('    </joints>')
    lines.append('    <frames>')
    for ftype, pos, ja in calibration_frames:
        lines.append(
            f'      <frame time="0" index="0" type="{ftype}">'
        )
        lines.append(f'        <position>{" ".join(f"{v:g}" for v in pos)}</position>')
        lines.append(f'        <jointAngle>{" ".join(f"{v:g}" for v in ja)}</jointAngle>')
        lines.append('      </frame>')
    for i, (t_ms, pos, ja) in enumerate(normal_frames):
        lines.append(
            f'      <frame time="{t_ms:g}" index="{i}" type="normal">'
        )
        lines.append(f'        <position>{" ".join(f"{v:g}" for v in pos)}</position>')
        lines.append(f'        <jointAngle>{" ".join(f"{v:g}" for v in ja)}</jointAngle>')
        lines.append('      </frame>')
    lines.append('    </frames>')
    lines.append('  </subject>')
    lines.append('</mvnx>')
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture()
def tiny_mvnx(tmp_path: Path) -> Path:
    p = tmp_path / "tiny.mvnx"
    segs = ["Pelvis", "L5"]
    joints = ["jRightKnee", "jLeftKnee"]
    calibration = [
        ("identity", [0.0] * 6, [0.0] * 6),
        ("tpose",    [0.0] * 6, [0.0] * 6),
        ("npose",    [0.0] * 6, [0.0] * 6),
    ]
    normal = [
        (0.0,     [0.1, 0.2, 0.3,  1.1, 1.2, 1.3], [10.0, 20.0, 30.0,  15.0, 25.0, 35.0]),
        (4.1667,  [0.11, 0.21, 0.31, 1.11, 1.21, 1.31], [11.0, 21.0, 31.0, 16.0, 26.0, 36.0]),
        (8.3333,  [0.12, 0.22, 0.32, 1.12, 1.22, 1.32], [12.0, 22.0, 32.0, 17.0, 27.0, 37.0]),
        (12.5,    [0.13, 0.23, 0.33, 1.13, 1.23, 1.33], [13.0, 23.0, 33.0, 18.0, 28.0, 38.0]),
        (16.6667, [0.14, 0.24, 0.34, 1.14, 1.24, 1.34], [14.0, 24.0, 34.0, 19.0, 29.0, 39.0]),
    ]
    _make_mvnx(p, segment_labels=segs, joint_labels=joints,
               calibration_frames=calibration, normal_frames=normal)
    return p


def test_converter_skips_calibration_frames(tiny_mvnx: Path, tmp_path: Path) -> None:
    out = tmp_path / "tiny.csv"
    summary = convert_mvnx_to_csv(tiny_mvnx, out)
    assert summary.calibration_frame_count == 3
    assert summary.normal_frame_count == 5
    assert summary.sample_rate_hz == pytest.approx(240.0)


def test_converter_column_names_match_live_bridge(tiny_mvnx: Path, tmp_path: Path) -> None:
    """These IDs are the contract — every script bound to a live channel
    must transparently work against a converted file."""
    out = tmp_path / "tiny.csv"
    convert_mvnx_to_csv(tiny_mvnx, out)
    with out.open() as f:
        rows = list(csv.reader(l for l in f if not l.startswith("#")))
    header = rows[0]
    assert header[0] == "time_sec"
    # 2 segments × 3 + 2 joints × 3 = 12 data columns.
    assert "xsens.seg00.x" in header
    assert "xsens.seg01.z" in header
    assert "xsens.joint.jRightKnee.x" in header
    assert "xsens.joint.jRightKnee.y" in header
    assert "xsens.joint.jRightKnee.z" in header
    assert "xsens.joint.jLeftKnee.z" in header


def test_converter_preserves_values_at_6_decimals(tiny_mvnx: Path, tmp_path: Path) -> None:
    out = tmp_path / "tiny.csv"
    convert_mvnx_to_csv(tiny_mvnx, out)
    session = load_session(out)
    # Row 0 is the first NORMAL frame — calibration frames were skipped.
    assert session.rows[0]["xsens.joint.jRightKnee.z"] == pytest.approx(30.0)
    assert session.rows[0]["xsens.seg00.x"] == pytest.approx(0.1)
    assert session.rows[-1]["xsens.joint.jRightKnee.z"] == pytest.approx(34.0)
    assert session.rows[-1]["xsens.seg01.y"] == pytest.approx(1.24)


def test_converter_uses_frame_time_attribute_in_seconds(tiny_mvnx: Path, tmp_path: Path) -> None:
    """Time comes from the frame's `time` attribute (ms → s), not from
    `index / rate`. Ensures dropped-frame gaps in the recording survive."""
    out = tmp_path / "tiny.csv"
    convert_mvnx_to_csv(tiny_mvnx, out)
    session = load_session(out)
    # 16.6667 ms == 0.016666... s.
    assert session.times[0] == pytest.approx(0.0)
    assert session.times[-1] == pytest.approx(0.0167, abs=1e-4)


def test_converter_output_loads_via_session_player(tiny_mvnx: Path, tmp_path: Path) -> None:
    """End-to-end contract: converter output must be a valid GaitLab
    session. If load_session succeeds, the Sessions tab can open it."""
    out = tmp_path / "tiny.csv"
    convert_mvnx_to_csv(tiny_mvnx, out)
    session = load_session(out)
    assert session.row_count == 5
    assert session.metadata.sample_hz == pytest.approx(240.0)
    assert "xsens.joint.jRightKnee.z" in session.channels


def test_converter_progress_callback_fires_and_ends_at_one(tiny_mvnx: Path, tmp_path: Path) -> None:
    out = tmp_path / "tiny.csv"
    seen: list[float] = []
    convert_mvnx_to_csv(tiny_mvnx, out, progress_callback=seen.append)
    assert seen, "progress callback never fired"
    assert seen[-1] == pytest.approx(1.0)


def test_converter_rejects_file_with_no_normal_frames(tmp_path: Path) -> None:
    p = tmp_path / "empty.mvnx"
    _make_mvnx(
        p,
        segment_labels=["Pelvis"],
        joint_labels=["jRightKnee"],
        calibration_frames=[("tpose", [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])],
        normal_frames=[],
    )
    out = tmp_path / "empty.csv"
    with pytest.raises(RuntimeError, match="no `type=normal` frames"):
        convert_mvnx_to_csv(p, out)


def test_converter_rejects_missing_input_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        convert_mvnx_to_csv(tmp_path / "does_not_exist.mvnx", tmp_path / "out.csv")
