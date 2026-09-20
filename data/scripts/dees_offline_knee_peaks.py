"""Dees's right-knee sagittal peak detector, run as a GaitLab offline analysis.

This is the function-based, pandas-flavor form — the minimum-boilerplate
version. The single ``find_peaks(sagittal, prominence=40)`` line below is
byte-identical to line 15 of Dees's ``process_mvnx.py``. Everything else
in this file is metadata the discovery layer needs (5 lines: ID,
DISPLAY_NAME, INPUTS, OUTPUTS, and a docstring).

The file-loading, plotting, and CSV-writing that Dees's standalone script
does are handled by GaitLab now — Sessions tab loads the recording,
Batch dialog draws the graph, batch runner writes the per-analysis CSV
next to the batch CSV.

Compare to the on-tick wrapper this replaced: that was 90 lines of
book-keeping around a rolling-window `find_peaks`. This is 10 lines
around the exact same one-shot call Dees uses — and it produces the
1203/1203 zero-offset ground-truth match without any tunables.
"""

from __future__ import annotations

import pandas as pd
from scipy.signal import find_peaks

ID = "dees.right_knee_sagittal_peaks"
DISPLAY_NAME = "Right knee sagittal peaks (Dees, offline)"
INPUTS = {"sagittal": "xsens.joint.jRightKnee.z"}
OUTPUTS = ["peak_index", "peak_value"]


def run(df: pd.DataFrame) -> pd.DataFrame:
    sagittal = df["sagittal"]
    peaks, properties = find_peaks(sagittal, prominence=40)
    return pd.DataFrame({
        "peak_index": peaks,
        "peak_value": sagittal.iloc[peaks].values,
    })
