# Session Replay Guide

Replay a previously-recorded GaitLab session, apply any script to it after the
fact, and save the annotated result as a new CSV.

## The core promise

**Seeking to any point in a recording produces exactly the same script output
as playing straight from the start to that point.** If you rewind to minute 3
and then to minute 5, the values you see at minute 5 are identical to what you
would have seen at minute 5 if you had never rewound. This is enforced by
resetting every enabled script and replaying from `t=0` on every seek.

The consequence: **long seeks take a few seconds** on long recordings, because
we drive every intermediate sample through your scripts. That is the price of
reproducibility and is the intended behaviour.

## Workflow

1. **Open the Sessions tab.**
2. **Open recording…** — pick a CSV from `~/GaitLabCaptures/` (or anywhere).
   The session's metadata (name, start time, duration, sample rate, channel
   count) appears; all channels are seeded into the hub at `0.0` so the
   Channels tab and script combos see them immediately.
3. **Enable scripts** — go to the **Scripts** tab and enable whichever
   analyses you want to run against the recording. Bind their inputs to the
   session's channels (they're listed just like live channels).
4. **Press Play.** The player owns the clock during replay: every enabled
   script's timer thread is paused, and the player drives each script
   synchronously in row order. This is what makes replay bit-identical across
   speeds.
5. **Change speed** (1× / 2× / 5× / 10× / MAX) as needed. Only wall-clock
   pacing changes — the samples your scripts see are identical.
6. **Seek** by clicking anywhere on the timeline. The UI shows *Seeking…*
   while it replays from the start to the target row. On a 5-minute recording
   at 60 Hz driving a handful of scripts, expect ~1–2 seconds.
7. **Close** to unload the session and hand scripts back to their timer
   threads (i.e. resume live-mode ticking).

## Save a derived session

The **Save derived session** panel records every hub channel — including the
outputs of any scripts you've enabled — into a new CSV.

- Type an output name (default: `<source_stem>_derived`).
- Tick **Record derived session on next Play**.
- Press **Play**. The recording starts alongside playback.
- **The recording is one-shot from the cursor forward.** Any seek stops the
  recording. Pause or Stop also stop it. This is deliberate: the file's time
  axis must stay monotonic to be useful, and seek + record would produce a
  file that skips around in virtual time.
- To record a different segment or a different script configuration, seek to
  the desired start position, tick **Record derived session on next Play**
  again, and press Play.

## The Dees "minute-baseline / minute-ratio" example

Ships as `signal.baseline_ratio` in `data/scripts/baseline_ratio.py`.

1. Open the runner's recording.
2. In the **Scripts** tab, select **Baseline window ratio**. Bind `signal` to
   the channel you care about (e.g. `xsens.seg17.y` for right-foot vertical
   position, or an acceleration channel if you're computing one). Leave both
   `baseline_seconds` and `window_seconds` at 60. Enable.
3. In the **Sessions** tab, tick **Record derived session on next Play**,
   name it `<runner>_baseline_ratio`, press Play, speed MAX.
4. When the file finishes, open the derived CSV. Look at columns
   `script.signal.baseline_ratio.baseline_mean` (constant after minute 1),
   `.window_mean` (updates once per minute), `.ratio` (updates once per
   minute; 1.0 = matches baseline, >1 = above baseline).
5. To compare two runners, load each recording in turn and repeat — you'll get
   one derived CSV per runner, easy to overlay in Excel or a notebook.

## Sizing / performance notes

- **Load time** is one full pass through the CSV: ~1 s per 100k rows on a
  laptop. All rows land in memory.
- **Playback at 1×** is trivial for typical recordings (60 Hz × 5 min = 18k
  rows).
- **Playback at MAX** with several enabled scripts is CPU-bound by the
  scripts; expect several thousand rows/second per script.
- **Seek time** is proportional to `target_row × enabled_scripts`. For 5 min
  of 60 Hz data driving 3 scripts, expect ~1 s per seek. For 30 min, expect
  ~5 s. If this becomes a pain point, we'll add periodic script-state
  snapshots — not implemented in v1 because determinism was the priority.

## What replay is NOT

- **Not a batch analyzer.** One session at a time, interactively. If you want
  to process 50 recordings unattended, we'll add a headless CLI later.
- **Not a data editor.** No trimming, no marker annotations. Seek + save-
  derived-from-here is the closest thing today.
- **Not a live/recording interleaving.** Loading a session pauses every
  enabled script from live ticking. Unload the session (or close the tab)
  before you expect scripts to run against live data again.
