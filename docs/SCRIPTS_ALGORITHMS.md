# GaitLab reference scripts — algorithms, libraries, and citations

> Answers to "how did you detect that?" and "what did you use?" for every reference script that ships with GaitLab. Each entry lists the method name, the primary citation, the Python library invoked, the tunable parameters, and the honest limitations.

Last updated: 2026-08-17.

---

## `signal.baseline_ratio` — First-window baseline vs subsequent-window ratio

**Purpose.** Answers "take the first minute as a baseline; for every minute
after that, report the average and its ratio to baseline." Generalises to any
1D channel and any window length.

**Parameters (class attributes; edit in the file or subclass to override):**

| Name | Default | Meaning |
|---|---|---|
| `baseline_seconds` | 60.0 | Length of the baseline window in seconds from t=0. |
| `window_seconds` | 60.0 | Length of every subsequent (non-overlapping) window. |

**Outputs.**

| Symbol | Meaning |
|---|---|
| `baseline_mean` | Mean of `signal` across the first `baseline_seconds`. Frozen once the baseline closes; `0.0` beforehand. |
| `window_mean` | Mean of the most recently *closed* window. |
| `ratio` | `window_mean / baseline_mean`. `1.0` = matches baseline. |
| `window_index` | `0` during baseline, `1` for first post-baseline window, etc. |

**Behaviour.** Boundary samples belong to the *new* window, not the closing
one — the sample at exactly `t = baseline_seconds` is the first sample of
window 1. Windows are contiguous and non-overlapping; means are of every
sample seen in the window. Definition is a pure function of the `ts`
argument, so live and replay produce identical output at any speed.

**Not emitted until closed.** A window's mean only appears when the next
boundary is crossed. If a recording ends mid-window, the trailing partial
window is discarded — matching researcher intent that means represent full
windows, not partial ones.

**Citations.** No external citation — this is elementary summary statistics.
The rolling-baseline design (freeze a baseline window, compare subsequent
windows against it) is common in fatigue and adaptation studies; see e.g.
Enoka & Duchateau, *Muscle fatigue: what, why and how it influences muscle
function*, J Physiol 586.1 (2008), for the general framing.

---

## `gait.peak_detector` — hand-rolled reference peak detector

**File:** `data/scripts/gait_peak_detector.py`

**Method.** Local-maximum detection on a rolling 7-sample window (~117 ms at 60 Hz) with a prominence gate and a hard refractory period. Emits one peak per stride when the middle sample of the window is `≥` every neighbour AND `middle − min(window) ≥ prominence`, provided at least `min_interval_s` has elapsed since the previous peak.

**Library.** Pure Python `collections.deque`; **no scientific-python dependency.** Deliberately minimal so the reference works even on an install without SciPy.

**Tunable parameters (class attributes):**
- `half_window = 3` — window is `2·half_window + 1` samples.
- `prominence = 0.01` (metres) — required height above the deeper edge of the window.
- `min_interval_s = 0.25` — refractory period; cadence upper bound is `60 / min_interval_s`.
- `max_interval_s = 2.0` — cadence lower bound is `60 / max_interval_s`.

**Outputs:** `peak_event` (1-tick impulse), `stride_count` (monotonic), `stride_cadence_bpm` (from last inter-peak interval), `signal_value` (passthrough).

**Limitations.**
- Not robust to plateau peaks (constant-value stretches) — refractory helps but doesn't fully solve it.
- Hand-tuned prominence is a scalar; noisy signals may need `scipy_peak_detector` instead.
- Reported peak time is the middle-of-window timestamp → inherent ~50 ms detection lag at 60 Hz.

---

## `signal.scipy_peak_detector` — SciPy `find_peaks`

**File:** `data/scripts/scipy_peak_detector.py`

**Method.** Runs `scipy.signal.find_peaks` on a rolling window every tick and reports each newly-appearing peak once. Prominence, minimum distance, optional width, and cadence sanity bounds are exposed as class attributes.

