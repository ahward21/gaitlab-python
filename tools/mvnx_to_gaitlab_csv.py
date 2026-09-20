"""Stream an Xsens .mvnx recording into a GaitLab-format CSV.

Why this exists: a researcher who arrives with a pre-existing MVN recording
would otherwise have to open MVN Analyze, enable the Network Streamer, and
replay the file end-to-end in real time (~17 min for the test dataset) just
to capture it into GaitLab. This converter reads the .mvnx XML directly —
no MVN Analyze, no realtime replay — and emits a CSV the Sessions panel
can open like any other GaitLab recording.

Design contract:

* **Streaming only.** ``lxml.iterparse`` walks the file in one pass; the
  full DOM is never materialised. RAM stays flat regardless of recording
  length (a 2 GB .mvnx converts in a few hundred MB).
* **No unwrapping, no smoothing.** Values are written exactly as the .mvnx
  stores them. Any post-processing (discontinuity removal, filtering, unit
  conversion) is a downstream concern.
* **Skip calibration frames.** MVN prepends `type="identity"`, `tpose`, and
  `npose` frames before the real capture starts. Their `time="0"` and
  `index="0"` collide with the first real frame. This converter drops
  every non-`normal` frame so ``time_sec`` starts at 0 on the first
  captured sample — matching how ``process_mvnx.py`` (Dees's reference
  script) indexes the data and therefore how the ground-truth peaks CSV
  is aligned.
* **Channel names mirror the live UDP bridge.** Joint angles land in
  ``xsens.joint.<label>.{x,y,z}`` (degrees) and segment positions in
  ``xsens.seg<NN>.{x,y,z}`` (metres) — same ids ``gaitlab.xsens.udp_source``
  publishes. Every script bound to a live channel works against the
  converted file with no re-binding.
* **Header carries provenance.** The CSV comment block records the source
  path, MVN version (if declared in the file), sample rate, calibration-
  frame count, and normal-frame count, so a researcher opening the file
  later can trace it back to the .mvnx.

Usage as a CLI::

    py -3.10 tools/mvnx_to_gaitlab_csv.py INPUT.mvnx [-o OUTPUT.csv]

Usage from Python (Sessions panel wires this in)::

    from tools.mvnx_to_gaitlab_csv import convert_mvnx_to_csv
    convert_mvnx_to_csv(mvnx_path, csv_path, progress_callback=lambda p: ...)
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from lxml import etree

_NS = "{http://www.xsens.com/mvn/mvnx}"

# MVN prepends these frame types before real capture; index/time on them is
# not aligned with the ground-truth CSVs.
_CALIBRATION_TYPES = frozenset({"identity", "tpose", "tpose-isb", "npose"})


@dataclass(frozen=True)
class ConversionSummary:
    """Result of one .mvnx → .csv conversion. Written into the CSV header."""

    source_path: Path
    output_path: Path
    joint_labels: tuple[str, ...]
    segment_labels: tuple[str, ...]
    calibration_frame_count: int
    normal_frame_count: int
    sample_rate_hz: float
    mvn_version: str


ProgressCallback = Callable[[float], None]


def _local(tag: str) -> str:
    """Return the local part of a `{ns}name` XML tag."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_floats(text: str | None) -> list[float]:
    """Parse a whitespace-separated float list. MVN uses space, sometimes \\n."""
    if not text:
        return []
    return [float(x) for x in text.split()]


def _extract_labels(elem: etree._Element) -> list[str]:
    """Read `label` attributes off every direct child of a `<segments>` /
    `<joints>` element. Order is authoritative — MVN's own column order."""
    return [c.get("label", "") for c in elem]


