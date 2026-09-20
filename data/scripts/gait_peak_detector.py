"""Reference AnalysisScript: real-time gait peak detection.

Watches one vertical-position signal (default: RightFoot Y from Xsens MVN
segment 17) and emits an event on every mid-swing peak — the moment the foot
is at maximum elevation, once per stride. From consecutive peak times it
derives instantaneous stride cadence in BPM.

Detector is a hand-rolled 3-sample local-maximum with a prominence threshold
and a hard refractory period, chosen so the reference script has *no*
scientific-python dependency. Swap in ``scipy.signal.find_peaks`` if you need
smoother behaviour on noisy or lower-Hz signals.
"""

from __future__ import annotations

from collections import deque

from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput


class GaitPeakDetector(AnalysisScript):
    id = "gait.peak_detector"
    display_name = "Gait peak detector"
    default_rate_hz = 60.0

    inputs = [
        ScriptInput(
            symbol="signal",
            unit="m",
            default_channel="xsens.seg17.y",  # RightFoot vertical elevation (MVN Y-up)
            description="Vertical position of the foot (or any 1D gait signal with clear stride peaks).",
        ),
    ]
    outputs = [
        ScriptOutput("peak_event", unit="", description="1.0 on the tick a peak is detected, else 0.0."),
        ScriptOutput("stride_cadence_bpm", unit="bpm", description="60 / (time between the last two peaks)."),
        ScriptOutput("stride_count", unit="", description="Monotonically increasing count of detected peaks."),
        ScriptOutput("signal_value", unit="m", description="Passthrough of the input signal, for graph overlay."),
    ]

    # Tunables (kept as attributes so a subclass can override without editing the loop).
    # Window = 2 * half_window + 1 samples. At 60 Hz, half_window=3 → ~117 ms window,
    # which is comfortably shorter than a normal stride (~500 ms) and long enough that
    # adjacent-sample values differ by more than ``prominence`` on a real gait signal.
    half_window: int = 3
    prominence: float = 0.01  # metres — the middle sample must exceed the window minimum by this much
    min_interval_s: float = 0.25  # refractory → cadence ≤ 240 bpm
    max_interval_s: float = 2.0  # cadence ≥ 30 bpm

    def setup(self) -> None:
        win = 2 * self.half_window + 1
        self._buf: deque[float] = deque(maxlen=win)
        self._tbuf: deque[float] = deque(maxlen=win)
        self._last_peak_t: float | None = None
        self._count: int = 0
        self._last_cadence_bpm: float = 0.0

    def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
        v = float(values.get("signal", 0.0))
        self._buf.append(v)
        self._tbuf.append(ts)

        out: dict[str, float] = {
            "peak_event": 0.0,
            "stride_count": float(self._count),
            "stride_cadence_bpm": self._last_cadence_bpm,
            "signal_value": v,
        }

        win = 2 * self.half_window + 1
        if len(self._buf) < win:
            return out

        mid = self.half_window
        middle = self._buf[mid]
        # Peak = middle sample is the max in the window (>= on ties so we don't
        # miss plateaus, then filter with prominence + refractory).
        for i in range(win):
            if i == mid:
                continue
            if self._buf[i] > middle:
                return out
        prominence = middle - min(self._buf)
        if prominence < self.prominence:
            return out

        peak_t = self._tbuf[mid]
        if self._last_peak_t is not None:
            dt = peak_t - self._last_peak_t
            if dt < self.min_interval_s:
                return out
            if dt <= self.max_interval_s and dt > 0:
                self._last_cadence_bpm = 60.0 / dt
                out["stride_cadence_bpm"] = self._last_cadence_bpm

        self._count += 1
        self._last_peak_t = peak_t
        out["peak_event"] = 1.0
        out["stride_count"] = float(self._count)
        return out
