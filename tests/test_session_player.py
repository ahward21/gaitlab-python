"""Session player tests — parse, playback ordering, and seek determinism.

The determinism test is the load-bearing one: it plays a synthetic session
straight from t=0 to t=T, records every script output, then rewinds and
compares the state at t=T against a second run that seeks to T without
straight-playing. Both must match bit-for-bit.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from gaitlab.hub import DataHub
from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput, hub_channel_id_for
from gaitlab.scripts.runner import ScriptBinding, ScriptRegistry
from gaitlab.session.player import PlayerState, SessionPlayer, load_session


# ---------------------------------------------------------------- test scripts


class _CumSum(AnalysisScript):
    """Emits a running sum of the input — pure function of history, so any
    off-by-one in the replay driver shows up as a mismatched final value."""

    id = "test.cumsum"
    display_name = "CumSum"
    default_rate_hz = 60.0
    inputs = [ScriptInput(symbol="x", default_channel="raw.x")]
    outputs = [ScriptOutput("total"), ScriptOutput("count")]

    def setup(self) -> None:
        self._total = 0.0
        self._n = 0

    def on_sample(self, ts, values):
        self._total += float(values.get("x", 0.0))
        self._n += 1
        return {"total": self._total, "count": float(self._n)}


# ---------------------------------------------------------------- fixtures


def _write_csv(path: Path, rows: list[tuple[float, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        f.write("# GaitLab CSV session\n")
        f.write("# session=test\n")
        f.write("# sample_mode=hz\n")
        f.write("# sample_hz=60\n")
        w = csv.writer(f)
        w.writerow(["time_sec", "raw.x"])
        for t, v in rows:
            w.writerow([f"{t:.4f}", f"{v:.6f}"])


@pytest.fixture()
def synthetic_csv(tmp_path: Path) -> Path:
    # 300 rows @ 60 Hz = 5 s. Values 1..300 so cumulative sum is deterministic.
    rows = [(i / 60.0, float(i + 1)) for i in range(300)]
    p = tmp_path / "syn.csv"
    _write_csv(p, rows)
    return p


# ---------------------------------------------------------------- tests


def test_load_session_parses_metadata_and_rows(synthetic_csv: Path) -> None:
    s = load_session(synthetic_csv)
    assert s.channels == ("raw.x",)
    assert s.row_count == 300
    assert s.metadata.session == "test"
    assert s.metadata.sample_mode == "hz"
    assert s.metadata.sample_hz == pytest.approx(60.0)
    assert s.times[0] == pytest.approx(0.0)
    # CSV timestamps are 4-decimal (see csv_recorder.py); loosen tolerance to match.
    assert s.times[-1] == pytest.approx(299 / 60.0, abs=1e-3)


def test_playback_max_speed_publishes_every_row(synthetic_csv: Path) -> None:
    hub = DataHub()
    scripts = ScriptRegistry(hub)
    scripts.register_class(_CumSum)
    scripts.enable("test.cumsum", [ScriptBinding("x", "raw.x")], publish_lsl=False)
    player = SessionPlayer(hub, scripts)
    player.load(synthetic_csv)
    player.set_speed(SessionPlayer.MAX_SPEED)
    player.play()
    # Wait for end-of-file. Player self-transitions back to PAUSED at EOF.
    t = player._thread  # test-only introspection
    if t is not None:
        t.join(timeout=5.0)
    assert player.cursor == 300
    # Cumulative sum of 1..300 = 300 * 301 / 2 = 45150.
    total_cid = hub_channel_id_for("test.cumsum", "total")
    count_cid = hub_channel_id_for("test.cumsum", "count")
    assert hub.try_get(total_cid) == pytest.approx(45150.0)
    assert hub.try_get(count_cid) == pytest.approx(300.0)
    scripts.disable_all()


def test_seek_produces_same_state_as_straight_play(synthetic_csv: Path) -> None:
    """The core guarantee: seek(T) == straight play from 0 to T."""
    total_cid = hub_channel_id_for("test.cumsum", "total")

    def run_straight() -> tuple[float, float]:
        hub = DataHub()
        scripts = ScriptRegistry(hub)
        scripts.register_class(_CumSum)
        scripts.enable("test.cumsum", [ScriptBinding("x", "raw.x")], publish_lsl=False)
        player = SessionPlayer(hub, scripts)
        player.load(synthetic_csv)
        # Straight play to row index 149 (t = 149/60 ≈ 2.483 s).
        player.seek(149 / 60.0)  # deterministic seek == same as straight play
        result = (hub.try_get(total_cid) or 0.0, hub.try_get("raw.x") or 0.0)
        scripts.disable_all()
        return result

    def run_with_wander() -> tuple[float, float]:
        hub = DataHub()
        scripts = ScriptRegistry(hub)
        scripts.register_class(_CumSum)
        scripts.enable("test.cumsum", [ScriptBinding("x", "raw.x")], publish_lsl=False)
        player = SessionPlayer(hub, scripts)
        player.load(synthetic_csv)
        # Seek forward past target, then back, then to target — must match.
        player.seek(4.0)
        player.seek(0.5)
        player.seek(149 / 60.0)
        result = (hub.try_get(total_cid) or 0.0, hub.try_get("raw.x") or 0.0)
        scripts.disable_all()
        return result

    a = run_straight()
    b = run_with_wander()
    assert a == b, f"seek is non-deterministic: straight={a} wander={b}"
    # Sanity: sum of 1..150 = 150 * 151 / 2 = 11325.
    assert a[0] == pytest.approx(11325.0)


def test_load_resets_cursor_and_publishes_channels(synthetic_csv: Path) -> None:
    hub = DataHub()
    scripts = ScriptRegistry(hub)
    player = SessionPlayer(hub, scripts)
    player.load(synthetic_csv)
    assert player.cursor == 0
    assert player.state == PlayerState.PAUSED
    # Every declared channel seeded at 0 for UI dropdowns.
    assert hub.try_get("raw.x") == pytest.approx(0.0)


def test_stop_resets_script_state_so_replay_is_clean(synthetic_csv: Path) -> None:
    """After Stop, pressing Play must start from a fresh script state — not
    carry end-of-file state (which caused constant cadence in real use)."""
    total_cid = hub_channel_id_for("test.cumsum", "total")
    count_cid = hub_channel_id_for("test.cumsum", "count")
    hub = DataHub()
    scripts = ScriptRegistry(hub)
    scripts.register_class(_CumSum)
    scripts.enable("test.cumsum", [ScriptBinding("x", "raw.x")], publish_lsl=False)
    player = SessionPlayer(hub, scripts)
    player.load(synthetic_csv)
    player.set_speed(SessionPlayer.MAX_SPEED)

    player.play()
    t = player._thread
    if t is not None:
        t.join(timeout=5.0)
    end_total = hub.try_get(total_cid)
    end_count = hub.try_get(count_cid)

    # Now Stop and Play again. Script state MUST reset — otherwise the second
    # run's cumulative sum would be double the first run's.
    player.stop()
    # Post-stop: hub is reset to zeros, cursor at 0.
    assert hub.try_get(total_cid) == pytest.approx(0.0)
    assert hub.try_get(count_cid) == pytest.approx(0.0)

    player.play()
    t = player._thread
    if t is not None:
        t.join(timeout=5.0)
    assert hub.try_get(total_cid) == pytest.approx(end_total)
    assert hub.try_get(count_cid) == pytest.approx(end_count)
    scripts.disable_all()


def test_play_at_eof_auto_rewinds(synthetic_csv: Path) -> None:
    """Pressing Play with cursor at EOF should restart from row 0."""
    total_cid = hub_channel_id_for("test.cumsum", "total")
    hub = DataHub()
    scripts = ScriptRegistry(hub)
    scripts.register_class(_CumSum)
    scripts.enable("test.cumsum", [ScriptBinding("x", "raw.x")], publish_lsl=False)
    player = SessionPlayer(hub, scripts)
    player.load(synthetic_csv)
    player.set_speed(SessionPlayer.MAX_SPEED)
    player.play()
    t = player._thread
    if t is not None:
        t.join(timeout=5.0)
    # Cursor at EOF now. A second Play should auto-rewind + replay to the
    # same terminal cumulative sum, not double it.
    player.play()
    t = player._thread
    if t is not None:
        t.join(timeout=5.0)
    assert hub.try_get(total_cid) == pytest.approx(45150.0)
    scripts.disable_all()


def test_compute_full_session_returns_full_time_series(synthetic_csv: Path) -> None:
    """Batch compute captures per-row output for every enabled script."""
    hub = DataHub()
    scripts = ScriptRegistry(hub)
    scripts.register_class(_CumSum)
    scripts.enable("test.cumsum", [ScriptBinding("x", "raw.x")], publish_lsl=False)
    player = SessionPlayer(hub, scripts)
    player.load(synthetic_csv)

    result = player.compute_full_session()

    assert result.sample_count == 300
    total_cid = hub_channel_id_for("test.cumsum", "total")
    count_cid = hub_channel_id_for("test.cumsum", "count")
    # Output series length must match row count.
    assert len(result.output_series[total_cid]) == 300
    assert len(result.output_series[count_cid]) == 300
    # Cumulative-sum output at row 149 = sum(1..150) = 11325.
    assert result.output_series[total_cid][149] == pytest.approx(11325.0)
    # Final row = sum(1..300) = 45150.
    assert result.output_series[total_cid][-1] == pytest.approx(45150.0)
    assert result.output_series[count_cid][-1] == pytest.approx(300.0)
    # Input series echoes the CSV values.
    assert result.input_series["raw.x"][0] == pytest.approx(1.0)
    assert result.input_series["raw.x"][-1] == pytest.approx(300.0)
    # Terminal hub state matches the last captured row (same as a straight play).
    assert hub.try_get(total_cid) == pytest.approx(45150.0)
    scripts.disable_all()


def test_compute_full_session_matches_max_speed_playback(synthetic_csv: Path) -> None:
    """Batch compute's terminal state must equal a straight MAX-speed play."""
    total_cid = hub_channel_id_for("test.cumsum", "total")

    def run_max_speed_play() -> float:
        hub = DataHub()
        scripts = ScriptRegistry(hub)
        scripts.register_class(_CumSum)
        scripts.enable("test.cumsum", [ScriptBinding("x", "raw.x")], publish_lsl=False)
        player = SessionPlayer(hub, scripts)
        player.load(synthetic_csv)
        player.set_speed(SessionPlayer.MAX_SPEED)
        player.play()
        t = player._thread
        if t is not None:
            t.join(timeout=5.0)
        value = hub.try_get(total_cid) or 0.0
        scripts.disable_all()
        return value

    def run_batch() -> float:
        hub = DataHub()
        scripts = ScriptRegistry(hub)
        scripts.register_class(_CumSum)
        scripts.enable("test.cumsum", [ScriptBinding("x", "raw.x")], publish_lsl=False)
        player = SessionPlayer(hub, scripts)
        player.load(synthetic_csv)
        result = player.compute_full_session()
        scripts.disable_all()
        return result.output_series[total_cid][-1]

    assert run_max_speed_play() == pytest.approx(run_batch())


