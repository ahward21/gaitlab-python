"""Reference AnalysisScript: first-window baseline vs subsequent-window ratio.

Answers the meeting example: "take the acceleration of the first minute, then
for every minute after that check the average and its ratio to the baseline."

Generalises to any 1D hub channel and any window length. Once the baseline
window closes, ``baseline_mean`` is frozen for the rest of the run and every
subsequent window emits its own ``window_mean`` plus ``ratio =
window_mean / baseline_mean``. Windows are non-overlapping and start
back-to-back from ``ts=0`` (recording start).

Behaviour is defined against the ``ts`` argument of ``on_sample``, so it is
identical live vs replayed at any speed.
"""

from __future__ import annotations

from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput


class BaselineRatio(AnalysisScript):
    id = "signal.baseline_ratio"
    display_name = "Baseline window ratio"
    default_rate_hz = 60.0

    inputs = [
        ScriptInput(
            symbol="signal",
            unit="",
            default_channel="",
            description=(
                "Any 1D channel — magnitude of acceleration, foot-swing height, "
                "cadence, heart rate. Baseline mean is computed over the first "
                "`baseline_seconds`; subsequent windows are `window_seconds` long."
            ),
        ),
    ]
    outputs = [
        ScriptOutput("baseline_mean", unit="",
                     description="Mean of `signal` across the first baseline_seconds. Frozen after t = baseline_seconds."),
        ScriptOutput("window_mean", unit="",
                     description="Mean of `signal` across the most recently closed window_seconds window."),
        ScriptOutput("ratio", unit="",
                     description="window_mean / baseline_mean. 1.0 = matches baseline; >1 above, <1 below."),
        ScriptOutput("window_index", unit="",
                     description="0 during baseline, 1 for the first post-baseline window, 2 for the second, ..."),
    ]

    baseline_seconds: float = 60.0
    window_seconds: float = 60.0

    def setup(self) -> None:
        self._t0: float | None = None
        self._sum: float = 0.0
        self._count: int = 0
        # Filled once the baseline window closes.
        self._baseline_mean: float | None = None
        self._current_window: int = 0
        # Last emitted values, reused between ticks so the CSV/graphs don't hole.
        self._last_baseline: float = 0.0
        self._last_window_mean: float = 0.0
        self._last_ratio: float = 0.0

    def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
        v = float(values.get("signal", 0.0))
        if self._t0 is None:
            self._t0 = ts
        elapsed = ts - self._t0

        # Which window does THIS sample belong to? Compute before accumulating
        # so the boundary sample lands in the new window, not the closing one.
        if elapsed < self.baseline_seconds:
            window_idx = 0
        else:
            window_idx = 1 + int(
                (elapsed - self.baseline_seconds) // max(self.window_seconds, 1e-6)
            )

        if window_idx != self._current_window:
            # Window boundary crossed. Emit the mean of the just-closed window
            # from the accumulator (which does NOT include v — v is the first
            # sample of the new window).
            mean = self._sum / self._count if self._count else 0.0
            if self._current_window == 0:
                self._baseline_mean = mean
                self._last_baseline = mean
            else:
                self._last_window_mean = mean
                if self._baseline_mean and abs(self._baseline_mean) > 1e-12:
                    self._last_ratio = mean / self._baseline_mean
                else:
                    self._last_ratio = 0.0
            self._sum = 0.0
            self._count = 0
            self._current_window = window_idx

        self._sum += v
        self._count += 1

        return {
            "baseline_mean": self._last_baseline,
            "window_mean": self._last_window_mean,
            "ratio": self._last_ratio,
            "window_index": float(self._current_window),
        }

    def teardown(self) -> None:
        self._t0 = None
        self._sum = 0.0
        self._count = 0
        self._baseline_mean = None
        self._current_window = 0
