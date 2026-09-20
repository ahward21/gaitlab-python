"""Tests for the offline analysis API.

Covers all three ways to write an offline analysis (NumPy class, pandas
class, function-based) plus the discovery layer that finds them and the
runner that dispatches by flavor + normalises returns.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gaitlab.scripts.discovery import discover_offline_analyses
from gaitlab.scripts.offline_api import (
    NumpyOfflineAnalysis,
    OfflineInput,
    OfflineOutput,
    PandasOfflineAnalysis,
)
from gaitlab.scripts.offline_runner import OfflineRegistry


# ------------------------------------------------------------------ fixtures


@pytest.fixture()
def synthetic_signal() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """500-sample linear ramp + a big spike at index 250 — a peak with
    prominence >> anything else, so find_peaks(prominence=10) sees exactly one."""
    n = 500
    t = np.arange(n) / 240.0
    x = 0.001 * np.arange(n, dtype=float)  # tiny ramp
    x[250] = 100.0                          # single clear peak
    return t, {"signal": x}


# ------------------------------------------------------------------ NumPy class flavor


class _NumpyPeakCount(NumpyOfflineAnalysis):
    id = "test.numpy_peak_count"
    display_name = "test numpy peak count"
    inputs = [OfflineInput("signal", default_channel="raw.x")]
    outputs = [OfflineOutput("peak_index"), OfflineOutput("peak_value")]

    def run(self, times, arrays):
        from scipy.signal import find_peaks
        sig = arrays["signal"]
        peaks, _ = find_peaks(sig, prominence=10)
        return {
            "peak_index": peaks.astype(float),
            "peak_value": sig[peaks],
        }


def test_numpy_flavor_runs_and_returns_declared_outputs(synthetic_signal):
    times, arrays = synthetic_signal
    reg = OfflineRegistry()
    reg.register_class(_NumpyPeakCount)
    reg.enable("test.numpy_peak_count")

    results = reg.run_all(times, {"raw.x": arrays["signal"]})

    assert len(results) == 1
    r = results[0]
    assert r.ok, f"analysis errored: {r.error}"
    assert r.flavor == "numpy"
    assert list(r.outputs["peak_index"]) == [250.0]
    assert r.outputs["peak_value"][0] == pytest.approx(100.0)


# ------------------------------------------------------------------ pandas class flavor


class _PandasPeakCount(PandasOfflineAnalysis):
    id = "test.pandas_peak_count"
    display_name = "test pandas peak count"
    inputs = [OfflineInput("sagittal", default_channel="raw.x")]
    outputs = [OfflineOutput("peak_index"), OfflineOutput("peak_value")]

    def run(self, df):
        from scipy.signal import find_peaks
        sig = df["sagittal"]
        peaks, _ = find_peaks(sig, prominence=10)
        return pd.DataFrame({
            "peak_index": peaks,
            "peak_value": sig.iloc[peaks].values,
        })


def test_pandas_flavor_dataframe_return_is_normalised_to_dict(synthetic_signal):
    times, arrays = synthetic_signal
    reg = OfflineRegistry()
    reg.register_class(_PandasPeakCount)
    reg.enable("test.pandas_peak_count")

    results = reg.run_all(times, {"raw.x": arrays["signal"]})

    r = results[0]
    assert r.ok, r.error
    assert r.flavor == "pandas"
    assert set(r.outputs.keys()) == {"peak_index", "peak_value"}
    assert r.outputs["peak_index"][0] == pytest.approx(250)
    assert r.outputs["peak_value"][0] == pytest.approx(100.0)


def test_pandas_flavor_dataframe_index_is_time_sec(synthetic_signal):
    """The DataFrame the runner constructs must be time-indexed so
    researchers can use df.loc[t0:t1] slicing."""
    times, arrays = synthetic_signal
    seen_index = {}

    class _CheckIndex(PandasOfflineAnalysis):
        id = "test.check_index"
        display_name = "check index"
        inputs = [OfflineInput("s", default_channel="raw.x")]
        outputs = [OfflineOutput("passthrough")]

        def run(self, df):
            seen_index["name"] = df.index.name
            seen_index["first"] = float(df.index[0])
            seen_index["last"] = float(df.index[-1])
            return {"passthrough": np.asarray(df["s"])}

    reg = OfflineRegistry()
    reg.register_class(_CheckIndex)
    reg.enable("test.check_index")
    reg.run_all(times, {"raw.x": arrays["signal"]})

    assert seen_index["name"] == "time_sec"
    assert seen_index["first"] == pytest.approx(0.0)
    assert seen_index["last"] == pytest.approx(times[-1])


# ------------------------------------------------------------------ function flavor


def test_function_flavor_is_discovered_and_runs(tmp_path):
    """Write a real .py file with a module-level `run` + metadata, then
    discover-and-run it end-to-end. Proves the whole function-based
    path works, not just the adapter in isolation."""
    script_path = tmp_path / "func_flavor.py"
    script_path.write_text(
        "import pandas as pd\n"
        "from scipy.signal import find_peaks\n"
        "\n"
        "ID = 'test.func_peak_count'\n"
        "DISPLAY_NAME = 'func peak count'\n"
        "INPUTS = {'signal': 'raw.x'}\n"
        "OUTPUTS = ['peak_index', 'peak_value']\n"
        "\n"
        "def run(df):\n"
        "    peaks, _ = find_peaks(df['signal'], prominence=10)\n"
        "    return pd.DataFrame({\n"
        "        'peak_index': peaks,\n"
        "        'peak_value': df['signal'].iloc[peaks].values,\n"
        "    })\n",
        encoding="utf-8",
    )

    found = discover_offline_analyses(tmp_path, None)
    ids = [d.id for d in found]
    assert "test.func_peak_count" in ids, f"function analysis not discovered: {ids}"

    reg = OfflineRegistry()
    reg.register_classes([d.cls for d in found])
    reg.enable("test.func_peak_count")

    # Same synthetic signal as the class-flavor tests.
    n = 500
    times = np.arange(n) / 240.0
    x = 0.001 * np.arange(n, dtype=float)
    x[250] = 100.0
    results = reg.run_all(times, {"raw.x": x})

    r = next(r for r in results if r.analysis_id == "test.func_peak_count")
    assert r.ok, r.error
    assert r.flavor == "pandas"                          # default flavor
    assert r.outputs["peak_index"][0] == pytest.approx(250)


def test_function_flavor_numpy_variant(tmp_path):
    """FLAVOR = 'numpy' switches the function signature to (times, arrays)."""
    (tmp_path / "func_numpy.py").write_text(
        "import numpy as np\n"
        "\n"
        "ID = 'test.func_numpy'\n"
        "DISPLAY_NAME = 'func numpy'\n"
        "FLAVOR = 'numpy'\n"
        "INPUTS = {'s': 'raw.x'}\n"
        "OUTPUTS = ['max']\n"
        "\n"
        "def run(times, arrays):\n"
        "    return {'max': np.array([float(arrays['s'].max())])}\n",
        encoding="utf-8",
    )
    found = discover_offline_analyses(tmp_path, None)
    reg = OfflineRegistry()
    reg.register_classes([d.cls for d in found])
    reg.enable("test.func_numpy")

    times = np.arange(10) / 60.0
    x = np.array([1.0, 2.0, 3.0, 42.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    results = reg.run_all(times, {"raw.x": x})
    r = results[0]
    assert r.ok, r.error
    assert r.flavor == "numpy"
    assert r.outputs["max"][0] == pytest.approx(42.0)


# ------------------------------------------------------------------ error isolation


def test_runner_isolates_exceptions(synthetic_signal):
    """A broken analysis produces an error result but does not stop
    subsequent enabled analyses from running."""

    class _Broken(NumpyOfflineAnalysis):
        id = "test.broken"
        display_name = "broken"
        inputs = [OfflineInput("s", default_channel="raw.x")]
        outputs = [OfflineOutput("out")]
        def run(self, times, arrays):
            raise RuntimeError("deliberate failure")

    class _Fine(NumpyOfflineAnalysis):
        id = "test.fine"
        display_name = "fine"
        inputs = [OfflineInput("s", default_channel="raw.x")]
        outputs = [OfflineOutput("out")]
        def run(self, times, arrays):
            return {"out": np.array([1.0])}

    times, arrays = synthetic_signal
    reg = OfflineRegistry()
    reg.register_class(_Broken)
    reg.register_class(_Fine)
    reg.enable("test.broken")
    reg.enable("test.fine")

    results = reg.run_all(times, {"raw.x": arrays["signal"]})
    by_id = {r.analysis_id: r for r in results}
    assert not by_id["test.broken"].ok
    assert "deliberate failure" in by_id["test.broken"].error
    assert by_id["test.fine"].ok
    assert by_id["test.fine"].outputs["out"][0] == pytest.approx(1.0)


def test_runner_passes_zeros_for_missing_input_channel(synthetic_signal):
    """A stale binding — script asks for a channel not present in the
    session — should not crash. The analysis sees an all-zero array so
    the failure surfaces as an obviously-zero output rather than an
    obscure KeyError."""
    class _NeedsMissingChannel(NumpyOfflineAnalysis):
        id = "test.needs_missing"
        display_name = "needs missing"
        inputs = [OfflineInput("s", default_channel="not.in.session")]
        outputs = [OfflineOutput("sum")]
        def run(self, times, arrays):
            return {"sum": np.array([float(arrays["s"].sum())])}

    times, arrays = synthetic_signal
    reg = OfflineRegistry()
    reg.register_class(_NeedsMissingChannel)
    reg.enable("test.needs_missing")

    results = reg.run_all(times, {"raw.x": arrays["signal"]})
    r = results[0]
    assert r.ok, r.error
    assert r.outputs["sum"][0] == pytest.approx(0.0)


# ------------------------------------------------------------------ registry semantics


def test_enable_state_survives_clear_discovered():
    """Rescan (which calls clear_discovered) should preserve the user's
    checkbox state so their picks don't reset every time they Rescan."""
    reg = OfflineRegistry()
    reg.register_class(_NumpyPeakCount)
    reg.enable("test.numpy_peak_count")
    assert reg.is_enabled("test.numpy_peak_count")

    reg.clear_discovered()
    assert reg.is_enabled("test.numpy_peak_count")  # still enabled!

    reg.register_class(_NumpyPeakCount)
    assert "test.numpy_peak_count" in reg.enabled_ids()