**Library / citations.**
- **SciPy** — Virtanen, P. et al. (2020). "SciPy 1.0: fundamental algorithms for scientific computing in Python." *Nature Methods* **17**, 261–272. https://doi.org/10.1038/s41592-019-0686-2
- `scipy.signal.find_peaks` implementation follows the topological-prominence definition described in the SciPy reference; equivalent behaviour to MATLAB `findpeaks`.

**Tunable parameters:**
- `window_seconds = 3.0` — rolling buffer length.
- `prominence = 0.02` — required topographic prominence (signal units).
- `distance_samples = 15` — minimum separation between peaks (samples).
- `width_samples = None` — optional width constraint at half-prominence.
- `min_interval_s / max_interval_s` — cadence sanity gate.

**Outputs:** `peak_event`, `peak_value`, `peak_prominence`, `cadence_bpm`, `peak_count`, `signal_value`.

**Use when.** Researcher expects the SciPy answer to "what peak finder?" or wants width-based filtering (rejecting narrow spikes) as well as prominence.

**Limitations.**
- Runs `find_peaks` every tick on a window — O(window · rate) cost. Fine at 60 Hz, 3 s window; consider longer windows carefully at 240 Hz.
- Same detection lag as any local-maximum detector.

---

## `gait.events_zeni` — Zeni (2008) kinematic gait-event detection

**File:** `data/scripts/gait_events_zeni.py`

**Method.** For each foot, computes the anterior-posterior distance from the pelvis:

$$d_R(t) = \text{RightFoot}_x(t) - \text{Pelvis}_x(t)$$

Heel strike (initial contact) is a **local maximum** of `d`; toe off is a **local minimum**. Both are found with `scipy.signal.find_peaks` on a rolling buffer. Stride time = interval between consecutive same-side heel strikes; step time = interval between contralateral heel strikes; stance% = fraction of the stride between HS and the following same-side TO; cadence = 120 / mean(stride_time_R, stride_time_L).

**Library / citation.**
- **Zeni, J. A., Richards, J. G., & Higginson, J. S. (2008).** "Two simple methods for determining gait events during treadmill and overground walking using kinematic data." *Gait & Posture* **27**(4), 710–714. https://doi.org/10.1016/j.gaitpost.2007.07.007

  Our implementation is Method 1 in the paper (the "coordinate" method) — Method 2 is a velocity-based variant. We substitute the MVN Pelvis segment position for the paper's sacral marker; functionally equivalent when the participant walks along a consistent forward axis.

- **SciPy** — as above.

**Inputs.** Three channels: `right_foot_ap`, `left_foot_ap`, `pelvis_ap`. Defaults bound to `xsens.seg17.x / seg21.x / seg00.x` (MVN convention: X is the forward direction).

**Outputs.** Ten: `right_hs_event`, `right_to_event`, `left_hs_event`, `left_to_event`, `right_stride_time_s`, `left_stride_time_s`, `step_time_s`, `cadence_bpm`, `right_stance_pct`, `left_stance_pct`. Plus two monotonic counters (`right_hs_count`, `left_hs_count`) and two overlay traces (`right_d_signal`, `left_d_signal`) so the researcher can plot the underlying signal alongside the detected events.

**Tunable parameters:** `prominence` (default 5 cm, roughly a step length), `distance_samples` (min inter-event spacing), `min_stride_s / max_stride_s` (physiological sanity gates).

**Limitations.**
- Assumes a single, consistent forward axis. On a curved path or a treadmill with drift, the AP channel needs pre-processing (subtract mean velocity or use pelvis-frame coordinates).
- Zeni's method is well-validated for overground and treadmill walking; running and irregular gait may fire spuriously — cross-check with foot-Y events for those cases.
- Toe-off detection is noisier than heel-strike in the original paper; expect ± 1 sample uncertainty.

---

## `biomech.joint_angles` — hip / knee / ankle flexion from MVN quaternions

**File:** `data/scripts/joint_angles.py`

**Method.** For each of the six major lower-limb joints, computes the relative rotation between the parent and child MVN segment quaternions:

$$q_{rel} = q_{parent}^{-1} \cdot q_{child}$$

