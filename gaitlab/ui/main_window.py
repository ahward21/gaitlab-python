"""Main workbench window — raw data first, graphs optional."""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gaitlab.ui.channels_panel import ChannelsPanel
from gaitlab.ui.context import LabContext
from gaitlab.ui.graph_workbench import GraphWorkbench
from gaitlab.ui.help import tip
from gaitlab.ui.metrics_panel import MetricsPanel
from gaitlab.ui.streams_panel import StreamsPanel


class MainWindow(QMainWindow):
    def __init__(self, ctx: LabContext | None = None) -> None:
        super().__init__()
        self.ctx = ctx or LabContext()
        self.setWindowTitle("GaitLab — raw LSL / UDP lab")
        self.resize(1280, 800)

        self._streams = StreamsPanel(self.ctx)
        self._channels = ChannelsPanel(self.ctx)
        self._metrics = MetricsPanel(self.ctx)
        self._graphs = GraphWorkbench(self.ctx)

        left_tabs = QTabWidget()
        left_tabs.addTab(self._streams, "Connections")
        left_tabs.addTab(self._channels, "Channels")
        left_tabs.addTab(self._metrics, "Metrics")
        tip(left_tabs, "Connections → live Channels → optional Metrics formulas. Graphs are optional on the right.")

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.addWidget(left_tabs, 1)

        self._session = QLineEdit("session")
        self._session.setMaximumWidth(180)
        tip(self._session, "Name used in the CSV filename under ~/GaitLabCaptures/")
        self._rec_btn = QPushButton("● Record")
        tip(self._rec_btn, "Start/stop CSV of all current hub channels (raw results).")
        self._rec_btn.clicked.connect(self._toggle_record)
        self._rec_path = QLabel("")
        self._rec_path.setStyleSheet("color: #8a9;")

        self._show_graphs = QCheckBox("Show graphs")
        self._show_graphs.setChecked(False)
        tip(self._show_graphs, "Optional. Main workflow is Channels + Record; enable for live plots.")
        self._show_graphs.toggled.connect(self._toggle_graphs)

        top = QHBoxLayout()
        top.addWidget(QLabel("GaitLab"))
        top.addStretch(1)
        top.addWidget(self._show_graphs)
        top.addWidget(QLabel("Session"))
        top.addWidget(self._session)
        top.addWidget(self._rec_btn)
        top.addWidget(self._rec_path, 1)

        self._splitter = QSplitter()
        self._splitter.addWidget(left)
        self._splitter.addWidget(self._graphs)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._graphs.setVisible(False)

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(top)
        root.addWidget(self._splitter, 1)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            "Raw results first: connect → Channels → Record. Metrics & graphs are optional."
        )

        self._ui_tick = 0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def _toggle_graphs(self, on: bool) -> None:
        self._graphs.setVisible(on)
        if on:
            sizes = self._splitter.sizes()
            if sum(sizes) > 0 and sizes[1] < 50:
                total = max(sum(sizes), 800)
                self._splitter.setSizes([int(total * 0.55), int(total * 0.45)])

    def _toggle_record(self) -> None:
        if self.ctx.recorder.is_recording:
            path = self.ctx.recorder.stop()
            self._rec_btn.setText("● Record")
            self._rec_path.setText(f"Saved: {path}" if path else "")
            self.statusBar().showMessage("Recording stopped", 3000)
        else:
            parts = []
            for s in self.ctx.lsl.connected_summaries():
                parts.append(f"LSL:{s['name']}/{s['type']}")
            xs = self.ctx.xsens.summary()
            if xs["connected"]:
                parts.append(f"UDP:{xs['bound_port']}")
            path = self.ctx.recorder.start(
                self._session.text().strip() or "session",
                metadata={"streams": ", ".join(parts) if parts else "none"},
            )
            self._rec_btn.setText("■ Stop")
            self._rec_path.setText(str(path))
            self.statusBar().showMessage(f"Recording → {path}", 3000)

    def _on_tick(self) -> None:
        self.ctx.evaluator.tick()
        t = time.perf_counter()
        if self._graphs.isVisible():
            self._graphs.tick(t)
        self._ui_tick += 1
        if self._ui_tick % 4 == 0:
            self._channels.refresh()
            self._metrics.refresh_preview()
            # keep UDP status fresh
            if hasattr(self._streams, "_refresh_connected_label"):
                self._streams._refresh_connected_label()
        if self.ctx.recorder.is_recording:
            self.ctx.recorder.tick()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.ctx.recorder.is_recording:
            self.ctx.recorder.stop()
        self.ctx.lsl.disconnect_all()
        self.ctx.xsens.stop()
        super().closeEvent(event)
