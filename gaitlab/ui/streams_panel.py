"""Connection panel: LSL streams + Xsens UDP (configurable ports)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gaitlab.lsl.mappings import (
    PRESET_HR,
    PRESET_MOCAP,
    ChannelMapEntry,
    StreamMapping,
    auto_map_channels,
    load_mappings,
    save_mappings,
)
from gaitlab.ui.context import LabContext, MAPPINGS_DIR
from gaitlab.ui.help import help_banner, tip
from gaitlab.xsens.udp_source import DEFAULT_FALLBACKS, DEFAULT_PORT


class StreamsPanel(QWidget):
    def __init__(self, ctx: LabContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Connections"))
        layout.addWidget(
            help_banner(
                "Connect LSL outlets (HR, custom sensors, mocap-over-LSL) and/or Xsens MVN UDP. "
                "Raw values land in the Channels tab. Change ports/names here if your lab setup differs."
            )
        )

        tabs = QTabWidget()
        tabs.addTab(self._build_lsl_tab(), "LSL")
        tabs.addTab(self._build_udp_tab(), "Xsens UDP")
        layout.addWidget(tabs, 1)

        self._connected = QLabel("Connected: none")
        self._connected.setWordWrap(True)
        self._status = QLabel("Ready")
        self._status.setWordWrap(True)
        layout.addWidget(self._connected)
        layout.addWidget(self._status)

        self.ctx.lsl.set_status_callback(self._on_status)
        self.ctx.xsens.set_status_callback(self._on_status)
        if not self.ctx.lsl.available():
            self._status.setText("pylsl not installed — LSL tab disabled until `pip install pylsl` (+ liblsl).")

        self._refresh_connected_label()

    # ---- LSL tab ----
    def _build_lsl_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(
            help_banner(
                "LSL finds streams by Name + Type (must match the outlet). "
                "Empty Name or Type = wildcard. Presets match Unity defaults."
            )
        )

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Name", "Type", "Channels", "Hz", "Source"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        tip(self._table, "Double-click or select a row, then Connect. Values appear under Channels.")

        self._preset = QComboBox()
        self._preset.addItem("Custom…", None)
        self._preset.addItem("Xsens MVN mocap (LSL)", PRESET_MOCAP)
        self._preset.addItem("Heart rate", PRESET_HR)
        self._preset.currentIndexChanged.connect(self._on_preset)
        tip(self._preset, "Fills Name/Type with Unity-aligned defaults. You can still edit them.")

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. XsensMVN or HeartRate")
        tip(self._name, "Exact LSL stream name from the outlet (case-insensitive). Leave blank to match any name.")
        self._type = QLineEdit()
        self._type.setPlaceholderText("e.g. MoCap or HR")
        tip(self._type, "Exact LSL stream type. Leave blank to match any type.")

        self._resolve_timeout = QSpinBox()
        self._resolve_timeout.setRange(1, 30)
        self._resolve_timeout.setValue(2)
        self._resolve_timeout.setSuffix(" s")
        tip(self._resolve_timeout, "How long to wait when resolving/connecting to an LSL stream.")

        form = QFormLayout()
        form.addRow("Preset", self._preset)
        form.addRow("Stream name", self._name)
        form.addRow("Stream type", self._type)
        form.addRow("Resolve timeout", self._resolve_timeout)

        refresh = QPushButton("Refresh list")
        tip(refresh, "Scan the network for LSL streams (uses resolve timeout).")
        refresh.clicked.connect(self.refresh)
        connect_btn = QPushButton("Connect")
        tip(connect_btn, "Connect using the selected table row, or the Name/Type fields above.")
        connect_btn.clicked.connect(self.connect_lsl)
        disconnect_btn = QPushButton("Disconnect LSL")
        disconnect_btn.clicked.connect(self.disconnect_lsl)
        save_btn = QPushButton("Save mappings")
        tip(save_btn, "Save connected LSL stream maps to data/mappings/user_streams.json")
        save_btn.clicked.connect(self.save_mappings_file)
        load_btn = QPushButton("Load mappings")
        load_btn.clicked.connect(self.load_mappings_file)

        actions = QHBoxLayout()
        actions.addWidget(refresh)
        actions.addWidget(connect_btn)
        actions.addWidget(disconnect_btn)
        actions.addWidget(load_btn)
        actions.addWidget(save_btn)
        actions.addStretch(1)

        layout.addLayout(form)
        layout.addWidget(self._table, 1)
        layout.addLayout(actions)
        return w

    # ---- UDP tab ----
    def _build_udp_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(
            help_banner(
                "Same as Unity “Online — Xsens UDP”: listen for MVN Network Streamer pose packets. "
                "Set the primary port to match MVN (default 9763). If that port is busy, fallbacks are tried."
            )
        )

        box = QGroupBox("UDP listen settings")
        form = QFormLayout(box)

        self._udp_port = QSpinBox()
        self._udp_port.setRange(1, 65535)
        self._udp_port.setValue(DEFAULT_PORT)
        tip(
            self._udp_port,
            "Primary UDP port configured in Movella MVN Network Streamer (Unity default: 9763).",
        )

        self._udp_fallbacks = QLineEdit(",".join(str(p) for p in DEFAULT_FALLBACKS))
        tip(
            self._udp_fallbacks,
            "Comma-separated fallback ports if the primary is in use (Unity defaults: 9764, 9765, 9766).",
        )

        self._udp_char = QSpinBox()
        self._udp_char.setRange(0, 15)
        self._udp_char.setValue(0)
        tip(self._udp_char, "MVN character id to accept (usually 0).")

        from PySide6.QtWidgets import QCheckBox
        self._udp_publish_quats = QCheckBox("Publish quaternions (qw/qx/qy/qz per segment)")
        self._udp_publish_quats.setChecked(self.ctx.xsens.publish_quaternions)
        tip(
            self._udp_publish_quats,
            "Turn on for joint-angle scripts (hip / knee / ankle). Adds 92 extra channels "
            "(23 segments × 4 quat components) on top of the 69 XYZ channels.",
        )
        self._udp_publish_joints = QCheckBox("Publish joint angles (X/Y/Z Euler per joint)")
        self._udp_publish_joints.setChecked(self.ctx.xsens.publish_joint_angles)
        tip(
            self._udp_publish_joints,
            "Turn on to receive MVN Streamer's MXTP20 joint-angle packets. Publishes "
            "xsens.joint.<label>.x/.y/.z (22 joints × 3 axes = 66 channels). "
            "Enable the 'Joint Angles' datagram in MVN Analyze's Network Streamer settings.",
        )

        form.addRow("Primary port", self._udp_port)
        form.addRow("Fallback ports", self._udp_fallbacks)
        form.addRow("Character id", self._udp_char)
        form.addRow(self._udp_publish_quats)
        form.addRow(self._udp_publish_joints)

        start_btn = QPushButton("Start listening")
        tip(start_btn, "Bind UDP and publish raw segment positions as xsens.segNN.x/y/z on the hub.")
        start_btn.clicked.connect(self.start_udp)
        stop_btn = QPushButton("Stop UDP")
        stop_btn.clicked.connect(self.stop_udp)

        row = QHBoxLayout()
        row.addWidget(start_btn)
        row.addWidget(stop_btn)
        row.addStretch(1)

        self._udp_status = QLabel("UDP: idle")
        tip(self._udp_status, "Shows the port actually bound and frame count.")

        layout.addWidget(box)
        layout.addLayout(row)
        layout.addWidget(self._udp_status)
        layout.addStretch(1)
        return w

    def _on_preset(self, _index: int) -> None:
        data = self._preset.currentData()
        if isinstance(data, StreamMapping):
            self._name.setText(data.stream_name)
            self._type.setText(data.stream_type)

    def _on_status(self, msg: str) -> None:
        self._status.setText(msg)
        self._refresh_connected_label()

    def refresh(self) -> None:
        if not self.ctx.lsl.available():
            QMessageBox.warning(self, "LSL", "pylsl is not available.")
            return
        timeout = float(self._resolve_timeout.value())
        self.ctx.lsl.resolve_timeout_s = timeout
        self._status.setText("Resolving streams…")
        streams = self.ctx.lsl.list_streams(timeout=timeout)
        self._table.setRowCount(0)
        for s in streams:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(s.name))
            self._table.setItem(row, 1, QTableWidgetItem(s.type))
            self._table.setItem(row, 2, QTableWidgetItem(str(s.channel_count)))
            self._table.setItem(row, 3, QTableWidgetItem(f"{s.nominal_srate:.1f}"))
            self._table.setItem(row, 4, QTableWidgetItem(s.source_id))
        self._status.setText(f"Found {len(streams)} stream(s)")

    def connect_lsl(self) -> None:
        name = self._name.text().strip()
        typ = self._type.text().strip()
        rows = self._table.selectionModel().selectedRows()
        ch_count = 0
        if rows:
            r = rows[0].row()
            name = self._table.item(r, 0).text() if self._table.item(r, 0) else name
            typ = self._table.item(r, 1).text() if self._table.item(r, 1) else typ
            try:
                ch_count = int(self._table.item(r, 2).text())
            except Exception:
                ch_count = 0
            self._name.setText(name)
            self._type.setText(typ)

        self.ctx.lsl.resolve_timeout_s = float(self._resolve_timeout.value())

        preset = self._preset.currentData()
        channels: list[ChannelMapEntry] = []
        if isinstance(preset, StreamMapping) and preset.channels:
            channels = list(preset.channels)
        elif ch_count > 0:
            channels = auto_map_channels(name or "stream", ch_count)

        mapping = StreamMapping(stream_name=name, stream_type=typ, channels=channels, enabled=True)
        ok = self.ctx.lsl.connect(mapping, ch_count)
        if not ok:
            QMessageBox.warning(self, "Connect", self._status.text() or "Connect failed")
        self._refresh_connected_label()

    def disconnect_lsl(self) -> None:
        self.ctx.lsl.disconnect_all()
        self._status.setText("Disconnected all LSL streams")
        self._refresh_connected_label()

    def start_udp(self) -> None:
        port = int(self._udp_port.value())
        raw = self._udp_fallbacks.text().strip()
        fallbacks: list[int] = []
        if raw:
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    fallbacks.append(int(part))
                except ValueError:
                    QMessageBox.warning(self, "UDP", f"Invalid fallback port: {part}")
                    return
        self.ctx.xsens.configure(
            port,
            fallbacks,
            int(self._udp_char.value()),
            publish_quaternions=self._udp_publish_quats.isChecked(),
            publish_joint_angles=self._udp_publish_joints.isChecked(),
        )
        ok = self.ctx.xsens.start()
        if not ok:
            QMessageBox.warning(self, "UDP", self._status.text() or "Bind failed")
        self._refresh_connected_label()

    def stop_udp(self) -> None:
        self.ctx.xsens.stop()
        self._status.setText("Xsens UDP stopped")
        self._refresh_connected_label()

    def _refresh_connected_label(self) -> None:
        parts = []
        for s in self.ctx.lsl.connected_summaries():
            state = "ok" if s["connected"] else f"down ({s['error']})"
            parts.append(f"LSL {s['name'] or '*'}/{s['type'] or '*'} · {s['channels']} ch · {state}")
        xs = self.ctx.xsens.summary()
        if xs["connected"]:
            parts.append(f"UDP port {xs['bound_port']} · {xs['frames']} frames")
            self._udp_status.setText(
                f"UDP: listening on {xs['bound_port']} (configured {xs['configured_port']}) · {xs['frames']} frames"
            )
        else:
            self._udp_status.setText("UDP: idle" + (f" — {xs['error']}" if xs.get("error") else ""))
        self._connected.setText("Connected: " + (", ".join(parts) if parts else "none"))

    def save_mappings_file(self) -> None:
        path = MAPPINGS_DIR / "user_streams.json"
        maps = [s["mapping"] for s in self.ctx.lsl.connected_summaries()]
        if not maps:
            QMessageBox.information(self, "Save", "No connected LSL streams to save.")
            return
        save_mappings(path, maps)
        self._status.setText(f"Saved mappings → {path}")

    def load_mappings_file(self) -> None:
        path = MAPPINGS_DIR / "user_streams.json"
        if not path.exists():
            path = MAPPINGS_DIR / "default_streams.json"
        if not path.exists():
            QMessageBox.information(self, "Load", "No mappings file found.")
            return
        maps = load_mappings(path)
        for m in maps:
            self.ctx.lsl.connect(m)
        self._refresh_connected_label()
        self._status.setText(f"Loaded {len(maps)} mapping(s) from {path.name}")
