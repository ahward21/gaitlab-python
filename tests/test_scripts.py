"""Tests for the custom-analysis-script plugin system."""

from __future__ import annotations

import math
import time
from pathlib import Path

import pytest

from gaitlab.hub import DataHub
from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput, hub_channel_id_for
from gaitlab.scripts.discovery import discover_scripts
from gaitlab.scripts.runner import ScriptBinding, ScriptRegistry


# ---------------- API ----------------

def test_hub_channel_id_naming():
    assert hub_channel_id_for("gait.peak_detector", "stride_count") == "script.gait.peak_detector.stride_count"


# ---------------- discovery ----------------

_DOUBLER_SOURCE = '''\
from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput

class Doubler(AnalysisScript):
    id = "test.doubler"
    display_name = "Doubler (test)"
    inputs = [ScriptInput("x", default_channel="a")]
    outputs = [ScriptOutput("y", unit="")]

    def on_sample(self, ts, values):
        return {"y": 2.0 * values["x"]}
'''

_BAD_MISSING_ID = '''\
from gaitlab.scripts.api import AnalysisScript, ScriptOutput

class NoId(AnalysisScript):
    outputs = [ScriptOutput("y")]

    def on_sample(self, ts, values):
        return {"y": 0.0}
'''

_BAD_NO_OUTPUTS = '''\
from gaitlab.scripts.api import AnalysisScript, ScriptInput

class NoOuts(AnalysisScript):
    id = "test.no_outs"
    inputs = [ScriptInput("x")]

    def on_sample(self, ts, values):
        return {}
'''


def test_discovery_finds_valid_script_and_skips_invalid(tmp_path: Path):
    pkg = tmp_path / "package_scripts"
    pkg.mkdir()
    (pkg / "doubler.py").write_text(_DOUBLER_SOURCE, encoding="utf-8")
    (pkg / "no_id.py").write_text(_BAD_MISSING_ID, encoding="utf-8")
    (pkg / "no_outs.py").write_text(_BAD_NO_OUTPUTS, encoding="utf-8")

    found = discover_scripts(package_folder=pkg, user_folder=None)
    ids = [d.cls.id for d in found]
    assert ids == ["test.doubler"]


def test_user_folder_overrides_package_on_id_collision(tmp_path: Path):
    pkg = tmp_path / "pkg"
    usr = tmp_path / "usr"
    pkg.mkdir()
    usr.mkdir()
    (pkg / "doubler.py").write_text(_DOUBLER_SOURCE, encoding="utf-8")
    override = _DOUBLER_SOURCE.replace('display_name = "Doubler (test)"', 'display_name = "Doubler USER"')
    (usr / "doubler.py").write_text(override, encoding="utf-8")

    found = discover_scripts(package_folder=pkg, user_folder=usr)
    assert len(found) == 1
    assert found[0].cls.display_name == "Doubler USER"
    assert found[0].origin == "user"


# ---------------- runtime ----------------

class _Doubler(AnalysisScript):
    id = "runtime.doubler"
    display_name = "Doubler"
    inputs = [ScriptInput("x", default_channel="a")]
    outputs = [ScriptOutput("y", unit="")]

    def on_sample(self, ts, values):
        return {"y": 2.0 * values["x"]}


class _Boomer(AnalysisScript):
    """Always raises — used to check auto-disable-on-consecutive-errors."""

    id = "runtime.boomer"
    display_name = "Boomer"
    inputs = [ScriptInput("x")]
    outputs = [ScriptOutput("y")]

    def on_sample(self, ts, values):
        raise RuntimeError("boom")


def _wait_until(pred, timeout_s: float = 2.0, poll_s: float = 0.02) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(poll_s)
    return False


def test_runner_publishes_output_to_hub_and_stops_cleanly():
    hub = DataHub()
    hub.publish("a", 3.0)
    reg = ScriptRegistry(hub)
    reg.register_class(_Doubler)

    inst = reg.enable(
        "runtime.doubler",
        bindings=[ScriptBinding("x", "a")],
        rate_hz=50.0,
        publish_lsl=False,
    )
    assert inst is not None and inst.status.enabled

    out_id = hub_channel_id_for("runtime.doubler", "y")
    got = _wait_until(lambda: abs(hub.get_or_default(out_id) - 6.0) < 1e-6, timeout_s=1.5)
    assert got, f"expected {out_id}=6.0, got {hub.get_or_default(out_id)!r}"

    reg.disable("runtime.doubler")
    assert reg.instance("runtime.doubler") is None


def test_runner_auto_disables_after_consecutive_errors():
    hub = DataHub()
    hub.publish("x", 1.0)
    reg = ScriptRegistry(hub)
    reg.register_class(_Boomer)

    inst = reg.enable(
        "runtime.boomer",
        bindings=[ScriptBinding("x", "x")],
        rate_hz=200.0,
        publish_lsl=False,
    )
    assert inst is not None

    got = _wait_until(lambda: reg.instance("runtime.boomer") is None, timeout_s=2.0)
    assert got, "boomer should have auto-disabled after 5 consecutive errors"


