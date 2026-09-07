"""Offline analysis API — runs once, after ``compute_full_session`` has
collected the full per-frame trace. Complements the tick-based
``AnalysisScript``: same discovery folders, same enable-in-UI shape, but
each analysis sees the *whole* recording as arrays and returns arrays or a
DataFrame back.

Three ways to write one — pick the one that matches your idioms. All
three are discovered from the same folders (``data/scripts/`` and
``~/GaitLabScripts/``) and appear together in the Sessions tab's
"Offline analyses" list.

**1. Class flavor — NumPy** (fast, no pandas dep in the researcher's file):

    class MyAnalysis(NumpyOfflineAnalysis):
        id = "signal.my_analysis"
        display_name = "My analysis"
        inputs = [OfflineInput("signal", default_channel="xsens.joint.jRightKnee.z")]
        outputs = [OfflineOutput("peak_index"), OfflineOutput("peak_value")]

        def run(self, times, arrays):
            signal = arrays["signal"]                        # np.ndarray, shape (N,)
            peaks, _ = find_peaks(signal, prominence=40)
            return {
                "peak_index": peaks.astype(float),
                "peak_value": signal[peaks],
            }

**2. Class flavor — pandas** (idiomatic for time-indexed work):

    class MyAnalysis(PandasOfflineAnalysis):
        id = "signal.my_analysis"
        display_name = "My analysis"
        inputs = [OfflineInput("sagittal", default_channel="xsens.joint.jRightKnee.z")]
        outputs = [OfflineOutput("peak_index"), OfflineOutput("peak_value")]

        def run(self, df):
            sagittal = df["sagittal"]                        # pd.Series indexed by time_sec
            peaks, _ = find_peaks(sagittal, prominence=40)
            return pd.DataFrame({
                "peak_index": peaks,
                "peak_value": sagittal.iloc[peaks].values,
            })

**3. Function flavor — pandas** (minimum boilerplate — a `run(df)` at
module level plus a few metadata constants):

    # in ~/GaitLabScripts/my_analysis.py
    ID = "signal.my_analysis"
    DISPLAY_NAME = "My analysis"
    INPUTS = {"sagittal": "xsens.joint.jRightKnee.z"}
    OUTPUTS = ["peak_index", "peak_value"]

    def run(df):
        peaks, _ = find_peaks(df["sagittal"], prominence=40)
        return pd.DataFrame({
            "peak_index": peaks,
            "peak_value": df["sagittal"].iloc[peaks].values,
        })

Function-flavor scripts default to the pandas signature; set
``FLAVOR = "numpy"`` at module level to have ``run(times, arrays)``
called instead.

Return shape (all flavors): a mapping (dict or DataFrame) from output
symbol -> 1D array-like of floats. Length can be anything — offline
outputs are typically sparse (peak lists, event indices) so they are not
merged into the per-frame batch CSV; they are written to their own
``<batch>_offline_<id>.csv`` and can be overlaid on the batch graph
when the columns follow the ``peak_index`` / ``peak_value`` convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ClassVar

import numpy as np


@dataclass(frozen=True)
class OfflineInput:
    """One named signal the analysis consumes from the loaded session.

    ``symbol`` is what the analysis sees as the key in ``arrays[symbol]``
    (numpy flavor) or as the column name in ``df[symbol]`` (pandas
    flavor). ``default_channel`` is the hub channel id pre-bound; the
    Sessions panel does not currently expose a per-analysis rebinding
    UI, so this is where a researcher chooses their input channel."""

    symbol: str
    unit: str = ""
    default_channel: str = ""
    description: str = ""


@dataclass(frozen=True)
class OfflineOutput:
    """One named output the analysis publishes.

    Output values are variable-length 1D arrays. Downstream: written to
    a per-analysis CSV; if the outputs follow the ``peak_index`` /
    ``peak_value`` convention they are also overlaid as scatter markers
    on the batch graph."""

    symbol: str
    unit: str = ""
    description: str = ""


class OfflineAnalysis:
    """Base class — never subclassed directly. Inherit from
    :class:`NumpyOfflineAnalysis` or :class:`PandasOfflineAnalysis`,
    or use the function-based form documented in the module docstring.

    Discovery treats any concrete subclass with a non-empty ``id`` and
    non-empty ``outputs`` as valid, so the class hierarchy is
    intentionally shallow — the flavor is a class attribute, not a
    protocol."""

    id: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    inputs: ClassVar[list[OfflineInput]] = []
    outputs: ClassVar[list[OfflineOutput]] = []
    flavor: ClassVar[str] = ""  # "numpy" | "pandas" — set by the flavored subclasses.

    def run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "Inherit from NumpyOfflineAnalysis or PandasOfflineAnalysis."
        )


class NumpyOfflineAnalysis(OfflineAnalysis):
    """NumPy flavor. Runner calls ``run(times, arrays)`` where ``times``
    is a 1D float array of session seconds and ``arrays`` is a dict of
    symbol -> 1D array aligned with ``times``."""

    flavor: ClassVar[str] = "numpy"

    def run(self, times: np.ndarray, arrays: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        raise NotImplementedError


class PandasOfflineAnalysis(OfflineAnalysis):
    """Pandas flavor. Runner imports pandas lazily, builds a DataFrame
    with ``time_sec`` as the index and one column per input symbol, and
    calls ``run(df)``. Return a dict-of-arrays OR a DataFrame — both
    are normalised to dict-of-arrays downstream."""

    flavor: ClassVar[str] = "pandas"

    def run(self, df: "Any") -> "Any":  # noqa: F821 - avoids pandas import at import time.
        raise NotImplementedError


# ------------------------------------------------------------------ function form


# Module-level metadata attribute names the discovery layer looks for on
# function-based scripts. Exposed here so the discovery + docs stay in
# sync with any renames.
FUNC_META_ID = "ID"
FUNC_META_DISPLAY = "DISPLAY_NAME"
FUNC_META_INPUTS = "INPUTS"
FUNC_META_OUTPUTS = "OUTPUTS"
FUNC_META_FLAVOR = "FLAVOR"
FUNC_META_DESCRIPTION = "DESCRIPTION"


class FunctionOfflineAnalysis(OfflineAnalysis):
    """Adapter class produced by discovery when it finds a module with a
    top-level ``run(...)`` function and the ``ID`` / ``INPUTS`` / ``OUTPUTS``
    metadata constants. Not intended to be subclassed by researchers —
    they write the function directly and this class wraps it.

    Kept as a real ``OfflineAnalysis`` subclass so the runner, registry,
    and Sessions-tab UI don't need to distinguish function-based from
    class-based analyses."""

    _fn: ClassVar[Callable[..., Any] | None] = None

    def run(self, *args: Any, **kwargs: Any) -> Any:
        fn = type(self)._fn
        if fn is None:
            raise RuntimeError(
                f"FunctionOfflineAnalysis subclass {type(self).__name__!r} has no _fn set."
            )
        return fn(*args, **kwargs)


def build_function_adapter(
    module: object,
    fn: Callable[..., Any],
) -> type[FunctionOfflineAnalysis] | None:
    """Turn a module + ``run`` function into a discoverable OfflineAnalysis
    subclass. Returns None if the module is missing required metadata.

    Called by the discovery layer; researchers never invoke this.
    """
    script_id = getattr(module, FUNC_META_ID, None)
    outputs_meta = getattr(module, FUNC_META_OUTPUTS, None)
    if not script_id or not outputs_meta:
        return None

    display = getattr(module, FUNC_META_DISPLAY, None) or script_id
    inputs_meta = getattr(module, FUNC_META_INPUTS, None) or {}
    flavor_meta = getattr(module, FUNC_META_FLAVOR, None) or "pandas"
    description = getattr(module, FUNC_META_DESCRIPTION, None) or ""

    if flavor_meta not in ("numpy", "pandas"):
        raise ValueError(
            f"Function-based offline script {script_id!r}: "
            f"FLAVOR must be 'numpy' or 'pandas', got {flavor_meta!r}."
        )

    input_objs = [
        OfflineInput(symbol=symbol, default_channel=channel)
        for symbol, channel in dict(inputs_meta).items()
    ]
    output_objs = [
        OfflineOutput(symbol=str(sym)) for sym in list(outputs_meta)
    ]

    # Build a bespoke subclass so each module ends up with its own type
    # object — cleanly distinct in the registry, sensibly named in tracebacks.
    cls_name = "".join(
        part.capitalize() for part in str(script_id).replace(".", "_").split("_") if part
    ) or "FunctionOfflineAnalysis"
    adapter = type(
        cls_name,
        (FunctionOfflineAnalysis,),
        {
            "id": str(script_id),
            "display_name": str(display),
            "inputs": input_objs,
            "outputs": output_objs,
            "flavor": flavor_meta,
            "_fn": staticmethod(fn),
            "__doc__": description or f"Function-based offline analysis {script_id!r}.",
        },
    )
    return adapter