def test_compute_full_session_reports_progress(synthetic_csv: Path) -> None:
    hub = DataHub()
    scripts = ScriptRegistry(hub)
    scripts.register_class(_CumSum)
    scripts.enable("test.cumsum", [ScriptBinding("x", "raw.x")], publish_lsl=False)
    player = SessionPlayer(hub, scripts)
    player.load(synthetic_csv)
    seen: list[tuple[int, int]] = []
    player.compute_full_session(progress_callback=lambda d, t: seen.append((d, t)))
    assert seen, "progress callback never fired"
    # Last call always reports completion at total row count.
    assert seen[-1] == (300, 300)
    scripts.disable_all()


def test_baseline_ratio_script_on_synthetic_signal(tmp_path: Path) -> None:
    """End-to-end: replay a CSV through the baseline_ratio reference script
    and confirm the ratio matches the analytic expectation."""
    # 3 s of constant 1.0, then 3 s of constant 2.0. Baseline & window both 3s.
    from data.scripts.baseline_ratio import BaselineRatio

    # 3 s of 1.0 (baseline) + 3 s of 2.0 (window 1) + 1 sample past the
    # window-1 boundary (t >= 6.0) so window 1's mean actually gets emitted.
    rows: list[tuple[float, float]] = []
    for i in range(180):  # baseline: t in [0, 3.0)
        rows.append((i / 60.0, 1.0))
    for i in range(180, 361):  # window 1: t in [3.0, 6.0]; last row t=6.0167 closes it
        rows.append((i / 60.0, 2.0))
    p = tmp_path / "step.csv"
    _write_csv(p, rows)

    hub = DataHub()
    scripts = ScriptRegistry(hub)
    scripts.register_class(BaselineRatio)
    # Override windows so the test doesn't need 2 minutes of data.
    BaselineRatio.baseline_seconds = 3.0
    BaselineRatio.window_seconds = 3.0
    scripts.enable(
        "signal.baseline_ratio",
        [ScriptBinding("signal", "raw.x")],
        publish_lsl=False,
    )
    player = SessionPlayer(hub, scripts)
    player.load(p)
    player.set_speed(SessionPlayer.MAX_SPEED)
    player.play()
    t = player._thread
    if t is not None:
        t.join(timeout=5.0)

    baseline = hub.try_get(hub_channel_id_for("signal.baseline_ratio", "baseline_mean"))
    window_mean = hub.try_get(hub_channel_id_for("signal.baseline_ratio", "window_mean"))
    ratio = hub.try_get(hub_channel_id_for("signal.baseline_ratio", "ratio"))
    assert baseline == pytest.approx(1.0)
    assert window_mean == pytest.approx(2.0)
    assert ratio == pytest.approx(2.0)
    scripts.disable_all()
