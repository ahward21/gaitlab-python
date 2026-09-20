# Writing analysis scripts for GaitLab

A guide for researchers who want to add their own real-time analysis to
GaitLab — computing derived signals from live sensor data (or from a
replayed recording), publishing them into the graph, the CSV, and out
over LSL to Unity or LabRecorder.

> **This guide covers *online* scripts only** — the kind that run tick by
> tick as data flows through the hub. For whole-signal offline analysis
> (e.g. `scipy.signal.find_peaks` on a full recording, non-causal
> filtering, gait-cycle segmentation over a complete trial), see the
> Sessions tab's batch mode or the forthcoming offline-script guide.

---

## 1. What a script is

A GaitLab analysis script is one Python file containing one class that
subclasses `AnalysisScript`. When you enable it in the Scripts tab, the
runtime:

1. Instantiates it once, calls `setup()`.
2. Runs a background thread that, at your `default_rate_hz`, reads the
   channels you declared as inputs from the shared `DataHub` and calls
   your `on_sample(ts, values)` with a dict of current values.
3. Publishes whatever your `on_sample` returns back into the hub (so the
   Channels / Graphs / Metrics dropdowns see it) and out over an LSL
   outlet (so Unity / LabRecorder can subscribe).
4. Calls `teardown()` when you disable it.

Same class runs during **live capture** and during **session replay** —
the player just drives your `on_sample` from CSV rows instead of hub
listeners. No changes needed on your side.

---

## 2. Where to put your file

You have two folders. Both are scanned when you press **Rescan** in the
Scripts tab.

| Folder | For | Notes |
|---|---|---|
| `data/scripts/` | Scripts shipped with GaitLab | Don't edit unless you're modifying a reference script |
| `~/GaitLabScripts/` | Your own scripts | Created on first Rescan. Same-`id` scripts here **override** built-ins |

Any `.py` file whose top level defines an `AnalysisScript` subclass with
a non-empty `id` and at least one `outputs` entry gets picked up.

---

## 3. Minimum viable script — copy this and start

Save as `~/GaitLabScripts/my_script.py`:

```python
"""One-line description of what this script does."""

from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput


class MyRollingMean(AnalysisScript):
    # --- discovery metadata (all required) ---
    id = "signal.my_rolling_mean"        # dotted, stable, unique across all scripts
    display_name = "My rolling mean"     # shown in the Scripts tab
    default_rate_hz = 60.0               # ticks per second; researcher can override in UI

    # --- what the script reads from the hub ---
    inputs = [
        ScriptInput(
            symbol="signal",                          # what your on_sample sees as values["signal"]
            unit="",
            default_channel="xsens.joint.jRightKnee.z",  # pre-selected in the binding dropdown
            description="Signal to average.",
        ),
    ]

    # --- what the script writes back to the hub ---
    outputs = [
        ScriptOutput(
            symbol="mean_last_1s",
            unit="",
            description="Mean of the last 1 s of samples.",
        ),
    ]

    # --- tunable parameters (edit + Rescan, or subclass and override) ---
    window_seconds: float = 1.0

    def setup(self) -> None:
        """Called once when the script is enabled."""
        from collections import deque
        n = max(2, int(self.window_seconds * self.default_rate_hz))
        self._buf: deque[float] = deque(maxlen=n)

    def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
        """Called every tick. `ts` = seconds since start. `values` = your inputs."""
        v = float(values.get("signal", 0.0))
        self._buf.append(v)
        return {"mean_last_1s": sum(self._buf) / len(self._buf)}

    def teardown(self) -> None:
        """Called once when the script is disabled. Release resources here."""
        self._buf.clear()
```

**That's the whole file.** Press Rescan in the Scripts tab, enable
"My rolling mean", bind `signal` to whichever channel you like, press
Play or open a session. The result appears in the hub as
`script.signal.my_rolling_mean.mean_last_1s`.

---

## 4. The class contract — what each attribute means

### Class attributes (set once, at the top of the class)

| Attribute | Type | Purpose |
|---|---|---|
| `id` | `str` | Stable, unique identifier. Format is convention: `<domain>.<name>` (e.g. `"gait.peak_detector"`, `"signal.lowpass"`). Used as the LSL outlet name and as the prefix for output hub channels (`script.<id>.<symbol>`). **Two scripts with the same id collide** — the last-loaded one wins, which is how user overrides work. |
| `display_name` | `str` | Human-readable name shown in the Scripts panel list. Can contain spaces / punctuation. |
| `default_rate_hz` | `float` | How many times per second `on_sample` is called. Researchers can override in the UI. Typical values: 60 Hz for gait, 240 Hz for high-rate MVN work, 1 Hz for slow signals like HR. |
| `inputs` | `list[ScriptInput]` | Channels you consume from the hub. Order matters — it's the order shown in the binding UI. Empty list means the script reads no bound inputs (it can still pull channels ad-hoc via `self.hub`). |
| `outputs` | `list[ScriptOutput]` | Values you publish back. **Must be non-empty** — a script with no outputs is silently skipped by discovery. |

