# GaitLab — Python LSL / UDP light lab

Standalone desktop app for raw sensor streams, custom metric formulas, optional live graphs, and CSV recording.

**This package does not modify or depend on the Unity project.** Keep it outside `Assets/`.

## What v1 includes

| Feature | Status |
|---------|--------|
| Discover / connect LSL streams (`pylsl`) | Yes — editable name, type, resolve timeout |
| Xsens MVN **UDP** listen (configurable port + fallbacks) | Yes — raw `xsens.segNN.x/y/z` |
| Map LSL channels → hub ids | Yes |
| In-memory data hub + CSV record | Yes (main workflow) |
| Custom metrics (`+ - * /`, `y = …`) | Yes — clearer letter↔channel bind |
| Optional graphs (time series ≤6 series, or XY) | Yes — off by default |
| Avatar / gait event derivation | No (Unity) |

## Install

```bash
cd python-gait-lab
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

On Windows, ensure [liblsl](https://github.com/sccn/liblsl/releases) is available for `pylsl`.

## Run

```bash
gaitlab
# or
python -m gaitlab
```

List LSL streams:

```bash
python -m gaitlab.ui.app list-streams
```

## Typical workflow

1. **Connections**
   - **LSL** — Refresh, set Name/Type (or preset), Connect. Hover fields for help.
   - **Xsens UDP** — set primary port (default **9763**, same as Unity/MVN), optional fallbacks `9764,9765,9766`, Start listening.
2. **Channels** — live hub values (raw results).
3. **Record** — CSV under `~/GaitLabCaptures/`.
4. **Metrics** (optional) — formula → Read letters → select letter → pick channel → Bind → Apply.
5. **Show graphs** (optional) — Time series (≤6 channels) or XY (X channel vs Y channel).

## Profiles & mappings

- Stream maps: `data/mappings/`
- Metric / graph JSON: `data/profiles/`

## Tests

```bash
pytest
```