# ---------------- gait peak detector ----------------

def test_gait_peak_detector_counts_sine_peaks():
    """A pure-sine input at 1.5 Hz over 4 s → about 6 peaks; cadence ~= 90 bpm."""
    # Import from the package's data/scripts folder so we exercise the shipped file.
    pkg = Path(__file__).resolve().parents[1] / "data" / "scripts"
    found = discover_scripts(package_folder=pkg, user_folder=None)
    cls_map = {d.cls.id: d.cls for d in found}
    assert "gait.peak_detector" in cls_map, "reference peak detector not discovered"
    cls = cls_map["gait.peak_detector"]

    inst = cls()
    inst.setup()

    freq_hz = 1.5
    rate_hz = 60.0
    duration_s = 4.0
    n = int(rate_hz * duration_s)
    dt = 1.0 / rate_hz

    peak_events = 0
    last_cadence = 0.0
    for i in range(n):
        ts = i * dt
        v = 0.10 * math.sin(2 * math.pi * freq_hz * ts)  # 10 cm amplitude → prominence ≈ 20 cm at peak
        out = inst.on_sample(ts, {"signal": v})
        if out.get("peak_event", 0.0) > 0.5:
            peak_events += 1
        if out.get("stride_cadence_bpm", 0.0):
            last_cadence = out["stride_cadence_bpm"]

    expected_peaks = int(freq_hz * duration_s)
    assert abs(peak_events - expected_peaks) <= 1, f"expected ~{expected_peaks} peaks, got {peak_events}"

    expected_bpm = 60.0 * freq_hz
    assert abs(last_cadence - expected_bpm) <= 3.0, f"expected ~{expected_bpm} bpm, got {last_cadence}"


def test_gait_peak_detector_stride_count_monotonic():
    pkg = Path(__file__).resolve().parents[1] / "data" / "scripts"
    found = discover_scripts(package_folder=pkg, user_folder=None)
    cls = next(d.cls for d in found if d.cls.id == "gait.peak_detector")

    inst = cls()
    inst.setup()

    rate_hz = 60.0
    dt = 1.0 / rate_hz
    prev_count = 0
    for i in range(int(rate_hz * 3.0)):
        ts = i * dt
        v = 0.10 * math.sin(2 * math.pi * 2.0 * ts)  # 2 Hz stride
        out = inst.on_sample(ts, {"signal": v})
        count = int(out["stride_count"])
        assert count >= prev_count
        prev_count = count
    assert prev_count >= 1


# ---------------- LSL outlet (optional) ----------------

# ---------------- Sprint 1 scripts ----------------


def _load_reference(cls_id: str):
    """Discover a shipped script class by id."""
    pkg = Path(__file__).resolve().parents[1] / "data" / "scripts"
    found = discover_scripts(package_folder=pkg, user_folder=None)
    for d in found:
        if d.cls.id == cls_id:
            return d.cls
    raise AssertionError(f"reference script {cls_id} not discovered")


def test_scipy_peak_detector_matches_sine():
    cls = _load_reference("signal.scipy_peak_detector")
    inst = cls()
    inst.setup()
    freq_hz = 1.5
    rate_hz = 60.0
    duration_s = 4.0
    n = int(rate_hz * duration_s)
    dt = 1.0 / rate_hz
    count = 0
    for i in range(n):
        v = 0.10 * math.sin(2 * math.pi * freq_hz * i * dt)
        out = inst.on_sample(i * dt, {"signal": v})
        if out.get("peak_event", 0.0) > 0.5:
            count += 1
    expected = int(freq_hz * duration_s)
    assert abs(count - expected) <= 1


def test_gait_events_zeni_fires_on_synthetic_walk():
    cls = _load_reference("gait.events_zeni")
    inst = cls()
    inst.setup()
    stride_hz = 1.5
    walk_v = 1.3
    ap_amp = 0.35
    rate_hz = 60.0
    duration_s = 6.0
    dt = 1.0 / rate_hz
    n = int(rate_hz * duration_s)
    r_hs = 0
    l_hs = 0
    for i in range(n):
        t = i * dt
        pelvis_x = walk_v * t
        right_x = pelvis_x + ap_amp * math.cos(2 * math.pi * stride_hz * t)
        left_x = pelvis_x + ap_amp * math.cos(2 * math.pi * stride_hz * t + math.pi)
        out = inst.on_sample(t, {"right_foot_ap": right_x,
                                 "left_foot_ap": left_x,
                                 "pelvis_ap": pelvis_x})
        if out.get("right_hs_event", 0.0) > 0.5:
            r_hs += 1
        if out.get("left_hs_event", 0.0) > 0.5:
            l_hs += 1
    # Expect ~ 1.5 * 6 = 9 heel strikes per leg (± 1 due to windowing).
    assert 6 <= r_hs <= 11, f"right HS count {r_hs} out of expected range"
    assert 6 <= l_hs <= 11, f"left HS count {l_hs} out of expected range"
    # And cadence should end near 180 bpm total (90 bpm per leg × 2 legs).
    assert 150.0 <= out["cadence_bpm"] <= 210.0


