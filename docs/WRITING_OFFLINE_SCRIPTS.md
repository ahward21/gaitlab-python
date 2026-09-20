# Writing offline analyses for GaitLab

A guide for researchers who want to run whole-signal analyses on a
loaded recording — things like `scipy.signal.find_peaks` over a full
trial, FFT-based fatigue indices, gait-cycle segmentation across the
entire session, or model inference that needs the complete time series
in one shot.

> **This guide is for offline analyses.** For real-time per-tick scripts
> that publish live outputs into the hub / LSL / graph as data streams,
> see `docs/WRITING_SCRIPTS.md`. Same discovery folders, different
> execution model.

---

## 1. When to write an offline analysis vs an online script

| Your algorithm needs… | Use |
|---|---|
| A rolling window of recent samples | Online script |
| Live cadence / event flags during recording | Online script |
| The **full array** at once (e.g. `find_peaks(signal, prominence=40)`) | **Offline analysis** |
| Non-causal filtering (`filtfilt`, forward-backward) | **Offline analysis** |
| Frequency-domain features on a whole trial (FFT, PSD) | **Offline analysis** |
| Cycle-normalised gait curves over a complete recording | **Offline analysis** |
| Batch model inference over a full time series | **Offline analysis** |

If you can express your algorithm as "given the entire recording as one
array, return a set of derived arrays", it's offline.

---

## 2. Three ways to write one

All three land in the same folders (`data/scripts/` for shipped
analyses, `~/GaitLabScripts/` for your own) and appear together in the
Sessions tab's **Offline analyses** checklist. Pick the flavor that
matches your idioms.

### Flavor A — function-based (minimum boilerplate)

```python
# ~/GaitLabScripts/my_analysis.py
import pandas as pd
from scipy.signal import find_peaks

ID = "signal.my_knee_peaks"
DISPLAY_NAME = "My knee peak detector"
INPUTS = {"sagittal": "xsens.joint.jRightKnee.z"}
OUTPUTS = ["peak_index", "peak_value"]

def run(df):
    peaks, _ = find_peaks(df["sagittal"], prominence=40)
    return pd.DataFrame({
        "peak_index": peaks,
        "peak_value": df["sagittal"].iloc[peaks].values,
    })
```

That's the whole file. Metadata is 5 module-level constants; the
research code is one function that receives a time-indexed pandas
DataFrame and returns a DataFrame (or a dict of arrays).

To use the NumPy signature instead, add `FLAVOR = "numpy"` and change
the function to `def run(times, arrays):` — see Flavor B for the
argument shapes.

### Flavor B — class, NumPy

```python
import numpy as np
from scipy.signal import find_peaks
from gaitlab.scripts.offline_api import NumpyOfflineAnalysis, OfflineInput, OfflineOutput

class MyKneePeaks(NumpyOfflineAnalysis):
    id = "signal.my_knee_peaks"
    display_name = "My knee peak detector"
    inputs = [OfflineInput("signal", default_channel="xsens.joint.jRightKnee.z")]
    outputs = [OfflineOutput("peak_index"), OfflineOutput("peak_value")]

    def run(self, times, arrays):
        # times: np.ndarray of session seconds, shape (N,)
        # arrays: dict[str, np.ndarray] — key is the input.symbol you declared
        sig = arrays["signal"]
        peaks, _ = find_peaks(sig, prominence=40)
        return {
            "peak_index": peaks.astype(float),
            "peak_value": sig[peaks],
        }
```

Use when you don't want pandas as a dep on your researcher's script, or
when your algorithm is naturally array-oriented.

### Flavor C — class, pandas

```python
import pandas as pd
from scipy.signal import find_peaks
from gaitlab.scripts.offline_api import PandasOfflineAnalysis, OfflineInput, OfflineOutput

class MyKneePeaks(PandasOfflineAnalysis):
    id = "signal.my_knee_peaks"
    display_name = "My knee peak detector"
    inputs = [OfflineInput("sagittal", default_channel="xsens.joint.jRightKnee.z")]
    outputs = [OfflineOutput("peak_index"), OfflineOutput("peak_value")]

    def run(self, df):
        # df: pandas.DataFrame indexed by time_sec, one column per input.symbol.
        # df.loc["3s":"12s"] style slicing works out of the box.
        peaks, _ = find_peaks(df["sagittal"], prominence=40)
        return pd.DataFrame({
            "peak_index": peaks,
            "peak_value": df["sagittal"].iloc[peaks].values,
        })
```

Use when you want pandas idioms (time-indexed slicing, `.rolling()`,
`.groupby()`, `.resample()`) and are OK depending on pandas.

---

## 3. What the runner passes to your script

### NumPy flavor

- `times: np.ndarray[float64]` — session seconds, monotonic, one per
  sample. Length = number of samples in the loaded recording.
