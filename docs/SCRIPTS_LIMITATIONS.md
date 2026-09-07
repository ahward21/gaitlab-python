# Custom analysis scripts — known limitations & future improvements

> Living document. Captures every gotcha that surfaced while shipping the plugin system so we know what to harden before the stakeholder demo. Anything researchers will trip over first is marked **[demo-risk]**.

Last updated: 2026-08-12 (v1 of the plugin system).

## Runtime behaviour

### L1 — Hub-latest sampling aliases impulsive outputs **[demo-risk]**

- **What happens:** `peak_event` returns 1.0 for a single tick and 0.0 otherwise. The Channels tab and the GraphWorkbench sample the hub at ~20 Hz (UI tick) while scripts run at up to 240 Hz. A single-tick impulse can be missed entirely in the UI even though it *was* published.
- **Mitigation shipped:** the reference detector also publishes `stride_count` (monotonic) and `stride_cadence_bpm` (continuous), which cannot be aliased away. The per-script LSL outlet preserves every sample so LabRecorder / Unity see all impulses.
- **Fix later:** either (a) run the UI refresh at the fastest enabled-script rate, or (b) give the hub a per-channel "sticky-N-ticks" mode for impulses so short pulses stay visible for say 3 UI frames.

### L2 — Script auto-disable is silent unless the user is looking at the Scripts tab

- **What happens:** after 5 consecutive `on_sample` raises, the script is disabled and the last error is written into its status line. The Channels tab and any graphs that had its outputs simply stop updating with no visible alarm.
- **Fix later:** surface a status-bar toast when any script auto-disables. Consider also disabling the outlet's LSL stream cleanly (currently it just stops pushing).

### L3 — Slow-tick warning is a boolean, not a metric

- **What happens:** `ScriptStatus.is_slow` flips true whenever a single tick exceeds half the budget. No histogram, no rolling p95.
- **Fix later:** keep a small ring buffer of the last N tick durations; expose p50/p95/p99 in the status line and a warning threshold users can tune.

### L4 — Timestamps come from `time.time()`, not LSL's clock

- **What happens:** `on_sample(ts, values)` receives wall-clock time. The per-script LSL outlet uses pylsl's default (also LSL clock — but the `ts` seen by the script is *wall-clock*). If a downstream tool correlates the output stream against `XsensMVN` in LSL time, small skew (~ms) between the two clocks can cause micro-desynchronisation.
- **Fix later:** pass `pylsl.local_clock()` as `ts` when pylsl is available; fall back to `time.time()` otherwise. Also allow the outlet to push explicit timestamps.

### L5 — Inputs are read at script tick, not at LSL sample time **[demo-risk]**

- **What happens:** if a script runs at 60 Hz but the input stream produces at 100 Hz, the script samples the hub-latest and drops any intermediate values. Peak detection on a fast source at a slow rate misses peaks.
- **Fix later:** offer a "sample-driven" mode that hooks `hub.subscribe(listener)` on the bound input channels and calls `on_sample` on every publish. Would need per-input triggering rules ("wait for all bound inputs to have updated once since last tick" vs "on any").

### L6 — Empty-tick silently reads 0.0

- **What happens:** if a bound input has never been published (e.g. bridge not started yet), `hub.get_or_default` returns 0.0. The script processes garbage without complaining.
- **Fix later:** track a "seeded but never touched" flag in the hub; skip a tick (or emit a status warning) if any bound input hasn't seen a real sample yet.

## Persistence & session management

### L7 — Enabled scripts and bindings are not persisted

- **What happens:** every app launch starts with all scripts disabled and inputs at their `default_channel`. A researcher who wants "peak detector on RightFoot + trunk sway on Pelvis" set up for every session has to click through it every time.
- **Fix later:** save enabled-script config to `data/profiles/scripts_last.json` on Disable and on shutdown; auto-restore on next launch. Mirror the existing `MetricProfile` / `GraphProfile` pattern.

### L8 — CSV recording columns are frozen at Start **[demo-risk]**

- **What happens:** if the researcher enables a script *after* pressing Record, the script's outputs don't get columns and are silently absent from the CSV. Data loss with no warning.
- **Mitigation shipped:** the README warns "Enable scripts BEFORE starting a recording."
- **Fix later:** either (a) surface a bright warning if any hub channels appear after Record starts, or (b) support append-mode columns (schema change — must be a deliberate decision).

### L9 — No hot-reload; edits to a script file need Rescan + re-Enable

- **What happens:** iterating on a script means saving the file, clicking Rescan, re-selecting the script, re-Enable. Not painful for demos but slow for development.
- **Fix later:** watch the scripts folders with a modest polling loop and auto-Rescan when mtimes change. Keep the previously-enabled script enabled with the same bindings across a hot-reload.