then extracts the sagittal flexion angle by converting `q_rel` to Euler angles in ZYX (Tait-Bryan) order and taking the Y (pitch) component. Positive = flexion, negative = extension.

**Library / citations.**
- **SciPy** `scipy.spatial.transform.Rotation` for quaternion algebra and Euler decomposition.
- Convention background: **Grood, E. S., & Suntay, W. J. (1983).** "A joint coordinate system for the clinical description of three-dimensional motions: application to the knee." *Journal of Biomechanical Engineering* **105**(2), 136–144. https://doi.org/10.1115/1.3138397

  Grood-Suntay is the gold-standard joint coordinate system for clinical gait analysis. Our Euler-decomposition approach is a *first-order approximation* — for a proper GS decomposition we'd need to define anatomical axes per segment from a calibration pose, which is a v2 enhancement.

- **Movella Xsens MVN User Manual** — segment coordinate system conventions.

**Prerequisite.** "Publish quaternions" must be enabled in the Connections → Xsens UDP tab. The script raises a clear error in setup() if quaternion channels aren't present in the hub.

**Segment mapping (MVN loop indices):**
- right_hip = seg00 → seg15  (Pelvis → RightUpperLeg)
- right_knee = seg15 → seg16
- right_ankle = seg16 → seg17
- left_hip = seg00 → seg19
- left_knee = seg19 → seg20
- left_ankle = seg20 → seg21

**Outputs.** Six flexion angles in degrees: `right_hip_deg`, `right_knee_deg`, `right_ankle_deg`, `left_hip_deg`, `left_knee_deg`, `left_ankle_deg`.

**Limitations.**
- Sagittal flexion axis assumes MVN default segment frames (X forward, Y up, Z lateral). If your MVN configuration uses a different convention, change `FLEXION_EULER_INDEX` at the top of the file (0 = X, 1 = Y, 2 = Z) and Rescan.
- No abduction/adduction or internal/external rotation output yet — only the primary sagittal flexion.
- Euler decomposition suffers gimbal lock near ±90° pitch; not a concern in walking, will matter for high-flexion sports.
- Not calibrated: reports raw segment-frame relative angle, not clinical zero-referenced flexion. A calibration pose (standing anatomical zero) subtraction would be the v2 fix.

---

## `gait.bilateral_symmetry` — Robinson (1987) Symmetry Index

**File:** `data/scripts/bilateral_symmetry.py`

**Method.** Consumes any pair of left/right scalar channels (typically `right_stride_time_s` / `left_stride_time_s` and the two `stance_pct` outputs from the Zeni script) and computes the Robinson Symmetry Index:

$$\text{SI} = 200 \cdot \frac{\left| X_R - X_L \right|}{X_R + X_L} \quad (\%)$$

0 = perfect symmetry. Literature typically flags **SI > 10 %** as clinically meaningful for temporal parameters.

**Library / citations.**
- **Robinson, R. O., Herzog, W., & Nigg, B. M. (1987).** "Use of force platform variables to quantify the effects of chiropractic manipulation on gait symmetry." *Journal of Manipulative and Physiological Therapeutics* **10**(4), 172–176.
- **Sadeghi, H., Allard, P., Prince, F., & Labelle, H. (2000).** "Symmetry and limb dominance in able-bodied gait: a review." *Gait & Posture* **12**(1), 34–45. https://doi.org/10.1016/S0966-6362(00)00070-9 — comprehensive review of symmetry indices in gait research.

**Inputs.** Four channels, defaulted to Zeni's outputs — a chained script.

**Outputs.** `si_stride_time_pct`, `si_stance_pct`, plus passthroughs `stride_time_r` and `stride_time_l` for reference.

**Limitations.**
- SI is a **ratio index** — it can't distinguish "R and L both 10 % high" from "R and L both nominal." Use alongside the raw R and L values, not instead of them.
- Undefined for R + L ≈ 0; we return 0 in that case rather than raising or reporting a spurious large value during warmup.
- Robinson SI is one of several symmetry metrics in the literature (others: Gait Asymmetry, Symmetry Angle, Bilateral Coordination). This script only ships Robinson's; adding others is trivial (subclass and override).