### Methods (override as needed)

```python
def setup(self) -> None:
    """One-time initialisation. Runs after `self.hub` is injected but
    before the first `on_sample`. Initialise buffers, filter state,
    counters here — anything that needs to reset between enable cycles.
    Exceptions here mark the script as failed-to-start and it is not
    driven further."""

def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
    """Runs on every tick.

    ts:     seconds since capture start (live) or since the recording's
            t=0 (replay). Monotonically increasing within a session.
    values: dict of {input.symbol: current_hub_value}. Missing inputs
            resolve to 0.0.

    Return: dict of {output.symbol: float}. Missing keys are
            legitimate — that output just isn't published this tick
            (previous value stays in the hub). Non-float values
            (strings, None) are silently dropped.

    Exceptions here are caught and logged. If your script raises 5
    ticks in a row, it auto-disables."""

def teardown(self) -> None:
    """Called when the script is disabled (Scripts tab OR session
    unload). Release resources — close files, drop large buffers.
    Exceptions here are logged and swallowed."""
```

### Runtime attributes injected by the framework

Set on your instance *before* `setup()` runs:

| Attribute | Type | Use |
|---|---|---|
| `self.hub` | `DataHub` | Read any hub channel by id: `self.hub.get_or_default("xsens.seg17.y")`. Useful when your script depends on channels you didn't declare as inputs (e.g. reading all 23 segment quaternions to compute joint angles) — declaring 90 inputs would clutter the binding UI. |

---

## 5. Hub channel names — what's available to bind

The `default_channel` you specify appears in the binding dropdown but
the researcher can pick any channel. Common families:

| Channel pattern | Source | Unit | Example |
|---|---|---|---|
| `xsens.seg<NN>.x/.y/.z` | Segment position (23 segments, 00–22) | m | `xsens.seg17.y` — right-foot elevation |
| `xsens.seg<NN>.qw/.qx/.qy/.qz` | Segment orientation quaternion | — | `xsens.seg22.qw` — left-foot orientation |
| `xsens.joint.<label>.x/.y/.z` | Joint angle (22 named joints) | deg | `xsens.joint.jRightKnee.z` — right-knee flexion |
| `GarminHRM.HeartRate` | Heart rate | BPM | Live from HRM bridge |
| `GarminHRM.RR_Interval` | R-R interval | ms | Live from HRM bridge |
| `script.<id>.<output>` | Another script's output | varies | `script.signal.lowpass.filtered` |

Full label list for `xsens.joint.<label>`: `jL5S1, jL4L3, jL1T12, jT9T8,
jT1C7, jC1Head, jRightT4Shoulder, jRightShoulder, jRightElbow,
jRightWrist, jLeftT4Shoulder, jLeftShoulder, jLeftElbow, jLeftWrist,
jRightHip, jRightKnee, jRightAnkle, jRightBallFoot, jLeftHip, jLeftKnee,
jLeftAnkle, jLeftBallFoot`.

---

## 6. A worked example — reading `data/scripts/scipy_peak_detector.py`

Open that file alongside this section. It's ~110 lines and demonstrates:

- **Class-attribute tunables** (`window_seconds`, `prominence`,
  `distance_samples`, `min_interval_s`) that a researcher can edit and
  Rescan without touching the algorithm.
- **A rolling buffer** implemented with `collections.deque(maxlen=n)` —
  the idiomatic pattern for bounded history.
- **Guarding on buffer length** (`if len(self._buf) < 8: return out`) —
  do nothing until you have enough context, but always return the
  full-shaped output dict so the hub sees stable columns.
- **Reporting only "new" events** using a `_last_reported_peak_t`
  attribute so the same peak isn't re-emitted every tick after it
  enters the window.
- **Sanity gates** (`min_interval_s`, `max_interval_s`) as a domain-
  aware post-filter — biomechanical priors that keep noise from
  producing garbage outputs.

Read it. If you can follow it, you can write a script.

---

## 7. Testing your script

The test file `tests/test_scripts.py` shows the pattern. In short:

```python
import pytest
from pathlib import Path
from gaitlab.hub import DataHub
from gaitlab.scripts.runner import ScriptRegistry, ScriptBinding
from gaitlab.session.player import SessionPlayer
from my_module import MyRollingMean   # your class

def test_my_rolling_mean_averages_correctly(tmp_path):
    # 1. Write a small synthetic session
    csv_path = tmp_path / "syn.csv"
    csv_path.write_text(
        "# GaitLab CSV session\n# sample_mode=hz\n# sample_hz=60\n"
        "time_sec,raw.x\n" +
        "\n".join(f"{i/60:.4f},{1.0 if i < 60 else 2.0}" for i in range(120))
    )

    # 2. Set up hub + registry + player
    hub = DataHub()
    scripts = ScriptRegistry(hub)
    scripts.register_class(MyRollingMean)
    scripts.enable("signal.my_rolling_mean",
                   [ScriptBinding("signal", "raw.x")], publish_lsl=False)
    player = SessionPlayer(hub, scripts)
    player.load(csv_path)

    # 3. Drive the whole file, check the final value
    result = player.compute_full_session()
    out = result.output_series["script.signal.my_rolling_mean.mean_last_1s"]
    assert out[-1] == pytest.approx(2.0)  # last second was all 2.0

    scripts.disable_all()
```

Run: `py -3.10 -m pytest tests/test_my_script.py -v`

**Test at least one thing** — the pattern above takes 15 minutes to adapt
and pays back the first time you tweak a parameter and want to know if
you broke something.

---

## 8. Common patterns

### Reading a channel you didn't declare as an input

```python
def on_sample(self, ts, values):
    # Bound inputs come through `values`.
    knee = float(values.get("signal", 0.0))
    # Ad-hoc reads go through the hub.
    hip = self.hub.get_or_default("xsens.joint.jRightHip.z")
    return {"knee_over_hip": knee - hip}
```

### Emitting only some outputs some ticks

```python
def on_sample(self, ts, values):
    self._buf.append(float(values["signal"]))
    if len(self._buf) < 60:
        return {}                        # nothing published — previous values stay
    if ts - self._last_report_ts < 1.0:
        return {"passthrough": self._buf[-1]}  # only passthrough this tick
    self._last_report_ts = ts
    return {"passthrough": self._buf[-1], "mean_1s": sum(self._buf) / len(self._buf)}
```

### Filter state across ticks

```python
def setup(self):
    from scipy.signal import butter
    self._b, self._a = butter(4, 5.0 / (self.default_rate_hz / 2), btype="low")
    self._zi = None   # filter state — persists across ticks
```

Store the SciPy filter's internal state (`zi`) so each `lfilter` call
picks up where the last one left off.

### Consuming another script's output

```python
inputs = [ScriptInput("mean", default_channel="script.signal.my_rolling_mean.mean_last_1s")]
```

Chaining works as long as the upstream script's rate is ≥ downstream's.

---

## 9. What NOT to do in a script

- **No blocking I/O.** No `time.sleep`, no `requests.get`, no file writes.
  Your `on_sample` runs on the runtime's tick thread — if you block, the
  next tick misses its deadline.
- **No unbounded memory.** A `list.append` in `on_sample` grows forever
  during a live session. Use `deque(maxlen=n)` or reset in `setup`.
- **No mutating `values` in place.** Treat it as read-only.
- **No importing PyQt / Qt.** Scripts run on background threads — Qt
  APIs will crash. Publish to the hub; the UI reads from there.
- **No assuming `default_rate_hz` matches the tick rate.** The researcher
  can override it in the UI. Use `self.rate_hz` (the runtime injects
  it) if you need the actual rate — but usually your algorithm should
  work regardless.

---

## 10. When your script fails at runtime

- **After 5 consecutive `on_sample` exceptions**, the script auto-disables
  itself. The last exception is visible in the Scripts tab as
  `last_error`. This exists to stop a runaway user script from flooding
  the log or blocking the runtime.
- **The `is_slow` flag** flips when `on_sample` takes more than half its
  tick budget. Not fatal, but it means you're pushing your rate too
  high or doing too much per tick — profile with
  `time.perf_counter()` around the hot part.
- **If setup fails**, the script never starts. Check the log; the error
  is written verbatim.

---

## 11. Where things go beyond this

For anything that needs the *whole* recording at once — peak counts
across a complete trial, FFT-based fatigue indices, cycle-normalised
gait curves, model inference over a full time series — the online
per-tick model is the wrong shape. See the Sessions tab's batch button
and the offline-script guide (WIP) for the pattern that fits.

If your algorithm is causal but non-trivial (long-horizon EMA, model
inference on a rolling window), the online pattern still works — bump
`default_rate_hz` down and buffer generously.

---

**Quickstart summary:** copy §3, save to `~/GaitLabScripts/`, Rescan,
enable, bind, run. If you want to share it, drop it into `data/scripts/`
and commit.
