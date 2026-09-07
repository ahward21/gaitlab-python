"""Bilateral Symmetry Index (Robinson et al. 1987) for gait timing.

Consumes any pair of left / right scalar channels (typically the stride-time,
step-time, or stance% outputs from ``gait_events_zeni``) and reports the
Symmetry Index — the standard limb-asymmetry metric used in clinical gait labs.

    SI = 200 · |X_R − X_L| / (X_R + X_L)      (percentage; 0 = perfect symmetry)

Bilateral gait research typically flags SI > 10 % as clinically meaningful
asymmetry for temporal parameters; the acceptable range varies by variable.

References:
    * Robinson, R. O., Herzog, W., & Nigg, B. M. (1987). "Use of force
      platform variables to quantify the effects of chiropractic manipulation
      on gait symmetry." Journal of Manipulative and Physiological
      Therapeutics, 10(4), 172–176.
    * Sadeghi, H., Allard, P., Prince, F., & Labelle, H. (2000). "Symmetry
      and limb dominance in able-bodied gait: a review." Gait & Posture,
      12(1), 34–45. https://doi.org/10.1016/S0966-6362(00)00070-9
"""

from __future__ import annotations

from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput


class BilateralSymmetry(AnalysisScript):
    id = "gait.bilateral_symmetry"
    display_name = "Bilateral symmetry index"
    default_rate_hz = 20.0  # slow-changing derived metric — UI tick is enough

    inputs = [
        ScriptInput("right_stride", unit="s",
                    default_channel="script.gait.events_zeni.right_stride_time_s",
                    description="Right-leg stride time (from Zeni gait events)."),
        ScriptInput("left_stride", unit="s",
                    default_channel="script.gait.events_zeni.left_stride_time_s"),
        ScriptInput("right_stance", unit="%",
                    default_channel="script.gait.events_zeni.right_stance_pct"),
        ScriptInput("left_stance", unit="%",
                    default_channel="script.gait.events_zeni.left_stance_pct"),
    ]
    outputs = [
        ScriptOutput("si_stride_time_pct", unit="%",
                     description="200·|R−L|/(R+L) for stride time. 0 = perfect symmetry."),
        ScriptOutput("si_stance_pct", unit="%",
                     description="200·|R−L|/(R+L) for stance percentage."),
        ScriptOutput("stride_time_r", unit="s"),
        ScriptOutput("stride_time_l", unit="s"),
    ]

    def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
        rs = float(values.get("right_stride", 0.0))
        ls = float(values.get("left_stride", 0.0))
        r_st = float(values.get("right_stance", 0.0))
        l_st = float(values.get("left_stance", 0.0))
        return {
            "si_stride_time_pct": _symmetry_index(rs, ls),
            "si_stance_pct": _symmetry_index(r_st, l_st),
            "stride_time_r": rs,
            "stride_time_l": ls,
        }


def _symmetry_index(r: float, l: float) -> float:
    """Robinson SI. Returns 0.0 when either value is missing / non-positive
    (avoids reporting spurious 200% during the warmup phase)."""
    if r <= 0.0 or l <= 0.0:
        return 0.0
    denom = r + l
    if denom <= 0.0:
        return 0.0
    return 200.0 * abs(r - l) / denom