- `arrays: dict[str, np.ndarray[float64]]` — one entry per declared
  input. Key is the input's `symbol` (not the hub channel id). Value
  is a 1D array aligned with `times`.

### pandas flavor

- `df: pandas.DataFrame` — indexed by `time_sec`, one column per input.
  Column name is the input's `symbol`. `df.index[0] == 0.0`,
  `df.index[-1]` is the session duration in seconds.

### Both flavors

If a declared input's `default_channel` isn't present in the loaded
session, you receive an all-zero array of the correct length and a
warning appears in the log. Your `run` does not crash — a stale binding
surfaces as an obviously-zero output rather than an obscure `KeyError`.

---

## 4. What the runner does with your return value

Accepted shapes (all flavors):

- `dict[str, array-like]`
- `pandas.DataFrame`
- `pandas.Series` (single-output case — column name defaults to the
  first `OFFLINE_OUTPUT`)

Every return is normalised to `dict[str, np.ndarray]` before storage.

**Output length is variable** — offline analyses typically return
sparse results (a peak list has ~1000 entries against 240 000 input
samples). Outputs are:

- **Written to their own CSV** next to the batch CSV as
  `<name>_offline_<analysis_id>_<timestamp>.csv` when you click
  **Save offline CSVs** in the batch dialog.
- **Overlaid as scatter markers on the batch graph** *if* your output
  columns follow the `peak_index` / `peak_value` convention. Any two
  columns named exactly `peak_index` and `peak_value` are treated as
  "frame indices + values" and plotted as `×` markers on the main plot.
- **Summarised** in the offline-summary panel of the batch dialog
  (name, flavor, duration, per-output length).

---

## 5. How to enable and run one

1. Save the .py file to `~/GaitLabScripts/` (survives package updates)
   or `data/scripts/` (ships with GaitLab).
2. In the Scripts tab, press **Rescan** — this covers both online
   scripts and offline analyses. The status line reports how many of
   each were found.
3. In the Sessions tab, open a recording (`.csv` or `.mvnx`). The
   **Offline analyses** checklist appears in the Batch analysis group.
4. Check the box next to the analysis you want to run.
5. Click **Compute over entire session**. Online scripts tick through
   as usual; when the batch finishes, enabled offline analyses run once
   against the completed trace.
6. The batch dialog opens: online outputs as time series, offline peaks
   as `×` markers overlaid, offline summary panel below the graph.
   Click **Save offline CSVs** to write per-analysis output files.

---

## 6. Bindings

Offline analyses bind to hub channels via `default_channel` on each
input. Unlike the online Scripts tab, there is no per-session rebinding
UI — the source file is the source of truth. To point an analysis at a
different channel, either:

- Edit the `default_channel` value in the .py file and Rescan, or
- Subclass the analysis in a new file:

```python
from data.scripts.dees_offline_knee_peaks import ...   # if class-based
# for function-based, just copy the file and change INPUTS.
```

This is deliberate — offline analyses are often reused across many
sessions and having a stable, source-controlled binding matters more
than per-session tweakability.

---

## 7. Worked example — Dees's `process_mvnx.py` migrated

Dees's original script is at `data/scripts/process_mvnx.py` (verbatim,
for reference — not executable by the framework). Its GaitLab-native
form is in `data/scripts/dees_offline_knee_peaks.py`:

```python
import pandas as pd
from scipy.signal import find_peaks

ID = "dees.right_knee_sagittal_peaks"
DISPLAY_NAME = "Right knee sagittal peaks (Dees, offline)"
INPUTS = {"sagittal": "xsens.joint.jRightKnee.z"}
OUTPUTS = ["peak_index", "peak_value"]

def run(df):
    sagittal = df["sagittal"]
    peaks, properties = find_peaks(sagittal, prominence=40)
    return pd.DataFrame({
        "peak_index": peaks,
        "peak_value": sagittal.iloc[peaks].values,
    })
```

**Diff vs Dees's original:**

- ✅ `find_peaks(sagittal, prominence=40)` — verbatim, line 15 of `process_mvnx.py`.
- ✅ Output DataFrame with `peak_index` + `peak_value` — same shape, same values.
- ➖ Deleted: hardcoded `.mvnx` path, `MVNXLoader.getDF` call, joint-index lookup, plotly figure, `to_csv` write.
- ➕ Added: 5 metadata constants + a `def run(df):` wrapper.

**Verification against ground truth:** running this against the
converted CSV of Dees's test recording produces **1203 / 1203
zero-offset peak indices**, identical to Dees's own
`_right_knee_sagittal_peaks.csv`.

---

## 8. Testing your offline analysis

Same pattern as online scripts. `tests/test_offline_api.py` has one
example per flavor. In short:

