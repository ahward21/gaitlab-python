"""Sessions tab — load a recorded CSV, replay it, and record a derived session.

Wires ``gaitlab.session.SessionPlayer`` (headless, deterministic) to a Qt
transport UI. All player state changes flow back through
``on_state_change`` -> ``_apply_state()`` so the buttons/labels stay in sync
even when a background thread transitions the player (e.g. hits EOF or
finishes a seek).

Save-Derived rule (see docs/REPLAY_GUIDE.md): recording only runs during
continuous Play. Any seek stops the recording — enforced here by wiring
``player.on_seek_during_record`` to ``_stop_derived_recording()``.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gaitlab.record.csv_recorder import CsvRecorder
from gaitlab.scripts.offline_runner import OfflineAnalysisResult
from gaitlab.session.player import (
    PlayerState,
    SessionComputeResult,
    SessionFile,
    SessionPlayer,
)
from gaitlab.ui.context import LabContext
from gaitlab.ui.help import help_banner, tip

_CAPTURES_DIR = Path.home() / "GaitLabCaptures"

# Realtime playback speeds only — offline "as-fast-as-possible" is now the
# Batch analysis button which also captures per-row output for graphing.
_SPEED_CHOICES: list[tuple[str, float]] = [
    ("1×", 1.0),
    ("2×", 2.0),
    ("5×", 5.0),
    ("10×", 10.0),
]

_BATCH_PLOT_COLORS = [
    (110, 159, 190),
    (212, 162, 110),
    (127, 171, 138),
    (201, 123, 123),
    (160, 139, 201),
    (110, 181, 176),
    (200, 200, 100),
    (170, 130, 170),
]

# Scrubber uses integer ticks; map to real seconds via total duration.
_SCRUB_TICKS = 1000


class SessionsPanel(QWidget):
    """Load / replay / scrub a recorded CSV; optionally record a derived session."""

    # Bridged so we can safely poke Qt from the player's background threads.
    _state_signal = Signal()

    def __init__(self, ctx: LabContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._player = SessionPlayer(ctx.hub, ctx.scripts)
        # Second recorder — the shared ctx.recorder is for live capture and
        # may already be running. Derived recordings go to their own file.
        self._derived_recorder = CsvRecorder(ctx.hub, output_dir=_CAPTURES_DIR)
        self._scrubbing = False  # user is dragging the timeline — suppress redraws

        # ----- File / metadata -----
        self._file_label = QLabel("(no session loaded)")
        self._file_label.setWordWrap(True)
        self._file_label.setStyleSheet("color: #cfd6df;")

        open_btn = QPushButton("Open recording…")
        tip(
            open_btn,
            f"Load a GaitLab CSV, or an Xsens .mvnx (auto-converted on first "
            f"open, then cached alongside the source). Default folder: {_CAPTURES_DIR}",
        )
        open_btn.clicked.connect(self._open_file)

        unload_btn = QPushButton("Close")
        tip(unload_btn, "Unload the session and hand scripts back to their live timer threads.")
        unload_btn.clicked.connect(self._unload)

        file_row = QHBoxLayout()
        file_row.addWidget(open_btn)
        file_row.addWidget(unload_btn)
        file_row.addWidget(self._file_label, 1)

        self._meta_box = QGroupBox("Session")
        meta_form = QFormLayout(self._meta_box)
        self._meta_session = QLabel("—")
        self._meta_started = QLabel("—")
        self._meta_duration = QLabel("—")
        self._meta_rate = QLabel("—")
        self._meta_channels = QLabel("—")
        meta_form.addRow("Name", self._meta_session)
        meta_form.addRow("Started", self._meta_started)
        meta_form.addRow("Duration", self._meta_duration)
        meta_form.addRow("Sample rate", self._meta_rate)
        meta_form.addRow("Channels", self._meta_channels)

        # ----- Transport -----
        self._play_btn = QPushButton("▶ Play")
        self._play_btn.clicked.connect(self._toggle_play)
        tip(self._play_btn, "Play / pause. Speed selector to the right.")
        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.clicked.connect(self._stop)
        tip(self._stop_btn, "Stop and rewind to the start of the session.")

        self._speed_combo = QComboBox()
        for label, _ in _SPEED_CHOICES:
            self._speed_combo.addItem(label)
        self._speed_combo.setCurrentIndex(0)
        self._speed_combo.currentIndexChanged.connect(self._on_speed_change)
        tip(
            self._speed_combo,
            "Playback speed. Sample sequence is bit-identical at every speed — only wall-clock "
            "pacing changes. For as-fast-as-possible offline analysis use the Batch analysis "
            "button below instead.",
        )

        transport = QHBoxLayout()
        transport.addWidget(self._play_btn)
        transport.addWidget(self._stop_btn)
        transport.addWidget(QLabel("Speed"))
        transport.addWidget(self._speed_combo)
        transport.addStretch(1)

        # ----- Timeline scrubber -----
        self._scrub = QSlider(Qt.Orientation.Horizontal)
        self._scrub.setRange(0, _SCRUB_TICKS)
        self._scrub.setValue(0)
        self._scrub.setEnabled(False)
        self._scrub.sliderPressed.connect(self._on_scrub_press)
        self._scrub.sliderReleased.connect(self._on_scrub_release)
        tip(
            self._scrub,
            "Click or drag to seek. Seeking resets every enabled script and replays "
            "from the start up to the target — deterministic but takes a moment on long sessions.",
        )
        self._time_label = QLabel("0.00 s / 0.00 s")
        self._time_label.setStyleSheet("color: #8a93a0;")

        # ----- Save-Derived -----
        self._derived_box = QGroupBox("Save derived session")
        d_layout = QVBoxLayout(self._derived_box)
        d_layout.addWidget(
            help_banner(
                "Records every hub channel (including live script outputs) to a new CSV in "
                f"{_CAPTURES_DIR}. Starts on Play, stops on Pause / Stop / Seek — recordings "
                "are one-shot from the current cursor forward. Enable your scripts BEFORE pressing Play."
            )
        )
        self._derived_name = QLineEdit()
        self._derived_name.setPlaceholderText("e.g. runner_A_baseline_ratio")
        tip(self._derived_name, "Base name for the derived CSV. Timestamp is appended automatically.")

        self._save_derived = QCheckBox("Record derived session on next Play")
        tip(
            self._save_derived,
            "When on, pressing Play starts a CSV alongside playback. Any seek stops the recording "
            "so the file's time axis stays monotonic.",
        )
        self._derived_path_label = QLabel("")
        self._derived_path_label.setStyleSheet("color: #8a9;")
        self._derived_path_label.setWordWrap(True)

        d_form = QFormLayout()
        d_form.addRow("Output name", self._derived_name)
        d_form.addRow(self._save_derived)
        d_layout.addLayout(d_form)
        d_layout.addWidget(self._derived_path_label)

        # ----- Batch (offline) analysis -----
        self._batch_box = QGroupBox("Batch analysis (offline)")
        b_layout = QVBoxLayout(self._batch_box)
        b_layout.addWidget(
            help_banner(
                "Runs every enabled script over every row in one pass — no real-time "
                "pacing, no LSL push. Produces a graph of all script outputs over the "
                "full session and (optionally) saves the per-row values to a CSV. Use "
                "this to compare an offline analysis (like scipy.find_peaks on the full "
                "array) against what the same script produces on the live rolling window."
            )
        )
        enabled_label = QLabel("Enabled scripts (from the Scripts tab):")
        enabled_label.setStyleSheet("color: #8a93a0;")
        self._enabled_scripts_list = QListWidget()
        self._enabled_scripts_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._enabled_scripts_list.setMaximumHeight(110)
        tip(
            self._enabled_scripts_list,
            "Scripts that will be driven by Compute. Bindings show input symbol ← hub "
            "channel. Empty = nothing enabled; enable scripts in the Scripts tab first.",
        )
        # Bookkeeping so tick_refresh only rebuilds the list when the set of
        # enabled scripts or their bindings actually changes — repainting every
        # 50 ms would spuriously flicker any selection state on the widget.
        self._enabled_snapshot: tuple = ()
        b_layout.addWidget(enabled_label)
        b_layout.addWidget(self._enabled_scripts_list)

        offline_label = QLabel(
            "Offline analyses (run once at end of batch — bindings live in the script file):"
        )
        offline_label.setStyleSheet("color: #8a93a0;")
        self._offline_list = QListWidget()
        self._offline_list.setMaximumHeight(110)
        self._offline_list.itemChanged.connect(self._on_offline_item_toggled)
        tip(
            self._offline_list,
            "Check each offline analysis you want the Batch button to run. Discovery "
            "populates this from the same folders as the Scripts tab — press Rescan "
            "in the Scripts tab after adding a new file.",
        )
        self._offline_snapshot: tuple = ()
        b_layout.addWidget(offline_label)
        b_layout.addWidget(self._offline_list)

        self._batch_btn = QPushButton("Compute over entire session")
        tip(
            self._batch_btn,
            "Blocks for a moment while every row is driven through enabled scripts. "
            "Opens a graph window on completion. Enable scripts in the Scripts tab first.",
        )
        self._batch_btn.clicked.connect(self._compute_full_session)
        self._batch_progress = QProgressBar()
        self._batch_progress.setRange(0, 100)
        self._batch_progress.setValue(0)
        self._batch_progress.setVisible(False)
        b_layout.addWidget(self._batch_btn)
        b_layout.addWidget(self._batch_progress)

        # ----- Status -----
        self._status = QLabel("Idle — open a recording to begin.")
        self._status.setWordWrap(True)

        # ----- Layout -----
        root = QVBoxLayout(self)
        root.addLayout(file_row)
        root.addWidget(self._meta_box)
        root.addLayout(transport)
        root.addWidget(self._scrub)
        root.addWidget(self._time_label)
        root.addWidget(self._derived_box)
        root.addWidget(self._batch_box)
        root.addWidget(self._status)
        root.addStretch(1)

        # Wire player callbacks. State-change comes from background threads —
        # bounce through a signal so we update widgets on the Qt thread.
        self._state_signal.connect(self._apply_state)
        self._player.on_state_change = lambda _p: self._state_signal.emit()
        self._player.on_seek_during_record = self._stop_derived_recording

        self._apply_state()

    # ----------------------------------------------------- file / metadata

    def _open_file(self) -> None:
        _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open GaitLab recording",
            str(_CAPTURES_DIR),
            "Recordings (*.csv *.mvnx);;GaitLab CSV (*.csv);;Xsens MVNX (*.mvnx);;All files (*.*)",
        )
        if not path_str:
            return
        chosen = Path(path_str)
        if chosen.suffix.lower() == ".mvnx":
            csv_path = self._import_mvnx(chosen)
            if csv_path is None:
                return
            path_str = str(csv_path)
        try:
            session = self._player.load(path_str)
        except Exception as exc:
            QMessageBox.critical(self, "Sessions", f"Failed to load {path_str}:\n\n{exc}")
            return
        self._file_label.setText(str(session.path))
        self._populate_meta(session)
        # Derived-name default: source-file stem + timestamp is added at save.
        if not self._derived_name.text().strip():
            self._derived_name.setText(f"{session.path.stem}_derived")
        self._apply_state()
        self._status.setText(
            "Loaded. Bind & enable any scripts in the Scripts tab, then press Play. "
            "Seek to jump anywhere — script state is rebuilt from t=0 so it always matches."
        )

    def _import_mvnx(self, mvnx_path: Path) -> Path | None:
        """Convert an Xsens .mvnx to a GaitLab CSV and return the CSV path.

        Caches the conversion next to the source (`<name>.csv` beside
        `<name>.mvnx`). A second Open of the same .mvnx picks up the cached
        CSV — an important quality-of-life win because parsing a 2 GB .mvnx
        takes several minutes. Returns None if the user cancels.
        """
        cached = mvnx_path.with_suffix(".csv")
        if cached.exists():
            reply = QMessageBox.question(
                self,
                "Import MVNX",
                (
                    f"A converted CSV already exists next to this recording:\n\n"
                    f"{cached}\n\n"
                    "Use the cached CSV (fast) or re-convert from the .mvnx (slow, "
                    "overwrites the cached file)?"
                ),
                QMessageBox.StandardButton.Open
                | QMessageBox.StandardButton.Retry
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Open,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return None
            if reply == QMessageBox.StandardButton.Open:
                return cached

        # Local import — keeps the panel importable in environments where
        # lxml isn't installed until the researcher actually tries an mvnx.
        try:
            from tools.mvnx_to_gaitlab_csv import convert_mvnx_to_csv
        except ImportError as exc:
            QMessageBox.critical(
                self,
                "Import MVNX",
                f"MVNX converter is unavailable ({exc}).\n\n"
                "Install lxml: py -3.10 -m pip install lxml",
            )
            return None

        dlg = QProgressDialog(
            f"Converting {mvnx_path.name}…\n"
            "Parses the full XML in one pass. This can take a few minutes for "
            "a multi-gigabyte recording.",
            "Cancel",
            0,
            100,
            self,
        )
        dlg.setWindowTitle("Import MVNX")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        cancelled = {"flag": False}

        def _on_progress(pct: float) -> None:
            if dlg.wasCanceled():
                cancelled["flag"] = True
                raise KeyboardInterrupt("user cancelled MVNX import")
            dlg.setValue(int(pct * 100))
            QApplication.processEvents()

        try:
            summary = convert_mvnx_to_csv(
                mvnx_path, cached, progress_callback=_on_progress
            )
        except KeyboardInterrupt:
            # Partial CSV is left on disk for inspection but not returned.
            self._status.setText("MVNX import cancelled.")
            return None
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Import MVNX",
                f"Conversion failed:\n\n{type(exc).__name__}: {exc}",
            )
            return None
        finally:
            dlg.close()

        self._status.setText(
            f"Imported {mvnx_path.name} → {cached.name}: "
            f"{summary.normal_frame_count} frames @ {summary.sample_rate_hz:g} Hz "
            f"({summary.calibration_frame_count} calibration frames skipped)."
        )
        return cached

    def _populate_meta(self, s: SessionFile) -> None:
        self._meta_session.setText(s.metadata.session or "—")
        self._meta_started.setText(s.metadata.started or "—")
        self._meta_duration.setText(f"{s.duration_sec:.2f} s ({s.row_count} rows)")
        if s.metadata.sample_mode:
            if s.metadata.sample_hz > 0:
                self._meta_rate.setText(f"{s.metadata.sample_hz:.3g} Hz")
            elif s.metadata.sample_interval_sec > 0:
                self._meta_rate.setText(f"every {s.metadata.sample_interval_sec:.3g} s")
            else:
                self._meta_rate.setText(s.metadata.sample_mode)
        else:
            self._meta_rate.setText("—")
        self._meta_channels.setText(f"{len(s.channels)}")

    def _unload(self) -> None:
        self._stop_derived_recording()
        self._player.unload()
        self._file_label.setText("(no session loaded)")
        self._meta_session.setText("—")
        self._meta_started.setText("—")
        self._meta_duration.setText("—")
        self._meta_rate.setText("—")
        self._meta_channels.setText("—")
        self._apply_state()
        self._status.setText("Session closed. Scripts are back in live mode.")

    # ----------------------------------------------------- transport

    def _toggle_play(self) -> None:
        if self._player.session is None:
            return
        if self._player.state == PlayerState.PLAYING:
            self._player.pause()
        else:
            if self._save_derived.isChecked() and not self._derived_recorder.is_recording:
                self._start_derived_recording()
            self._player.play()

    def _stop(self) -> None:
        self._stop_derived_recording()
        self._player.stop()

    def _on_speed_change(self, idx: int) -> None:
        if 0 <= idx < len(_SPEED_CHOICES):
            self._player.set_speed(_SPEED_CHOICES[idx][1])

    # ----------------------------------------------------- scrubber

    def _on_scrub_press(self) -> None:
        self._scrubbing = True

    def _on_scrub_release(self) -> None:
        self._scrubbing = False
        s = self._player.session
        if s is None:
            return
        frac = self._scrub.value() / _SCRUB_TICKS
        target = frac * s.duration_sec
        self._status.setText(f"Seeking to {target:.2f} s… (replays from start, may take a moment)")
        # Blocks the Qt loop for the fast-forward — acceptable given the
        # determinism guarantee. Sessions long enough for this to feel slow
        # would be several minutes; snap the UI so users see the busy state.
        self.setEnabled(False)
        try:
            self._player.seek(target)
        finally:
            self.setEnabled(True)
        self._status.setText(f"Seek done — at {self._player.current_time_sec:.2f} s.")

    # ----------------------------------------------------- derived recording

    def _start_derived_recording(self) -> None:
        s = self._player.session
        if s is None:
            return
        base = (self._derived_name.text().strip()
                or (s.path.stem + "_derived"))
        meta = {
            "derived_from": str(s.path),
            "derived_at": datetime.now().isoformat(),
            "player_start_time_sec": f"{self._player.current_time_sec:.4f}",
        }
        try:
            path = self._derived_recorder.start(
                base,
                metadata=meta,
                output_name=base,
                sample_mode="hz",
                sample_value=120.0,  # capture at 2x typical MVN rate to avoid aliasing
            )
        except Exception as exc:
            QMessageBox.warning(self, "Sessions", f"Could not start derived recording:\n\n{exc}")
            return
        self._derived_path_label.setText(f"Recording → {path}")

    def _stop_derived_recording(self) -> None:
        if not self._derived_recorder.is_recording:
            return
        path = self._derived_recorder.stop()
        if path is not None:
            self._derived_path_label.setText(f"Saved: {path}")
        # Uncheck so the next Play doesn't silently restart recording.
        self._save_derived.blockSignals(True)
        self._save_derived.setChecked(False)
        self._save_derived.blockSignals(False)

    # ----------------------------------------------------- batch (offline) compute

    def _compute_full_session(self) -> None:
        s = self._player.session
        if s is None:
            return
        if not self.ctx.scripts.enabled_ids() and not self.ctx.offline.enabled_ids():
            QMessageBox.information(
                self,
                "Batch analysis",
                "Enable at least one script (Scripts tab) or offline analysis "
                "(the list above) before running a batch — there is nothing to graph yet.",
            )
            return
        self._stop_derived_recording()
        self._batch_progress.setVisible(True)
        self._batch_progress.setValue(0)
        self.setEnabled(False)
        # setEnabled(False) also disables children including the progress bar;
        # re-enable just the progress bar so the researcher sees it move.
        self._batch_progress.setEnabled(True)
        self._status.setText(f"Batch: driving {s.row_count} rows through enabled scripts…")

        def _on_progress(done: int, total: int) -> None:
            pct = int(100 * done / max(1, total))
            self._batch_progress.setValue(pct)
            QApplication.processEvents()

        try:
            result = self._player.compute_full_session(progress_callback=_on_progress)
        except Exception as exc:
            self.setEnabled(True)
            self._batch_progress.setVisible(False)
            QMessageBox.critical(self, "Batch analysis", f"Batch compute failed:\n\n{exc}")
            self._status.setText(f"Batch failed: {exc}")
            return
        finally:
            self.setEnabled(True)
            self._batch_progress.setVisible(False)

        base = (self._derived_name.text().strip() or (s.path.stem + "_derived"))

        # Run any enabled offline analyses against the completed trace.
        # Combine input + online-output series so an offline analysis can
        # bind to either an input channel or another script's output.
        all_series: dict = {}
        all_series.update(result.input_series)
        all_series.update(result.output_series)
        times_arr = np.asarray(result.times, dtype=np.float64)
        try:
            offline_results = self.ctx.offline.run_all(times_arr, all_series)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Batch analysis",
                f"Offline stage raised (batch results still available):\n\n{exc}",
            )
            offline_results = []

        self._status.setText(
            f"Batch done — {result.sample_count} rows, "
            f"{len(result.output_series)} online outputs, "
            f"{len(offline_results)} offline analyses."
        )
        dialog = BatchResultDialog(
            result=result,
            source_path=s.path,
            base_name=base,
            offline_results=offline_results,
            source_metadata=dict(s.metadata.extra),
            parent=self,
        )
        dialog.exec()

    # ----------------------------------------------------- periodic tick (from MainWindow)

    def tick_refresh(self) -> None:
        """Called every ~50 ms by MainWindow. Advances the time label + scrubber."""
        s = self._player.session
        if s is None:
            self._time_label.setText("0.00 s / 0.00 s")
            return
        now = self._player.current_time_sec
        total = s.duration_sec
        self._time_label.setText(f"{now:.2f} s / {total:.2f} s")
        if not self._scrubbing and total > 0:
            frac = min(1.0, max(0.0, now / total))
            self._scrub.blockSignals(True)
            self._scrub.setValue(int(frac * _SCRUB_TICKS))
            self._scrub.blockSignals(False)
        # If the derived recorder is running, feed it hub snapshots at its
        # requested cadence. We piggyback on the MainWindow tick so the same
        # snapshot is used by all consumers this frame.
        if self._derived_recorder.is_recording:
            self._derived_recorder.tick()
        # State button labels can drift if the player transitions off-thread
        # between state-change fires and the current tick.
        self._sync_transport_labels()
        self._refresh_enabled_scripts()
        self._refresh_offline_analyses()

    def _refresh_offline_analyses(self) -> None:
        """Rebuild the offline-analyses checklist only when the discovered
        set changes. Enable state is source-of-truth on the registry, so
        toggling a checkbox writes there, not the panel."""
        registry = self.ctx.offline
        snapshot: list[tuple[str, str, str]] = []
        for cls in registry.discovered():
            snapshot.append((cls.id, cls.display_name or cls.__name__, cls.flavor))
        signature = tuple(snapshot)
        if signature == self._offline_snapshot:
            return
        self._offline_snapshot = signature
        self._offline_list.blockSignals(True)
        self._offline_list.clear()
        if not snapshot:
            item = QListWidgetItem(
                "(no offline analyses — add a .py file and press Rescan in the Scripts tab)"
            )
            item.setForeground(pg.mkColor(138, 147, 160))
            self._offline_list.addItem(item)
        else:
            for aid, name, flavor in snapshot:
                item = QListWidgetItem(f"{name}   [{flavor}]")
                item.setData(Qt.ItemDataRole.UserRole, aid)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if registry.is_enabled(aid) else Qt.CheckState.Unchecked
                )
                self._offline_list.addItem(item)
        self._offline_list.blockSignals(False)

    def _on_offline_item_toggled(self, item: QListWidgetItem) -> None:
        aid = item.data(Qt.ItemDataRole.UserRole)
        if not aid:
            return
        self.ctx.offline.set_enabled(str(aid), item.checkState() == Qt.CheckState.Checked)

    def _refresh_enabled_scripts(self) -> None:
        """Rebuild the enabled-scripts list only when the set/bindings change."""
        registry = self.ctx.scripts
        snapshot: list[tuple] = []
        for sid in registry.enabled_ids():
            inst = registry.instance(sid)
            if inst is None:
                continue
            bindings = tuple(
                (b.symbol, b.channel_id or "(unbound)") for b in inst.bindings
            )
            snapshot.append((sid, inst.display_name, inst.rate_hz, bindings))
        signature = tuple(snapshot)
        if signature == self._enabled_snapshot:
            return
        self._enabled_snapshot = signature
        self._enabled_scripts_list.clear()
        if not snapshot:
            item = QListWidgetItem("(no scripts enabled — enable in the Scripts tab)")
            item.setForeground(pg.mkColor(138, 147, 160))
            self._enabled_scripts_list.addItem(item)
            return
        for _sid, name, rate, bindings in snapshot:
            if bindings:
                binding_str = ", ".join(f"{sym} ← {cid}" for sym, cid in bindings)
            else:
                binding_str = "no inputs"
            self._enabled_scripts_list.addItem(f"• {name}  ·  {binding_str}  ·  {rate:g} Hz")

    # ----------------------------------------------------- state / cleanup

    def _apply_state(self) -> None:
        loaded = self._player.session is not None
        state = self._player.state
        self._scrub.setEnabled(loaded and state != PlayerState.SEEKING)
        self._play_btn.setEnabled(loaded)
        self._stop_btn.setEnabled(loaded)
        self._batch_btn.setEnabled(loaded and state != PlayerState.SEEKING)
        self._sync_transport_labels()
        # Auto-stop derived recording if the player left the PLAYING state
        # for any reason (EOF, pause, stop, seek). Keeps CSV time monotonic.
        if state != PlayerState.PLAYING and self._derived_recorder.is_recording:
            self._stop_derived_recording()

    def _sync_transport_labels(self) -> None:
        state = self._player.state
        if state == PlayerState.PLAYING:
            self._play_btn.setText("❚❚ Pause")
        elif state == PlayerState.SEEKING:
            self._play_btn.setText("… Seeking")
        else:
            self._play_btn.setText("▶ Play")

    # ----------------------------------------------------- clock (for graphs)

    def virtual_clock(self) -> tuple[float | None, int]:
        """Player's virtual time in seconds + a seek generation counter.

        Returns ``(None, gen)`` when no session is loaded — callers should
        fall back to wall-clock. The generation number bumps on every seek /
        stop / load; consumers detect virtual-clock discontinuities by
        comparing against the last seen value.
        """
        if self._player.session is None:
            return (None, self._player.seek_generation)
        return (self._player.current_time_sec, self._player.seek_generation)

    def shutdown(self) -> None:
        """Called from MainWindow.closeEvent — best-effort cleanup."""
        self._stop_derived_recording()
        try:
            self._player.unload()
        except Exception:
            pass


class BatchResultDialog(QDialog):
    """Modal display of a full-session batch compute.

    Plots every script output channel against session time in one panel.
    A side list lets the researcher toggle individual series. The Save CSV
    button writes a row-per-sample CSV containing every input and output
    channel to the GaitLabCaptures folder alongside the source recording.
    """

    def __init__(
        self,
        *,
        result: SessionComputeResult,
        source_path: Path,
        base_name: str,
        offline_results: list[OfflineAnalysisResult] | None = None,
        source_metadata: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._result = result
        self._source_path = source_path
        self._base_name = base_name
        self._offline_results: list[OfflineAnalysisResult] = list(offline_results or [])
        # Provenance from the loaded session. When the session came from
        # the .mvnx converter, this carries `source=mvnx-import` and
        # `source_path=<mvnx>` so the batch/offline CSVs can point at the
        # true origin recording, not the intermediate cache CSV.
        self._source_metadata: dict[str, str] = dict(source_metadata or {})
        self.setWindowTitle("Batch analysis result")
        self.resize(1000, 620)

        header = QLabel(
            f"Source: {source_path.name}   ·   "
            f"{result.sample_count} rows   ·   "
            f"{len(result.output_series)} online outputs   ·   "
            f"{len(result.input_series)} input channels   ·   "
            f"{len(self._offline_results)} offline analyses"
        )
        header.setWordWrap(True)
        header.setStyleSheet("color: #cfd6df;")

        self._plot = pg.PlotWidget()
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.setLabel("left", "Value")
        self._plot.addLegend(offset=(10, 10))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.itemChanged.connect(self._on_item_toggled)
        # Set an initial plot title so an opened-but-empty plot self-
        # explains. Cleared once the user ticks the first series.
        self._plot.setTitle(
            "Tick a series in the list to plot it "
            "(series are off by default — batches can be hundreds of channels × 100k+ samples)"
        )

        # Series bookkeeping: `_series_specs` holds the raw data + pen for
        # every togglable entry (line series AND scatter overlays) keyed by
        # its list-item label. `_curves` maps the same label to a live
        # `PlotDataItem` **only while it's on screen** — off-toggling
        # removes the item and drops the reference so a 135-channel batch
        # never has more than the researcher-picked few in the plot.
        self._times_np: np.ndarray = np.asarray(result.times, dtype=np.float64)
        self._series_specs: dict[str, dict] = {}
        self._curves: dict[str, pg.PlotDataItem] = {}

        # Prefer showing script outputs by default (that's the point of the
        # batch), but if none are present fall back to inputs so the graph
        # isn't empty.
        primary = result.output_series if result.output_series else result.input_series
        for i, (cid, values) in enumerate(primary.items()):
            color = _BATCH_PLOT_COLORS[i % len(_BATCH_PLOT_COLORS)]
            self._series_specs[cid] = {
                "kind": "line",
                "x": self._times_np,
                "y": np.asarray(values, dtype=np.float64),
                "color": color,
                "legend_name": cid,
            }
            item = QListWidgetItem(cid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)     # OFF by default
            item.setForeground(pg.mkColor(*color))
            self._list.addItem(item)

        # Peak overlays follow the same lazy pattern. They're tiny (~1k
        # points) but keeping the toggle behavior uniform is worth more
        # than the marginal render cost.
        for i, offr in enumerate(self._offline_results):
            if not offr.ok:
                continue
            idx = offr.outputs.get("peak_index")
            val = offr.outputs.get("peak_value")
            if idx is None or val is None or len(idx) == 0:
                continue
            frame_ids = np.asarray(idx, dtype=np.int64)
            in_range = (frame_ids >= 0) & (frame_ids < len(self._times_np))
            frame_ids = frame_ids[in_range]
            ys = np.asarray(val, dtype=np.float64)[in_range]
            xs = self._times_np[frame_ids]
            color = _BATCH_PLOT_COLORS[(i + len(primary)) % len(_BATCH_PLOT_COLORS)]
            label = f"{offr.display_name}: peaks ({len(frame_ids)})"
            self._series_specs[label] = {
                "kind": "scatter",
                "x": xs,
                "y": ys,
                "color": color,
                "legend_name": label,
            }
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)     # OFF by default
            item.setForeground(pg.mkColor(*color))
            self._list.addItem(item)

        # Summary panel for offline analyses — small, always visible even
        # when there are no per-frame outputs to overlay.
        offline_summary = QLabel(self._format_offline_summary())
        offline_summary.setWordWrap(True)
        offline_summary.setStyleSheet(
            "background: #1e2530; color: #cfd6df; padding: 8px; border-radius: 4px;"
        )

        save_btn = QPushButton("Save batch CSV")
        tip(save_btn, "Writes one row per session sample with every input + online-output channel.")
        save_btn.clicked.connect(self._save_csv)
        save_offline_btn = QPushButton("Save offline CSVs")
        tip(
            save_offline_btn,
            "Writes one CSV per successful offline analysis (variable-length "
            "outputs like peak lists) next to the batch CSV.",
        )
        save_offline_btn.clicked.connect(self._save_offline_csvs)
        save_offline_btn.setEnabled(any(r.ok and r.outputs for r in self._offline_results))
        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(self.reject)
        close_btn.accepted.connect(self.accept)

        bottom = QHBoxLayout()
        bottom.addWidget(save_btn)
        bottom.addWidget(save_offline_btn)
        bottom.addStretch(1)
        bottom.addWidget(close_btn)

        body = QHBoxLayout()
        body.addWidget(self._plot, 3)
        body.addWidget(self._list, 1)

        root = QVBoxLayout(self)
        root.addWidget(header)
        root.addLayout(body, 1)
        root.addWidget(offline_summary)
        root.addLayout(bottom)

    def _format_offline_summary(self) -> str:
        if not self._offline_results:
            return "No offline analyses were enabled for this batch."
        lines = ["Offline analyses:"]
        for r in self._offline_results:
            if not r.ok:
                lines.append(f"  ✗ {r.display_name} [{r.flavor}] — ERROR: {r.error}")
                continue
            shapes = ", ".join(
                f"{k}: {len(v)}" for k, v in r.outputs.items()
            ) or "no outputs"
            lines.append(
                f"  ✓ {r.display_name} [{r.flavor}]  "
                f"({r.duration_sec * 1000:.0f} ms)  →  {shapes}"
            )
        return "\n".join(lines)

    def _save_offline_csvs(self) -> None:
        _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for r in self._offline_results:
            if not r.ok or not r.outputs:
                continue
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in r.analysis_id)
            path = _CAPTURES_DIR / f"{self._base_name}_offline_{safe_id}_{stamp}.csv"
            try:
                self._write_offline_csv(path, r)
                written.append(path)
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Save offline CSVs",
                    f"Could not write {path.name}:\n\n{exc}",
                )
        if written:
            QMessageBox.information(
                self,
                "Save offline CSVs",
                "Wrote:\n" + "\n".join(str(p) for p in written),
            )

    def _write_provenance(self, fh) -> None:
        """Write the shared `# ...` provenance header used by every
        batch/offline CSV. Puts the true origin recording (the .mvnx if
        the session was imported via the converter) on `batch_source`,
        and keeps the intermediate cache CSV on `batch_source_cache`.
        """
        fh.write(f"# batch_generated={datetime.now().isoformat()}\n")
        extras = self._source_metadata
        if extras.get("source") == "mvnx-import" and extras.get("source_path"):
            # Session was loaded from an .mvnx via the converter — the
            # true origin is the .mvnx one folder up, not the cache CSV
            # SessionPlayer actually read.
            fh.write(f"# batch_source={extras['source_path']}\n")
            fh.write(f"# batch_source_type=mvnx\n")
            fh.write(f"# batch_source_cache={self._source_path}\n")
            if extras.get("mvn_version"):
                fh.write(f"# mvn_version={extras['mvn_version']}\n")
        else:
            # Session was loaded from a plain GaitLab CSV.
            fh.write(f"# batch_source={self._source_path}\n")
            fh.write(f"# batch_source_type=csv\n")

    def _write_offline_csv(self, path: Path, r: OfflineAnalysisResult) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            self._write_provenance(f)
            f.write(f"# offline_analysis={r.analysis_id}\n")
            f.write(f"# offline_flavor={r.flavor}\n")
            f.write(f"# offline_duration_sec={r.duration_sec:.6f}\n")
            writer = csv.writer(f)
            cols = list(r.outputs.keys())
            writer.writerow(cols)
            # Outputs may differ in length — pad short columns with "".
            max_len = max((len(v) for v in r.outputs.values()), default=0)
            for i in range(max_len):
                row = []
                for c in cols:
                    arr = r.outputs[c]
                    if i < len(arr):
                        v = arr[i]
                        row.append(
                            f"{float(v):.6f}" if isinstance(v, (int, float, np.floating, np.integer))
                            else str(v)
                        )
                    else:
                        row.append("")
                writer.writerow(row)

    def _on_item_toggled(self, item: QListWidgetItem) -> None:
        """Add or remove the curve from the plot on check-state change.

        Lazy add is the point of this whole refactor: rendering every
        series up-front is what locked up the app. We create the
        `PlotDataItem` on first check, remove it on uncheck, and free
        the reference so it can be garbage-collected.
        """
        label = item.text()
        spec = self._series_specs.get(label)
        if spec is None:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        existing = self._curves.get(label)

        if checked and existing is None:
            color = spec["color"]
            if spec["kind"] == "line":
                curve = self._plot.plot(
                    spec["x"], spec["y"],
                    pen=pg.mkPen(color, width=2),
                    name=spec["legend_name"],
                )
            else:  # scatter — peak overlays
                curve = self._plot.plot(
                    spec["x"], spec["y"],
                    pen=None,
                    symbol="x",
                    symbolBrush=pg.mkBrush(*color),
                    symbolPen=pg.mkPen(*color, width=2),
                    symbolSize=8,
                    name=spec["legend_name"],
                )
            self._curves[label] = curve
            # First tick removes the "how to use this dialog" placeholder title.
            self._plot.setTitle("")
        elif not checked and existing is not None:
            self._plot.removeItem(existing)
            self._curves.pop(label, None)

    def _save_csv(self) -> None:
        _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        default_name = f"{self._base_name}_batch_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save batch CSV",
            str(_CAPTURES_DIR / default_name),
            "CSV (*.csv)",
        )
        if not path_str:
            return
        out_path = Path(path_str)
        try:
            with out_path.open("w", newline="", encoding="utf-8") as f:
                self._write_provenance(f)
                f.write(f"# sample_count={self._result.sample_count}\n")
                writer = csv.writer(f)
                input_ids = list(self._result.input_series.keys())
                output_ids = list(self._result.output_series.keys())
                header = ["time_sec", *input_ids, *output_ids]
                writer.writerow(header)
                for i, t in enumerate(self._result.times):
                    row = [f"{t:.4f}"]
                    for cid in input_ids:
                        row.append(f"{self._result.input_series[cid][i]:.6f}")
                    for cid in output_ids:
                        row.append(f"{self._result.output_series[cid][i]:.6f}")
                    writer.writerow(row)
        except Exception as exc:
            QMessageBox.critical(self, "Batch analysis", f"Could not save CSV:\n\n{exc}")
            return
        QMessageBox.information(self, "Batch analysis", f"Saved:\n{out_path}")
