
# GaitLab — Python LSL / UDP light lab

Standalone desktop app for raw sensor streams, custom metric formulas, optional live graphs, and CSV recording.

**This package does not modify or depend on the Unity project.** Keep it outside `Assets/`.

## Features

| Feature | Status |
|---------|--------|
| LSL streams (name/type/timeout) | Yes |
| Xsens MVN UDP (port + fallbacks) | Yes — raw `xsens.segNN.x/y/z` |
| Participant + session metadata | Yes — CSV header + filename |
| Live hub + CSV record | Yes |
| Custom metrics (`+ - * /`, `^`, `sqrt`, `sq`, …) | Yes |
| Optional graphs (≤6 series or XY) | Yes — off by default |

## Install / run

The GitHub repo is named **`gaitlab-python`**. After you download or clone, open a terminal **inside that folder** (not the parent).

**Option A — clone**

```bash
git clone https://github.com/ahward21/gaitlab-python.git
cd gaitlab-python
```

**Option B — ZIP download**

GitHub names the extracted folder `gaitlab-python-master` (or `gaitlab-python-main`). Use that name:

```bash
cd gaitlab-python-master
```

Then (Windows):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
gaitlab
```

macOS / Linux: use `source .venv/bin/activate` instead of `.venv\Scripts\activate`.

## Typical workflow

1. **Connections** — connect LSL and/or Xsens UDP (set port if needed).
2. **Participant** — ID / session fields, plus **output file name** and **sample rate** (Hz or every N seconds).
3. **Channels** — live channels from connected streams (Xsens → all `xsens.segNN.*` / LSL → all stream channels).
4. **Record** — CSV in `~/GaitLabCaptures/`.
5. **Metrics** (optional) — labeled boxes on the tab:
   - **A** Formula box — type e.g. `y = sqrt(sq(x) + sq(z))`
   - **B** Letters list — press *Find letters*, click `x`
   - **C** Channel dropdown — pick a sensor, press *Bind*
   - **Apply** — metric starts computing into Channels
6. **Show graphs** (optional).

### Formula helpers

`sqrt(x)` `sq(x)` `pow(x,2)` `x^2` `abs(x)` `min(a,b)` `max(a,b)` `round(x)` plus `+ - * /`.

## Tests

```bash
pytest
```
