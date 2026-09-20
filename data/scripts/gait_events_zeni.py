"""Zeni (2008) heel-strike / toe-off detector + basic stride timing.

Detects gait events kinematically — no force plate or foot-switch needed —
using the coordinate difference between each foot and the sacrum along the
direction of progression:

    d_R(t) = RightFoot_x(t) − Sacrum_x(t)
    d_L(t) = LeftFoot_x(t)  − Sacrum_x(t)

Heel strike (initial contact) = local maximum of d.
Toe off                       = local minimum of d.

From consecutive heel strikes we compute per-leg stride time and cadence;
step time is the interval between contralateral heel strikes; stance% is the
fraction of a stride during which the foot is between HS and the following
TO of the same leg.

The detection uses ``scipy.signal.find_peaks`` on a rolling window (same
approach as ``ScipyPeakDetector``) so the algorithm is deterministic and its
parameters are the ones biomechanics researchers already recognise.

References:
    * Zeni, J. A., Richards, J. G., & Higginson, J. S. (2008). "Two simple
      methods for determining gait events during treadmill and overground
      walking using kinematic data." Gait & Posture, 27(4), 710–714.
      https://doi.org/10.1016/j.gaitpost.2007.07.007
    * The "coordinate-based" method (Method 1 in the paper) uses the sacral
      marker + heel markers; we substitute Pelvis and Foot segment positions
      from MVN, which is functionally equivalent when the participant walks
      along a consistent forward axis.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from scipy.signal import find_peaks

from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput


class GaitEventsZeni(AnalysisScript):
    id = "gait.events_zeni"
    display_name = "Gait events — Zeni (2008)"
    default_rate_hz = 60.0

    inputs = [
        ScriptInput("right_foot_ap", unit="m", default_channel="xsens.seg17.x",
                    description="RightFoot position along the direction of progression."),
        ScriptInput("left_foot_ap", unit="m", default_channel="xsens.seg21.x",
                    description="LeftFoot position along the direction of progression."),
        ScriptInput("pelvis_ap", unit="m", default_channel="xsens.seg00.x",
                    description="Pelvis (sacrum proxy) position along the direction of progression."),
    ]
    outputs = [
        ScriptOutput("right_hs_event", unit="", description="1.0 on right heel-strike sample."),
        ScriptOutput("right_to_event", unit="", description="1.0 on right toe-off sample."),
        ScriptOutput("left_hs_event", unit="", description="1.0 on left heel-strike sample."),
        ScriptOutput("left_to_event", unit="", description="1.0 on left toe-off sample."),
        ScriptOutput("right_stride_time_s", unit="s", description="Time between consecutive right heel strikes."),
        ScriptOutput("left_stride_time_s", unit="s", description="Time between consecutive left heel strikes."),
        ScriptOutput("step_time_s", unit="s", description="Time between contralateral heel strikes (last R→L or L→R)."),
        ScriptOutput("cadence_bpm", unit="bpm", description="120 / mean(stride_time) — total steps per minute."),
        ScriptOutput("right_stance_pct", unit="%", description="Right stance duration as % of the last right stride."),
        ScriptOutput("left_stance_pct", unit="%", description="Left stance duration as % of the last left stride."),
        ScriptOutput("right_hs_count", unit="", description="Monotonic counter."),
        ScriptOutput("left_hs_count", unit="", description="Monotonic counter."),
        ScriptOutput("right_d_signal", unit="m", description="d_R = right_foot_ap − pelvis_ap (for graph overlay)."),
        ScriptOutput("left_d_signal", unit="m", description="d_L = left_foot_ap − pelvis_ap (for graph overlay)."),
    ]

    window_seconds: float = 3.0
    prominence: float = 0.05  # metres — Zeni signal amplitude is ~step length
    distance_samples: int = 20  # ~333 ms between events → cadence ≤ 180 bpm
    min_stride_s: float = 0.5
    max_stride_s: float = 2.0

    def setup(self) -> None:
        n = max(8, int(self.window_seconds * self.default_rate_hz))
        self._right = _LegState(n)
        self._left = _LegState(n)
        self._last_step_t: float | None = None
        self._last_step_side: str = ""  # "R" or "L"
        self._step_time_s: float = 0.0
        self._cadence_bpm: float = 0.0

    def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
        pelvis = float(values.get("pelvis_ap", 0.0))
        d_r = float(values.get("right_foot_ap", 0.0)) - pelvis
        d_l = float(values.get("left_foot_ap", 0.0)) - pelvis

        r_events = self._right.push(ts, d_r, self.prominence, self.distance_samples,
                                    self.min_stride_s, self.max_stride_s)
        l_events = self._left.push(ts, d_l, self.prominence, self.distance_samples,
                                   self.min_stride_s, self.max_stride_s)

        out: dict[str, float] = {
            "right_hs_event": 1.0 if r_events["hs"] else 0.0,
            "right_to_event": 1.0 if r_events["to"] else 0.0,
            "left_hs_event": 1.0 if l_events["hs"] else 0.0,
            "left_to_event": 1.0 if l_events["to"] else 0.0,
            "right_stride_time_s": self._right.last_stride_s,
            "left_stride_time_s": self._left.last_stride_s,
            "right_stance_pct": self._right.last_stance_pct,
            "left_stance_pct": self._left.last_stance_pct,
            "right_hs_count": float(self._right.hs_count),
            "left_hs_count": float(self._left.hs_count),
            "right_d_signal": d_r,
            "left_d_signal": d_l,
        }

        # Step time = interval between contralateral heel strikes.
        if r_events["hs"]:
            if self._last_step_t is not None and self._last_step_side == "L":
                self._step_time_s = ts - self._last_step_t
            self._last_step_t = ts
            self._last_step_side = "R"
        if l_events["hs"]:
            if self._last_step_t is not None and self._last_step_side == "R":
                self._step_time_s = ts - self._last_step_t
            self._last_step_t = ts
            self._last_step_side = "L"

        # Cadence = total steps per minute from the mean of both legs' latest strides.
        strides = [s for s in (self._right.last_stride_s, self._left.last_stride_s) if s > 0]
        if strides:
            self._cadence_bpm = 120.0 / (sum(strides) / len(strides))
        out["step_time_s"] = self._step_time_s
        out["cadence_bpm"] = self._cadence_bpm
        return out


class _LegState:
    """Per-leg buffer + last-event bookkeeping."""

    def __init__(self, buffer_len: int) -> None:
        self.buf: deque[float] = deque(maxlen=buffer_len)
        self.tbuf: deque[float] = deque(maxlen=buffer_len)
        self.last_hs_t: float | None = None
        self.last_to_t: float | None = None
        self.last_stride_s: float = 0.0
        self.last_stance_pct: float = 0.0
        self.hs_count: int = 0

    def push(self, ts: float, d: float,
             prominence: float, distance: int,
             min_stride_s: float, max_stride_s: float) -> dict[str, bool]:
        self.buf.append(d)
        self.tbuf.append(ts)
        events = {"hs": False, "to": False}
        if len(self.buf) < 8:
            return events

        arr = np.fromiter(self.buf, dtype=np.float64, count=len(self.buf))
        hs_idx, _ = find_peaks(arr, prominence=prominence, distance=distance)
        to_idx, _ = find_peaks(-arr, prominence=prominence, distance=distance)

        # Only fire on NEW events (newer than what we've already reported).
        for idx in hs_idx:
            peak_t = self.tbuf[idx]
            if self.last_hs_t is not None and peak_t <= self.last_hs_t:
                continue
            if self.last_hs_t is not None:
                dt = peak_t - self.last_hs_t
                if not (min_stride_s <= dt <= max_stride_s):
                    continue
                self.last_stride_s = dt
                # stance% requires a toe-off between the two heel strikes.
                if self.last_to_t is not None and self.last_hs_t < self.last_to_t < peak_t:
                    self.last_stance_pct = 100.0 * (self.last_to_t - self.last_hs_t) / dt
            self.last_hs_t = peak_t
            self.hs_count += 1
            events["hs"] = True

        for idx in to_idx:
            trough_t = self.tbuf[idx]
            if self.last_to_t is not None and trough_t <= self.last_to_t:
                continue
            self.last_to_t = trough_t
            events["to"] = True

        return events