def convert_mvnx_to_csv(
    mvnx_path: Path | str,
    output_path: Path | str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> ConversionSummary:
    """Stream-convert an .mvnx to a GaitLab-format CSV.

    Reads segment + joint definitions from the file header, then iterates
    every ``<frame>`` element, writing one CSV row per ``type="normal"``
    frame. Emits ``xsens.seg<NN>.{x,y,z}`` for position and
    ``xsens.joint.<label>.{x,y,z}`` for jointAngle — exact same channel
    ids the live UDP bridge publishes.

    Blocks the caller. Suitable for CLI use and for wrapping in a Qt
    worker thread or a QProgressDialog loop.

    ``progress_callback`` is called with a value in [0.0, 1.0] roughly
    every 500 frames. It is derived from file byte offset because
    iterparse can't know the total frame count until the end of the file.
    """
    src = Path(mvnx_path)
    dst = Path(output_path)
    if not src.exists():
        raise FileNotFoundError(src)
    file_size = src.stat().st_size

    joint_labels: list[str] = []
    segment_labels: list[str] = []
    sample_rate_hz = 0.0
    mvn_version = ""
    calibration_count = 0
    normal_count = 0

    dst.parent.mkdir(parents=True, exist_ok=True)
    fh_in = src.open("rb")
    fh_out = dst.open("w", newline="", encoding="utf-8")
    try:
        # Two passes conceptually — but only one file traversal. We open
        # the writer lazily after the header has revealed segment / joint
        # counts (needed to build the column list).
        writer: csv.writer | None = None
        columns: list[str] = []

        context = etree.iterparse(
            fh_in,
            events=("start", "end"),
            huge_tree=True,
        )
        for event, elem in context:
            tag = _local(elem.tag)

            if event == "start" and tag == "mvnx":
                mvn_version = elem.get("version", "") or ""

            elif event == "start" and tag == "subject":
                # `frameRate` is an attribute set at element START; reading
                # at END would work too but this is order-agnostic and free.
                fr = elem.get("frameRate")
                if fr:
                    try:
                        sample_rate_hz = float(fr)
                    except ValueError:
                        sample_rate_hz = 0.0

            elif event == "end" and tag == "segments":
                # `<segments>` fires end BEFORE the first `<frame>` end event
                # in a well-formed MVNX (header precedes motion data), so by
                # the time a frame lands we already know how many columns.
                segment_labels = _extract_labels(elem)
                elem.clear()

            elif event == "end" and tag == "joints":
                joint_labels = _extract_labels(elem)
                elem.clear()

            elif event == "end" and tag == "frame":
                ftype = elem.get("type", "normal")
                if ftype in _CALIBRATION_TYPES:
                    calibration_count += 1
                    elem.clear()
                    continue

                if writer is None:
                    # First real frame — build header now that we know the
                    # segment + joint counts. If the .mvnx omitted the
                    # header (rare), fall back to the width of this frame's
                    # position/jointAngle arrays.
                    if not segment_labels:
                        pos_text = elem.findtext(_NS + "position")
                        n_seg = len(_parse_floats(pos_text)) // 3
                        segment_labels = [f"seg{i:02d}" for i in range(n_seg)]
                    if not joint_labels:
                        ja_text = elem.findtext(_NS + "jointAngle")
                        n_joint = len(_parse_floats(ja_text)) // 3
                        joint_labels = [f"j{i:02d}" for i in range(n_joint)]
                    columns = _build_columns(segment_labels, joint_labels)
                    _write_header(
                        fh_out,
                        src=src,
                        columns=columns,
                        sample_rate_hz=sample_rate_hz,
                        mvn_version=mvn_version,
                        segment_labels=segment_labels,
                        joint_labels=joint_labels,
                    )
                    writer = csv.writer(fh_out)
                    writer.writerow(columns)

                _write_frame(
                    writer,
                    elem,
                    segment_labels=segment_labels,
                    joint_labels=joint_labels,
                    frame_zero_time_ms=None,  # first normal frame == time 0
                )
                normal_count += 1
                elem.clear()

                # Reporting by frame count would misrepresent progress on
                # long files where the header is small; use byte offset.
                if progress_callback is not None and normal_count % 500 == 0:
                    try:
                        progress_callback(min(1.0, fh_in.tell() / max(1, file_size)))
                    except Exception:
                        pass

        if writer is None:
            raise RuntimeError(
                f"{src.name} contained no `type=normal` frames — nothing to write."
            )
        if progress_callback is not None:
            try:
                progress_callback(1.0)
            except Exception:
                pass
    finally:
        fh_in.close()
        fh_out.close()

    return ConversionSummary(
        source_path=src,
        output_path=dst,
        joint_labels=tuple(joint_labels),
        segment_labels=tuple(segment_labels),
        calibration_frame_count=calibration_count,
        normal_frame_count=normal_count,
        sample_rate_hz=sample_rate_hz,
        mvn_version=mvn_version,
    )


def _build_columns(
    segment_labels: list[str],
    joint_labels: list[str],
) -> list[str]:
    """Column order matches what the UDP bridge publishes: segments first
    (x,y,z per segment), then joints (x,y,z per joint)."""
    columns = ["time_sec"]
    for i, _label in enumerate(segment_labels):
        # Segment channels use numeric index (seg00..seg22), not the
        # semantic label — this matches how xsens.udp_source publishes.
        columns.append(f"xsens.seg{i:02d}.x")
        columns.append(f"xsens.seg{i:02d}.y")
        columns.append(f"xsens.seg{i:02d}.z")
    for label in joint_labels:
        columns.append(f"xsens.joint.{label}.x")
        columns.append(f"xsens.joint.{label}.y")
        columns.append(f"xsens.joint.{label}.z")
    return columns


def _write_header(
    fh,
    *,
    src: Path,
    columns: list[str],
    sample_rate_hz: float,
    mvn_version: str,
    segment_labels: list[str],
    joint_labels: list[str],
) -> None:
    from datetime import datetime

    fh.write("# GaitLab CSV session\n")
    fh.write(f"# session={src.stem}\n")
    fh.write(f"# started={datetime.now().isoformat()}\n")
    fh.write("# source=mvnx-import\n")
    fh.write(f"# source_path={src}\n")
    if mvn_version:
        fh.write(f"# mvn_version={mvn_version}\n")
    if sample_rate_hz > 0:
        fh.write("# sample_mode=hz\n")
        fh.write(f"# sample_hz={sample_rate_hz:.4g}\n")
    fh.write(f"# segment_count={len(segment_labels)}\n")
    fh.write(f"# joint_count={len(joint_labels)}\n")
    fh.write(f"# column_count={len(columns)}\n")


def _write_frame(
    writer: csv.writer,
    frame_elem: etree._Element,
    *,
    segment_labels: list[str],
    joint_labels: list[str],
    frame_zero_time_ms: float | None,
) -> None:
    """Serialise one `<frame>` into a CSV row.

    Time is taken from the frame's own ``time`` attribute (ms since capture
    start) rather than derived from frame index — preserves gaps caused by
    dropped samples in the original recording.
    """
    time_ms_str = frame_elem.get("time")
    try:
        t_sec = float(time_ms_str) / 1000.0 if time_ms_str is not None else 0.0
    except ValueError:
        t_sec = 0.0

    pos = _parse_floats(frame_elem.findtext(_NS + "position"))
    ja = _parse_floats(frame_elem.findtext(_NS + "jointAngle"))

    row: list[str] = [f"{t_sec:.4f}"]
    n_seg = len(segment_labels)
    n_joint = len(joint_labels)

    for i in range(n_seg):
        base = 3 * i
        if base + 2 < len(pos):
            row.append(f"{pos[base]:.6f}")
            row.append(f"{pos[base + 1]:.6f}")
            row.append(f"{pos[base + 2]:.6f}")
        else:
            # Missing / truncated field — write empty cells rather than
            # zero, so downstream consumers can tell absent from real 0.
            row.append("")
            row.append("")
            row.append("")

    for i in range(n_joint):
        base = 3 * i
        if base + 2 < len(ja):
            row.append(f"{ja[base]:.6f}")
            row.append(f"{ja[base + 1]:.6f}")
            row.append(f"{ja[base + 2]:.6f}")
        else:
            row.append("")
            row.append("")
            row.append("")

    writer.writerow(row)


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Stream-convert an Xsens .mvnx recording to a GaitLab CSV.",
    )
    ap.add_argument("mvnx_path", type=Path, help=".mvnx file to read")
    ap.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output CSV path (default: <mvnx-stem>.csv next to the source)",
    )
    ap.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = ap.parse_args(argv)

    dst = args.output or args.mvnx_path.with_suffix(".csv")

    def _print_progress(pct: float) -> None:
        if not args.quiet:
            sys.stdout.write(f"\r  {pct * 100:5.1f}%")
            sys.stdout.flush()

    summary = convert_mvnx_to_csv(
        args.mvnx_path, dst, progress_callback=_print_progress
    )
    if not args.quiet:
        sys.stdout.write("\n")
    print(f"Wrote {summary.output_path}")
    print(
        f"  {summary.normal_frame_count} frames "
        f"@ {summary.sample_rate_hz:g} Hz  "
        f"(+ {summary.calibration_frame_count} calibration frames skipped)"
    )
    print(
        f"  {len(summary.segment_labels)} segments · "
        f"{len(summary.joint_labels)} joints"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
