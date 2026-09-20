# Custom analysis scripts

GaitLab discovers Python files here and in `~/GaitLabScripts/` at startup. Any subclass of `AnalysisScript` becomes selectable in the **Scripts** tab.

## Where to put your script

- **Ship with the app:** `data/scripts/<name>.py` (this folder). Good for reference examples that live with the code.
- **Your personal folder:** `~/GaitLabScripts/<name>.py` (Windows: `C:\Users\<you>\GaitLabScripts\`). Survives package upgrades. **User copies override package copies with the same `id`.**

Files starting with `_` are ignored (use for shared helpers).

## Minimum viable script

```python
from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput

class Doubler(AnalysisScript):
    id = "example.doubler"                 # unique across all scripts
    display_name = "Doubler (example)"
    default_rate_hz = 60.0

    inputs = [ScriptInput("x", default_channel="xsens.seg00.z")]
    outputs = [ScriptOutput("y", unit="m")]

    def on_sample(self, ts, values):
        return {"y": 2.0 * values["x"]}
```

Save as `~/GaitLabScripts/doubler.py`, restart GaitLab, open the **Scripts** tab. `script.example.doubler.y` will show up in the Channels list once you Enable it.

## The API (`gaitlab.scripts.api`)

- `AnalysisScript` — base class. Override the class attributes and the three lifecycle methods you need.
- `ScriptInput(symbol, unit="", default_channel="", description="")` — a hub channel the script consumes. `symbol` is what your `on_sample` sees inside `values`; `default_channel` pre-fills the picker in the UI.
- `ScriptOutput(symbol, unit="", description="")` — a value the script publishes. Hub channel id is auto-derived: `script.<your_id>.<symbol>`. LSL outlet name: `GaitLabScript_<your_id>` (one channel per output, in declaration order).

Lifecycle:
- `setup(self)` — called once when the researcher clicks **Enable**. Initialise buffers, filter state, counters.
- `on_sample(self, ts, values)` — called every tick at `default_rate_hz` (or whatever rate the user set). Return a `dict[str, float]` of any subset of your declared outputs. Missing keys are simply not published on this tick — every full sample still lands in the LSL outlet using last-known values (so LabRecorder XDF stays gap-free).
- `teardown(self)` — called on **Disable**. Release anything you allocated.

## Reference implementations

- [`gait_peak_detector.py`](gait_peak_detector.py) — 3-sample local-maximum peak finder with prominence + refractory gates. Emits `peak_event`, `stride_cadence_bpm`, `stride_count`, `signal_value`. Read this before writing your own.

## Rules to save yourself time

1. **Give your script a stable `id`.** Renaming it after recordings exist means old CSVs / XDFs reference channel ids that no longer resolve. Use dotted namespaces (`gait.peak_detector`, `physio.hrv_rmssd`).
2. **Emit continuous features alongside impulses.** The Channels list samples the hub at ~20 Hz (UI tick). If your only output is a 1-tick impulse it may not be visible on graphs — publish a monotonic count or a running gauge in parallel. The per-script LSL outlet preserves every sample regardless.
3. **Keep `on_sample` fast.** Aim for well under `1 / rate_hz` seconds. GaitLab logs a `is_slow` warning if a tick exceeds half the budget. If your algorithm needs a sliding window, use `collections.deque(maxlen=N)` in `setup`.
4. **Don't touch anything outside `values` and your own state.** No network calls, no filesystem writes from `on_sample`. This is user's-machine trust, not a sandbox — misbehaving scripts can crash the app or leak resources.
5. **A script that raises 5 times in a row is auto-disabled** with the error surfaced in the panel status. Fix the code and click Enable again.