## Script API

### L10 — Detection lag is inherent but not reported to the user

- **What happens:** the reference peak detector uses a 7-sample window (half_window=3) so the reported "peak now" is always ~50 ms after the physical peak (at 60 Hz). Fine for cadence, but if any downstream analysis assumes real-time it will be systematically 50 ms late.
- **Fix later:** add an optional `report_lag_ms` output to the base class or the reference script so consumers can back-correct. Document the lag in the panel's status line.

### L11 — No plateau handling in the reference detector

- **What happens:** two adjacent samples with the exact same maximum value would both satisfy the ≥ comparison but neither is > the other. Currently guarded by the refractory period, but on quantised sources (integer-valued or heavily filtered signals) this can cause a miss or a doubled count near the peak.
- **Fix later:** either detect the plateau explicitly and mark the centre, or ship a `scipy.signal.find_peaks`-based advanced detector (see L12).

### L12 — Reference detector is intentionally simple; no scipy option **[demo-risk for researchers]**

- **What happens:** biomechanics researchers will ask "which detector?" and "did you use `find_peaks`?" — the 7-sample hand-roll is a defensible choice (no extra deps) but they'll want the option.
- **Fix later:** ship a second reference script `gait_peak_detector_scipy.py` that uses `scipy.signal.find_peaks(prominence=..., distance=..., width=...)` on a rolling buffer. Guarded import; graceful message if scipy is missing.

### L13 — Chained scripts have no declared ordering

- **What happens:** a script can read another script's outputs from the hub (they're just channel IDs), so pipelines like "peak detector → cadence smoother → variability" already work. But there's no execution-order guarantee: if two scripts run at 60 Hz on independent threads, the downstream script may read a hub value one tick stale on any given cycle.
- **Fix later:** either accept the 1-tick lag (usually fine) or introduce an explicit "depends_on" list in the API and topologically sort into shared threads.

### L14 — No test hook / no offline replay

- **What happens:** researchers can't feed a saved XDF into a script and see what it would have produced. All validation is live-only.
- **Fix later:** small CLI: `gaitlab replay <path.xdf> --script gait.peak_detector` that reads the XDF, feeds samples into the script at their recorded timestamps, and writes the outputs to a CSV. Useful for reproducibility and for regression-testing detector tweaks against a golden trial.

## UI

### L15 — Channels combo in Scripts panel only appends new channels; never removes stale ones

- **What happens:** if a stream disconnects, its channels stay in the input dropdown. Clicking one just returns 0.0 forever.
- **Fix later:** on tick refresh, prune combo items whose ids are no longer in `hub.channel_ids()`.

### L16 — No way to inspect a script's live inputs

- **What happens:** the panel shows outputs (channel ids + LSL name) but not what the currently-bound input values are at this moment. If a script silently misbehaves the researcher has to hop to the Channels tab to check.
- **Fix later:** add a compact "current input values" strip under the Bindings group, refreshed on tick.

### L17 — Scripts panel doesn't yet expose `min_interval_s`, `prominence`, or any per-script parameters **[demo-risk]**

- **What happens:** the reference peak detector's thresholds are class attributes. Tuning them requires editing the .py file and Rescanning.
- **Fix later:** add an optional `parameters: list[ScriptParameter]` class attribute (like inputs/outputs) that the panel renders as spinners. Each parameter has a name, type (float/int/bool), default, min/max. Values are passed into `setup(params)`.

## Cross-cutting

### L18 — Discovery imports arbitrary Python **[security]**

- **What happens:** whitelisted-folder discovery means we `importlib` any `.py` file the user drops in. Same trust model as running any Python locally, but if a lab shares scripts via network drive, a malicious file could execute at startup.
- **Fix later:** at minimum, add a startup log line listing every script that was imported and its source path. Consider a "trusted authors" allowlist for lab-shared repos.

### L19 — mvn_lsl_bridge and gaitlab UDP:9763 collision (pre-existing)

- Not new to the scripts feature. See `memory/technical_decisions.md` — the fix is to teach the gaitlab Xsens tab to consume the LSL `XsensMVN` stream instead of parsing UDP directly.

## Priority for a stakeholder-perfect demo

If we have limited time before showing this to Dees or the team, address in this order:

1. **L12** — ship the scipy-based detector so the answer to "did you use find_peaks?" is yes.
2. **L8** — either warn or fix append-mode CSV columns; silent data loss is the worst failure mode.
3. **L17** — expose detector parameters in the panel so we can retune live during a demo.
4. **L7** — persist enabled scripts across restarts so the researcher doesn't set up from scratch every session.
5. **L1** — sticky impulses in the UI so `peak_event` visibly flashes.

L2 / L3 / L14 / L18 are nice-to-haves.
