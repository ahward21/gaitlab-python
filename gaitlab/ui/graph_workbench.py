"""Optional multi-panel graphs: time series or XY, up to 6 series per panel."""

from __future__ import annotations

from collections import deque

import pyqtgraph as pg
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gaitlab.profiles.graph_profile import (
    MAX_SERIES,
    GraphPanel,
    GraphProfile,
    load_graph_profile,
    save_graph_profile,
)
from gaitlab.ui.context import LabContext, PROFILES_DIR
from gaitlab.ui.help import help_banner, tip

SERIES_COLORS = [
    (110, 159, 190),
    (212, 162, 110),
    (127, 171, 138),
    (201, 123, 123),
    (160, 139, 201),
    (110, 181, 176),
]


class GraphPanelWidget(QWidget):
    def __init__(self, ctx: LabContext, panel: GraphPanel, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.panel = panel
        self._buffers: dict[str, deque[tuple[float, float]]] = {}
        self._xy_buf: deque[tuple[float, float]] = deque(maxlen=5000)
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._xy_curve: pg.PlotDataItem | None = None
        self._t0 = None
        self._on_close = None

        header = QHBoxLayout()
        self._title = QLabel(panel.title)
        self._title.setStyleSheet("font-weight: bold;")

        self._mode = QComboBox()
        self._mode.addItem("Time series", "time")
        self._mode.addItem("XY scatter", "xy")
        idx = self._mode.findData(panel.plot_mode)
        self._mode.setCurrentIndex(idx if idx >= 0 else 0)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        tip(self._mode, "Time: up to 6 channels vs time. XY: one X channel vs one Y channel (not time-based).")

        self._window = QDoubleSpinBox()
        self._window.setRange(5.0, 600.0)
        self._window.setValue(panel.window_seconds or 30.0)
        self._window.setSuffix(" s")
        tip(self._window, "Rolling window length for time-series plots.")
        self._window.valueChanged.connect(self._on_window)

        add_btn = QPushButton("+ Y channel")
        tip(add_btn, f"Add a hub channel as a series (max {MAX_SERIES}). In XY mode this is the Y axis.")
        add_btn.clicked.connect(self.add_channel_dialog)

        self._x_btn = QPushButton("Set X channel")
        tip(self._x_btn, "XY mode only: choose which hub channel is the horizontal axis.")
        self._x_btn.clicked.connect(self.set_x_channel_dialog)

        close_btn = QPushButton("×")
        close_btn.setFixedWidth(28)
        close_btn.clicked.connect(self._request_close)

        header.addWidget(self._title, 1)
        header.addWidget(QLabel("Mode"))
        header.addWidget(self._mode)
        header.addWidget(QLabel("Window"))
        header.addWidget(self._window)
        header.addWidget(add_btn)
        header.addWidget(self._x_btn)
        header.addWidget(close_btn)

        self._series_lbl = QLabel("")
        self._series_lbl.setWordWrap(True)

        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.addLegend(offset=(10, 10))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(header)
        layout.addWidget(self._series_lbl)
        layout.addWidget(self.plot, 1)

        self._apply_mode_ui()
        self._rebuild_curves()

    def set_close_callback(self, cb) -> None:
        self._on_close = cb

    def _request_close(self) -> None:
        if self._on_close:
            self._on_close(self)

    def _on_window(self, value: float) -> None:
        self.panel.window_seconds = float(value)

    def _on_mode_changed(self) -> None:
        self.panel.plot_mode = str(self._mode.currentData() or "time")
        self._xy_buf.clear()
        self._apply_mode_ui()
        self._rebuild_curves()

    def _apply_mode_ui(self) -> None:
        xy = self.panel.plot_mode == "xy"
        self._x_btn.setVisible(xy)
        self._window.setEnabled(not xy)
        self._refresh_series_label()

    def _refresh_series_label(self) -> None:
        if self.panel.plot_mode == "xy":
            y = self.panel.channel_ids[0] if self.panel.channel_ids else "(none)"
            x = self.panel.x_channel_id or "(none)"
            self._series_lbl.setText(f"XY: X = {x}   ·   Y = {y}")
        else:
            ch = ", ".join(self.panel.channel_ids) if self.panel.channel_ids else "(none)"
            self._series_lbl.setText(f"Series ({len(self.panel.channel_ids)}/{MAX_SERIES}): {ch}")

    def _rebuild_curves(self) -> None:
        self.plot.clear()
        self._curves.clear()
        self._xy_curve = None
        if self.panel.plot_mode == "xy":
            self.plot.setLabel("bottom", self.panel.x_channel_id or "X")
            self.plot.setLabel("left", self.panel.channel_ids[0] if self.panel.channel_ids else "Y")
            pen = pg.mkPen(color=SERIES_COLORS[0], width=2)
            self._xy_curve = self.plot.plot([], [], pen=None, symbol="o", symbolSize=5, symbolBrush=SERIES_COLORS[0], name="XY")
            # silence unused pen
            _ = pen
        else:
            self.plot.setLabel("bottom", "time (s)")
            self.plot.setLabel("left", "value")
            for i, cid in enumerate(self.panel.channel_ids[:MAX_SERIES]):
                color = SERIES_COLORS[i % len(SERIES_COLORS)]
                if i < len(self.panel.colors_hex) and self.panel.colors_hex[i]:
                    try:
                        hx = self.panel.colors_hex[i].lstrip("#")
                        if len(hx) >= 6:
                            color = (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))
                    except Exception:
                        pass
                curve = self.plot.plot([], [], pen=pg.mkPen(color=color, width=2), name=cid)
                self._curves[cid] = curve
                if cid not in self._buffers:
                    self._buffers[cid] = deque(maxlen=5000)
        if self.panel.y_range_mode == "fixed" and self.panel.y_max > self.panel.y_min:
            self.plot.setYRange(self.panel.y_min, self.panel.y_max, padding=0)
        self._refresh_series_label()

    def add_channel(self, channel_id: str) -> None:
        if not channel_id:
            return
        if self.panel.plot_mode == "xy":
            # XY: only one Y series
            self.panel.channel_ids = [channel_id]
        else:
            if channel_id in self.panel.channel_ids:
                return
            if len(self.panel.channel_ids) >= MAX_SERIES:
                QMessageBox.information(self, "Graphs", f"Max {MAX_SERIES} series per panel.")
                return
            self.panel.channel_ids.append(channel_id)
        self._rebuild_curves()

    def add_channel_dialog(self) -> None:
        ids = self.ctx.hub.channel_ids()
        if not ids:
            QMessageBox.information(self, "Graphs", "No hub channels yet — connect a stream first.")
            return
        cid, ok = QInputDialog.getItem(self, "Add channel", "Hub channel:", ids, 0, False)
        if ok and cid:
            self.add_channel(cid)

    def set_x_channel_dialog(self) -> None:
        ids = self.ctx.hub.channel_ids()
        if not ids:
            return
        cid, ok = QInputDialog.getItem(self, "X channel", "Horizontal axis channel:", ids, 0, False)
        if ok and cid:
            self.panel.x_channel_id = cid
            self._rebuild_curves()

    def tick(self, t: float) -> None:
        if self.panel.plot_mode == "xy":
            self._tick_xy()
            return
        if self._t0 is None:
            self._t0 = t
        rel = t - self._t0
        window = float(self.panel.window_seconds or 30.0)
        for cid, curve in self._curves.items():
            val = self.ctx.hub.try_get(cid)
            if val is None:
                continue
            buf = self._buffers.setdefault(cid, deque(maxlen=5000))
            buf.append((rel, float(val)))
            while buf and rel - buf[0][0] > window:
                buf.popleft()
            curve.setData([p[0] for p in buf], [p[1] for p in buf])
        if self.panel.y_range_mode != "fixed":
            self.plot.enableAutoRange(axis="y", enable=True)

    def _tick_xy(self) -> None:
        if not self.panel.x_channel_id or not self.panel.channel_ids or self._xy_curve is None:
            return
        x = self.ctx.hub.try_get(self.panel.x_channel_id)
        y = self.ctx.hub.try_get(self.panel.channel_ids[0])
        if x is None or y is None:
            return
        self._xy_buf.append((float(x), float(y)))
        self._xy_curve.setData([p[0] for p in self._xy_buf], [p[1] for p in self._xy_buf])
        self.plot.enableAutoRange(enable=True)