---

## `signal.lowpass` — real-time Butterworth low-pass filter

**File:** `data/scripts/lowpass_filter.py`

**Method.** Causal SOS (second-order-sections) Butterworth low-pass filter with persistent per-tick state (`sosfilt` + `sosfilt_zi`), so the filter operates on a live sample stream without needing the full signal up-front. Filter is initialised at the first sample's DC level to avoid a warmup transient.

**Library / citations.**
- **SciPy** `scipy.signal.butter`, `sosfilt`, `sosfilt_zi`.
- **Winter, D. A. (2009).** "Biomechanics and Motor Control of Human Movement" (4th ed.), Wiley. Chapter 3 recommends 6 Hz low-pass for lower-limb kinematics — that is our default cutoff.

**Tunable parameters:**
- `cutoff_hz = 6.0` (Winter's recommendation for lower-limb kinematics).
- `order = 4`.

**Outputs.** `filtered` (the low-pass output) and `raw` (passthrough) so overlaying the two in a graph is trivial.

**Limitations.**
- **Causal filter → group delay.** A 4th-order Butterworth at 6 Hz introduces roughly 40–60 ms lag at 60 Hz sample rate. If you need zero-lag filtering (e.g. for publication-quality kinematics), record raw and apply `scipy.signal.filtfilt` offline instead.
- `cutoff_hz` is set at script load; changing it requires editing the file and Rescanning. Priority-list item **L17** in `SCRIPTS_LIMITATIONS.md` is exposing script parameters in the panel.
- Not a band-pass or high-pass; those are three-line variants of this file if you need them.

---

## Cross-cutting notes

- **All scripts publish to two channels simultaneously:** the shared `DataHub` (visible in Channels, Metrics, Graphs, and the CSV recorder) *and* a per-script LSL outlet named `GaitLabScript_<id>` (subscribable by Unity or LabRecorder). Impulse outputs may be aliased in the UI but are preserved bit-exact on the LSL outlet.
- **All scripts run in isolated daemon threads** at a user-configurable rate (default 60 Hz). Failures are caught and counted; after 5 consecutive exceptions the script auto-disables — see `SCRIPTS_LIMITATIONS.md` L2.
- **Chained scripts.** Bilateral symmetry consumes Zeni's outputs; joint-angle output could feed a low-pass filter, which could feed a scipy peak detector. There is no explicit ordering guarantee (see L13) but scripts read hub-latest values, so at 60 Hz any chain incurs at most a 1-tick (~17 ms) lag per stage.
- **Missing methods worth adding in v2:**
  * Grood-Suntay joint coordinate system with calibration pose.
  * Panebianco et al. (2018) IMU-native gait event detection — cross-validates Zeni.
  * Lord et al. (2011) Harmonic Ratio for gait smoothness.
  * Numerical differentiation script (for accelerations, since MXTP02 is pose-only).
  * Peak-per-gait-cycle statistics (chained off Zeni's events + a target channel).

---

## Where to point a reviewer

If someone asks *"which library did you use for X?"*:

| X | Answer |
|---|---|
| peak detection | `scipy.signal.find_peaks` (SciPy 1.10+) via the `signal.scipy_peak_detector` script, with a hand-rolled reference in `gait.peak_detector` for zero-dependency operation |
| gait events (heel strike / toe off) | **Zeni et al. (2008)** — coordinate method — implemented on top of `scipy.signal.find_peaks` |
| joint angles | Quaternion relative rotation + Euler decomposition via `scipy.spatial.transform.Rotation`; a v2 will implement **Grood & Suntay (1983)** with a calibration pose |
| symmetry | **Robinson et al. (1987)** Symmetry Index |
| low-pass filter | 4th-order Butterworth (**Winter 2009** recommendation) via `scipy.signal.butter` + `sosfilt` for causal streaming |
| the plugin system itself | Custom (no framework); patterns mirror the existing `gaitlab.metrics.evaluator` — each script is a `threading.Thread` with a `DataHub` publish/subscribe pattern; the API contract is in `gaitlab/scripts/api.py` |