```python
import numpy as np
import pytest
from gaitlab.scripts.offline_runner import OfflineRegistry
from my_module import MyKneePeaks   # or the discovery path for function form

def test_my_knee_peaks_finds_the_spike():
    n = 500
    times = np.arange(n) / 240.0
    x = 0.001 * np.arange(n, dtype=float)
    x[250] = 100.0                          # single big peak

    reg = OfflineRegistry()
    reg.register_class(MyKneePeaks)
    reg.enable("signal.my_knee_peaks")

    results = reg.run_all(times, {"xsens.joint.jRightKnee.z": x})

    r = results[0]
    assert r.ok, r.error
    assert list(r.outputs["peak_index"]) == [250.0]
```

Function-based scripts test the same way but need to be discovered
first — see `test_function_flavor_is_discovered_and_runs` in
`test_offline_api.py` for the pattern (writes a real .py file into
`tmp_path`, calls `discover_offline_analyses`).

---

## 9. Common patterns

### Return only one output

```python
def run(df):
    return {"fatigue_index": np.array([compute_fatigue(df["hr"])])}
```

Length-1 outputs display fine — the batch CSV row has just one value.

### Multiple outputs of different lengths

```python
def run(df):
    peaks, _ = find_peaks(df["signal"], prominence=40)
    strides = compute_stride_times(df["signal"])
    return {
        "peak_index": peaks,           # 1203 values
        "peak_value": df["signal"].iloc[peaks].values,   # 1203 values
        "stride_duration_s": strides,  # ~1200 values
        "mean_stride_hz": np.array([len(peaks) / df.index[-1]]),  # 1 value
    }
```

The offline CSV writer pads shorter columns with blanks so the file is
still valid CSV.

### Read a channel you didn't declare as an input

Not supported. Offline analyses must declare every channel they need in
`INPUTS` (function form) or `inputs` (class form). This is stricter
than online scripts on purpose — offline outputs are cached and shared,
so implicit dependencies would silently break reproducibility.

If you need many channels, declare them all:

```python
INPUTS = {
    "hip":   "xsens.joint.jRightHip.z",
    "knee":  "xsens.joint.jRightKnee.z",
    "ankle": "xsens.joint.jRightAnkle.z",
}
```

### Downsample or resample

```python
# pandas flavor
def run(df):
    # Downsample from 240 Hz to 60 Hz, mean per bin.
    slow = df["signal"].resample(pd.Timedelta("16.667ms")).mean()
    ...
```

Note: pandas resample works when the index is a `TimedeltaIndex` or
`DatetimeIndex`. The runner hands you a plain float index (seconds).
Convert with `df.index = pd.to_timedelta(df.index, unit="s")` at the
top of your run.

---

## 10. What NOT to do

- **No blocking I/O to random paths.** If you want to save something
  yourself, use a full path or `~` — don't rely on a working directory.
  Better: return your outputs and let the framework write the CSV.
- **No plotly / matplotlib show().** Return your arrays; the batch
  dialog draws them.
- **No pandas dependency in NumPy-flavor scripts.** The runner only
  imports pandas when a pandas-flavor script runs — keep the invariant.
- **No unbounded per-call state.** Each batch instantiates your class
  fresh (or calls your function fresh). If you need state across
  batches, cache it externally.

---

## 11. Error behavior

- A `run` that raises produces an error result — visible in the batch
  dialog's summary panel as `✗ <name> — ERROR: <message>`.
- Other enabled analyses still run — one bad script doesn't take down
  the batch.
- The full traceback goes to the app log.

---

## 12. Field reference

### Function-based module-level constants

| Constant | Type | Required | Default | Purpose |
|---|---|---|---|---|
| `ID` | `str` | ✅ | — | Stable, unique dotted id (e.g. `"gait.stride_stats"`). |
| `DISPLAY_NAME` | `str` | | `ID` | Shown in the Sessions tab checklist. |
| `INPUTS` | `dict[str, str]` | | `{}` | Mapping from `symbol` → hub `channel_id`. |
| `OUTPUTS` | `list[str]` | ✅ | — | Output symbol names. |
| `FLAVOR` | `"numpy"` or `"pandas"` | | `"pandas"` | Signature of your `run`. |
| `DESCRIPTION` | `str` | | `""` | Freeform text; shown as the docstring on the discovered class. |

### Class-based attributes

| Attribute | Type | Required | Purpose |
|---|---|---|---|
| `id` | `str` | ✅ | As above. |
| `display_name` | `str` | | As above. |
| `inputs` | `list[OfflineInput]` | | Ordered declaration; `OfflineInput(symbol, default_channel, ...)`. |
| `outputs` | `list[OfflineOutput]` | ✅ | Ordered; `OfflineOutput(symbol, ...)`. |
| `flavor` | `"numpy"` \| `"pandas"` | | Set by the base class you inherit from. |

---

**Quickstart summary**: copy §2 Flavor A, save to `~/GaitLabScripts/`,
Rescan, tick the box in the Sessions tab's Offline analyses list, click
Compute. Your algorithm runs once against the full loaded recording.
