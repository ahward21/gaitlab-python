"""CSV session recorder for hub channels with researcher-chosen sample rate."""

from __future__ import annotations

import csv
import re
import time
from datetime import datetime
from pathlib import Path

from gaitlab.hub import DataHub


class CsvRecorder:
    def __init__(self, hub: DataHub, output_dir: Path | str | None = None) -> None:
        self.hub = hub
        home = Path.home() / "GaitLabCaptures"
        self.output_dir = Path(output_dir) if output_dir else home
        self._file = None
        self._writer: csv.writer | None = None
        self._columns: list[str] = []
        self._t0 = 0.0
        self._last_write = -1e9
        self._min_interval = 0.0  # seconds between rows
        self.path: Path | None = None
        self.is_recording = False
        self.session_name = ""
        self.sample_mode = "hz"
        self.sample_value = 50.0

    def start(
        self,
        session_name: str = "",
        metadata: dict | None = None,
        *,
        output_name: str = "",
        sample_mode: str = "hz",
        sample_value: float = 50.0,
    ) -> Path:
        """
        sample_mode:
          - ``hz`` / ``fps``: write about ``sample_value`` rows per second
          - ``interval``: write one row every ``sample_value`` seconds
        """
        if self.is_recording:
            self.stop()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if output_name.strip():
            base = _safe_name(output_name)
        else:
            base = _safe_name(session_name or "session")
        self.session_name = base
        self.sample_mode = sample_mode
        self.sample_value = float(sample_value)
        if sample_mode in ("hz", "fps"):
            hz = max(0.01, float(sample_value))
            self._min_interval = 1.0 / hz
        else:
            self._min_interval = max(0.001, float(sample_value))

        self.path = self.output_dir / f"GaitLab_{base}_{stamp}.csv"
        self._columns = ["time_sec"] + self.hub.channel_ids()
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        meta = dict(metadata or {})
        meta.setdefault("sample_mode", sample_mode)
        meta.setdefault("sample_value", str(sample_value))
        if sample_mode in ("hz", "fps"):
            meta.setdefault("sample_hz", f"{float(sample_value):.4g}")
        else:
            meta.setdefault("sample_interval_sec", f"{float(sample_value):.4g}")
        self._file.write("# GaitLab CSV session\n")
        self._file.write(f"# session={base}\n")
        self._file.write(f"# started={datetime.now().isoformat()}\n")
        for k, v in meta.items():
            self._file.write(f"# {k}={v}\n")
        self._writer = csv.writer(self._file)
        self._writer.writerow(self._columns)
        self._t0 = time.perf_counter()
        self._last_write = -1e9
        self.is_recording = True
        return self.path

    def tick(self) -> None:
        if not self.is_recording or self._writer is None:
            return
        now = time.perf_counter()
        if now - self._last_write < self._min_interval:
            return
        self._last_write = now
        snap = self.hub.snapshot()
        t = now - self._t0
        row = [f"{t:.4f}"]
        for cid in self._columns[1:]:
            val = snap.get(cid)
            if val is None:
                val = self.hub.try_get(cid)
            row.append("" if val is None else f"{val:.6f}")
        self._writer.writerow(row)
        if self._file:
            self._file.flush()

    def stop(self) -> Path | None:
        path = self.path
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
        self._file = None
        self._writer = None
        self.is_recording = False
        return path


def _safe_name(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", (text or "").strip()).strip("_")
    return s or "session"
