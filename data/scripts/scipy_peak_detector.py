"""Configurable peak detector powered by ``scipy.signal.find_peaks``.

Drop-in replacement for the hand-rolled reference peak detector when you want
the parameters biomechanics researchers expect (prominence, distance, width,
height) and the industry-standard SciPy implementation. Runs ``find_peaks``
on a rolling window every tick and reports only "new" peaks (those inside the
window that weren't already reported last tick), so timing is deterministic
regardless of window length.

References:
    * Virtanen et al. (2020). "SciPy 1.0: Fundamental Algorithms for
      Scientific Computing in Python." Nature Methods 17, 261–272.
    * https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.find_peaks.html
"""

from __future__ import annotations

from collections import deque

import numpy as np
from scipy.signal import find_peaks

from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput


class ScipyPeakDetector(AnalysisScript):
    id = "signal.scipy_peak_detector"
    display_name = "Peak detector (scipy.find_peaks)"
    default_rate_hz = 60.0

    inputs = [
        ScriptInput(
            symbol="signal",
            unit="",
            default_channel="xsens.seg17.y",
            description="1D signal to search for peaks (e.g. foot vertical elevation).",
        ),
    ]
    outputs = [
        ScriptOutput("peak_event", unit="", description="1.0 on the tick a new peak enters the window, else 0.0."),
        ScriptOutput("peak_value", unit="", description="Value at the most recent peak."),
        ScriptOutput("peak_prominence", unit="", description="Prominence of the most recent peak (find_peaks output)."),
        ScriptOutput("cadence_bpm", unit="bpm", description="60 / (time between the last two peaks)."),
        ScriptOutput("peak_count", unit="", description="Monotonic counter of detected peaks."),
        ScriptOutput("signal_value", unit="", description="Passthrough of the input signal for graph overlay."),
    ]

    # Tunables — edit and Rescan, or subclass and override.
    window_seconds: float = 3.0
    prominence: float = 0.02  # metres for foot-Y; adjust to signal units
    distance_samples: int = 15  # ≥ 250 ms at 60 Hz → cadence ≤ 240 bpm
    width_samples: int | None = None  # None = no width constraint
    min_interval_s: float = 0.25  # cadence sanity gate lower bound
    max_interval_s: float = 2.0  # cadence sanity gate upper bound

    def setup(self) -> None:
        n = max(8, int(self.window_seconds * self.default_rate_hz))
        self._buf: deque[float] = deque(maxlen=n)
        self._tbuf: deque[float] = deque(maxlen=n)
        self._last_reported_peak_t: float | None = None
        self._last_peak_val: float = 0.0
        self._last_prominence: float = 0.0
        self._last_cadence_bpm: float = 0.0
        self._count: int = 0

    def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
        v = float(values.get("signal", 0.0))
        self._buf.append(v)
        self._tbuf.append(ts)

        out: dict[str, float] = {
            "peak_event": 0.0,
            "peak_value": self._last_peak_val,
            "peak_prominence": self._last_prominence,
            "cadence_bpm": self._last_cadence_bpm,
            "peak_count": float(self._count),
            "signal_value": v,
        }
        if len(self._buf) < 8:
            return out

        arr = np.fromiter(self._buf, dtype=np.float64, count=len(self._buf))
        kwargs = {"prominence": self.prominence, "distance": self.distance_samples}
        if self.width_samples is not None:
            kwargs["width"] = self.width_samples
        peak_idx, props = find_peaks(arr, **kwargs)
        if len(peak_idx) == 0:
            return out

        # Consider only peaks whose timestamps are newer than the last reported one.
        for i, idx in enumerate(peak_idx):
            peak_t = self._tbuf[idx]
            if self._last_reported_peak_t is not None and peak_t <= self._last_reported_peak_t:
                continue
            if self._last_reported_peak_t is not None:
                dt = peak_t - self._last_reported_peak_t
                if dt < self.min_interval_s:
                    continue
                if self.min_interval_s <= dt <= self.max_interval_s:
                    self._last_cadence_bpm = 60.0 / dt
                    out["cadence_bpm"] = self._last_cadence_bpm
            self._last_reported_peak_t = peak_t
            self._last_peak_val = float(arr[idx])
            self._last_prominence = float(props["prominences"][i]) if "prominences" in props else 0.0
            self._count += 1
            out["peak_event"] = 1.0
            out["peak_value"] = self._last_peak_val
            out["peak_prominence"] = self._last_prominence
            out["peak_count"] = float(self._count)
        return out