class GraphWorkbench(QWidget):
    def __init__(self, ctx: LabContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._panels: list[GraphPanelWidget] = []

        bar = QHBoxLayout()
        add_btn = QPushButton("+ Panel")
        tip(add_btn, "Add another graph panel (optional — raw Channels/CSV are the main workflow).")
        add_btn.clicked.connect(lambda: self.add_panel())
        save_btn = QPushButton("Save…")
        save_btn.clicked.connect(self.save_profile)
        load_btn = QPushButton("Load…")
        load_btn.clicked.connect(self.load_profile)
        bar.addWidget(QLabel("Graphs (optional)"))
        bar.addStretch(1)
        bar.addWidget(add_btn)
        bar.addWidget(load_btn)
        bar.addWidget(save_btn)

        self._host = QVBoxLayout()
        self._host.setSpacing(8)
        scroll_inner = QWidget()
        scroll_inner.setLayout(self._host)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(scroll_inner)

        layout = QVBoxLayout(self)
        layout.addWidget(
            help_banner(
                "Optional live plots. Time series: up to 6 channels vs time. "
                "XY: pick an X channel and a Y channel (e.g. two sensors against each other)."
            )
        )
        layout.addLayout(bar)
        layout.addWidget(scroll, 1)

        self.add_panel()

    def add_panel(self, panel: GraphPanel | None = None) -> GraphPanelWidget:
        panel = panel or GraphPanel(title=f"Panel {len(self._panels) + 1}")
        w = GraphPanelWidget(self.ctx, panel)
        w.set_close_callback(self._remove_panel)
        w.setMinimumHeight(240)
        self._panels.append(w)
        self._host.addWidget(w)
        return w

    def _remove_panel(self, w: GraphPanelWidget) -> None:
        if w in self._panels:
            self._panels.remove(w)
        w.setParent(None)
        w.deleteLater()

    def tick(self, t: float) -> None:
        if not self.isVisible():
            return
        for p in self._panels:
            p.tick(t)

    def capture_profile(self, name: str = "Untitled") -> GraphProfile:
        return GraphProfile(
            profile_name=name,
            panels=[
                GraphPanel(
                    title=p.panel.title,
                    channel_ids=list(p.panel.channel_ids)[:MAX_SERIES],
                    colors_hex=list(p.panel.colors_hex),
                    y_range_mode=p.panel.y_range_mode,
                    y_min=p.panel.y_min,
                    y_max=p.panel.y_max,
                    display_mode=p.panel.display_mode,
                    plot_mode=p.panel.plot_mode,
                    x_channel_id=p.panel.x_channel_id,
                    window_seconds=p.panel.window_seconds,
                )
                for p in self._panels
            ],
        )

    def apply_profile(self, profile: GraphProfile) -> None:
        for w in list(self._panels):
            self._remove_panel(w)
        for panel in profile.panels or [GraphPanel(title="Panel 1")]:
            self.add_panel(panel)

    def save_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "Save graph profile", "Profile name:", text="My graphs")
        if not ok or not name.strip():
            return
        path = PROFILES_DIR / f"graph_{name.strip()}.json"
        save_graph_profile(path, self.capture_profile(name.strip()))

    def load_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load graph profile", str(PROFILES_DIR), "JSON (*.json)"
        )
        if not path:
            return
        self.apply_profile(load_graph_profile(path))