def test_joint_angles_needs_quaternions():
    cls = _load_reference("biomech.joint_angles")
    hub = DataHub()
    # No quats seeded → setup must raise a helpful message.
    inst = cls()
    inst.hub = hub
    with pytest.raises(RuntimeError, match="Publish quaternions"):
        inst.setup()


def test_joint_angles_computes_relative_rotation():
    cls = _load_reference("biomech.joint_angles")
    hub = DataHub()
    # Seed identity quats for every segment.
    for s in range(23):
        prefix = f"xsens.seg{s:02d}"
        hub.publish(f"{prefix}.qw", 1.0)
        hub.publish(f"{prefix}.qx", 0.0)
        hub.publish(f"{prefix}.qy", 0.0)
        hub.publish(f"{prefix}.qz", 0.0)
    # Rotate RightLowerLeg (16) by 45° about Y — should show up as ~45° knee flexion.
    half = math.radians(45.0) / 2
    hub.publish("xsens.seg16.qw", math.cos(half))
    hub.publish("xsens.seg16.qy", math.sin(half))
    inst = cls()
    inst.hub = hub
    inst.setup()
    out = inst.on_sample(0.0, {})
    assert abs(out["right_knee_deg"] - 45.0) < 1.0, out


def test_bilateral_symmetry_index():
    cls = _load_reference("gait.bilateral_symmetry")
    inst = cls()
    inst.setup()
    # Perfect symmetry
    out = inst.on_sample(0.0, {"right_stride": 1.0, "left_stride": 1.0,
                               "right_stance": 60.0, "left_stance": 60.0})
    assert out["si_stride_time_pct"] == 0.0
    # 10% asymmetry → SI = 200 * 0.1 / 2.1 ≈ 9.52%
    out = inst.on_sample(0.0, {"right_stride": 1.1, "left_stride": 1.0,
                               "right_stance": 60.0, "left_stance": 60.0})
    assert abs(out["si_stride_time_pct"] - 9.524) < 0.01
    # Missing side → 0 (no spurious 200%)
    out = inst.on_sample(0.0, {"right_stride": 0.0, "left_stride": 1.0,
                               "right_stance": 0.0, "left_stance": 0.0})
    assert out["si_stride_time_pct"] == 0.0


def test_lowpass_filter_attenuates_high_frequency():
    cls = _load_reference("signal.lowpass")
    inst = cls()
    inst.setup()  # 6 Hz cutoff at 60 Hz sample rate
    rate_hz = 60.0
    duration_s = 3.0
    n = int(rate_hz * duration_s)
    dt = 1.0 / rate_hz
    # Mix: 1 Hz (pass) + 25 Hz (well above cutoff, should be attenuated ~30 dB).
    outputs: list[float] = []
    inputs: list[float] = []
    for i in range(n):
        t = i * dt
        v = math.sin(2 * math.pi * 1.0 * t) + math.sin(2 * math.pi * 25.0 * t)
        inp = inst.on_sample(t, {"signal": v})
        outputs.append(inp["filtered"])
        inputs.append(v)
    # Skip the first 0.5 s (filter warmup) for the energy comparison.
    warmup = int(0.5 * rate_hz)
    in_rms = math.sqrt(sum(x * x for x in inputs[warmup:]) / (n - warmup))
    out_rms = math.sqrt(sum(x * x for x in outputs[warmup:]) / (n - warmup))
    # 25 Hz noise dominates the raw signal (RMS ≈ 1.0 total). Filtered RMS
    # should be ~ 0.7 (only the 1 Hz survives). Give a tolerant assertion.
    assert 0.5 <= out_rms <= 0.9, f"filtered RMS {out_rms:.3f} outside sanity band"
    assert out_rms < in_rms, "filter did not attenuate the signal at all"


def test_lsl_outlet_appears_when_enabled():
    """Optional smoke test — skipped if pylsl / liblsl aren't available."""
    pylsl = pytest.importorskip("pylsl")

    hub = DataHub()
    hub.publish("a", 1.0)
    reg = ScriptRegistry(hub)
    reg.register_class(_Doubler)

    inst = reg.enable(
        "runtime.doubler",
        bindings=[ScriptBinding("x", "a")],
        rate_hz=30.0,
        publish_lsl=True,
    )
    assert inst is not None and inst.status.enabled

    try:
        found = _wait_until(
            lambda: any(
                s.name() == "GaitLabScript_runtime.doubler"
                for s in pylsl.resolve_streams(wait_time=0.5)
            ),
            timeout_s=3.0,
        )
        assert found, "expected LSL stream GaitLabScript_runtime.doubler to be discoverable"
    finally:
        reg.disable("runtime.doubler")
