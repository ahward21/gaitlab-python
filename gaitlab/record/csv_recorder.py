"""CSV session recorder for hub channels."""

from __future__ import annotations

import csv
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
        self.path: Path | None = None
        self.is_recording = False
        self.session_name = ""

    def start(self, session_name: str = "", metadata: dict | None = None) -> Path:
        if self.is_recording:
            self.stop()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = (session_name or "session").strip().replace(" ", "_") or "session"
        self.session_name = base
        self.path = self.output_dir / f"GaitLab_{base}_{stamp}.csv"
        self._columns = ["time_sec"] + self.hub.channel_ids()
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        meta = metadata or {}
        self._file.write("# GaitLab CSV session\n")
        self._file.write(f"# session={base}\n")
        self._file.write(f"# started={datetime.now().isoformat()}\n")
        for k, v in meta.items():
            self._file.write(f"# {k}={v}\n")
        self._writer = csv.writer(self._file)
        self._writer.writerow(self._columns)
        self._t0 = time.perf_counter()
        self.is_recording = True
        return self.path

    def tick(self) -> None:
        if not self.is_recording or self._writer is None:
            return
        snap = self.hub.snapshot()
        t = time.perf_counter() - self._t0
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
