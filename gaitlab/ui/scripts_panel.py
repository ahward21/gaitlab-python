"""Scripts tab — enable/disable custom analysis scripts and wire their inputs."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gaitlab import channels as channel_catalog
from gaitlab.scripts.api import hub_channel_id_for, lsl_stream_name_for
from gaitlab.scripts.runner import ScriptBinding
from gaitlab.ui.context import LabContext
from gaitlab.ui.help import help_banner, tip


class ScriptsPanel(QWidget):
    """Left: discovered scripts list. Right: bindings + Enable/Disable + status."""

    def __init__(self, ctx: LabContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._current_id: str = ""
        self._input_combos: dict[str, QComboBox] = {}

        # --- LEFT: script list ---
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select_row)
        tip(self._list, "Discovered analysis scripts. Green ● = enabled.")

        rescan_btn = QPushButton("Rescan folders")
        tip(rescan_btn, "Re-import scripts from data/scripts/ and ~/GaitLabScripts/. Disables everything first.")
        rescan_btn.clicked.connect(self._rescan)

        left = QVBoxLayout()
        left.addWidget(QLabel("Scripts"))
        left.addWidget(
            help_banner(
                "Drop a .py file into ~/GaitLabScripts/ and press Rescan. Reference examples "
                "live in the package under data/scripts/."
            )
        )
        left.addWidget(self._list, 1)
        left.addWidget(rescan_btn)

        # --- RIGHT: editor ---
        self._title = QLabel("(select a script)")
        self._title.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._description = QLabel("")
        self._description.setWordWrap(True)
        self._description.setStyleSheet("color: #8a93a0;")

        self._inputs_box = QGroupBox("Inputs — bind each symbol to a live hub channel")
        self._inputs_form = QFormLayout(self._inputs_box)

        self._rate = QDoubleSpinBox()
        self._rate.setRange(0.1, 240.0)
        self._rate.setSuffix(" Hz")
        self._rate.setDecimals(1)
        tip(self._rate, "How often on_sample() runs. Default is the script's declared rate; MVN is 60 Hz.")

        self._publish_lsl = QCheckBox("Publish outputs to LSL (GaitLabScript_<id>)")
        self._publish_lsl.setChecked(True)
        tip(
            self._publish_lsl,
            "Off = hub-only (still visible in Channels/Graphs/CSV). On = also broadcast an LSL outlet "
            "for Unity or LabRecorder to subscribe to.",
        )

        settings_form = QFormLayout()
        settings_form.addRow("Rate", self._rate)
        settings_form.addRow(self._publish_lsl)

        self._outputs_box = QGroupBox("Outputs (read-only)")
        self._outputs_list = QListWidget()
        self._outputs_list.setMaximumHeight(120)
        outs_l = QVBoxLayout(self._outputs_box)
        outs_l.addWidget(
            help_banner(
                "Hub channel ids you can pick in Channels / Graphs / Metrics / CSV. "
                "The LSL outlet name is shown below."
            )
        )
        outs_l.addWidget(self._outputs_list)
        self._lsl_name_label = QLabel("")
        self._lsl_name_label.setStyleSheet("color: #8a93a0;")
        outs_l.addWidget(self._lsl_name_label)

        self._enable_btn = QPushButton("Enable")
        self._enable_btn.setStyleSheet("font-weight: bold;")
        self._enable_btn.clicked.connect(self._toggle_enabled)

        self._status = QLabel("")
        self._status.setWordWrap(True)

        right = QVBoxLayout()
        right.addWidget(self._title)
        right.addWidget(self._description)
        right.addWidget(self._inputs_box)
        right.addLayout(settings_form)
        right.addWidget(self._outputs_box)
        right.addWidget(self._enable_btn)
        right.addWidget(self._status)
        right.addStretch(1)

        root = QHBoxLayout(self)
        root.addLayout(left, 1)
        root.addLayout(right, 2)

        self._rescan()

    # ------------------------------------------------------------------ discovery / list

    def _rescan(self) -> None:
        self.ctx.scripts.disable_all()
        self.ctx.scripts.clear_discovered()
        found = self.ctx.discover_scripts()
        self.ctx.scripts.register_classes([d.cls for d in found])

        # Offline analyses share the same discovery folders — refresh them
        # here so a single Rescan click covers online + offline. Enable
        # state on the offline registry persists across rescans (see
        # OfflineRegistry.clear_discovered docstring).
        self.ctx.offline.clear_discovered()
        found_offline = self.ctx.discover_offline_analyses()
        self.ctx.offline.register_classes([d.cls for d in found_offline])

        self._reload_list()
        parts = []
        if found:
            parts.append(f"{len(found)} online")
        if found_offline:
            parts.append(f"{len(found_offline)} offline")
        if parts:
            self._status.setText(f"Discovered {' + '.join(parts)} analysis file(s).")
        else:
            self._status.setText(
                "No scripts found. Add a .py file to ~/GaitLabScripts/ or data/scripts/ and press Rescan."
            )

    def _reload_list(self) -> None:
        prev_id = self._current_id
        self._list.clear()
        for sid in self.ctx.scripts.discovered_ids():
            cls = self.ctx.scripts.get_class(sid)
            if cls is None:
                continue
            enabled = self.ctx.scripts.instance(sid) is not None
            marker = "● " if enabled else "○ "
            item = QListWidgetItem(f"{marker}{cls.display_name or cls.__name__}")
            item.setData(256, sid)
            self._list.addItem(item)
        # Restore selection if the same id is still in the list.
        for i in range(self._list.count()):
            if self._list.item(i).data(256) == prev_id:
                self._list.setCurrentRow(i)
                return
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        else:
            self._current_id = ""
            self._clear_right_pane()

    # ------------------------------------------------------------------ selection

    def _on_select_row(self, row: int) -> None:
        if row < 0 or row >= self._list.count():
            self._current_id = ""
            self._clear_right_pane()
            return
        sid = self._list.item(row).data(256) or ""
        self._current_id = sid
        self._load_editor(sid)

    def _clear_right_pane(self) -> None:
        self._title.setText("(select a script)")
        self._description.setText("")
        self._clear_inputs_form()
        self._outputs_list.clear()
        self._lsl_name_label.setText("")
        self._enable_btn.setText("Enable")
        self._enable_btn.setEnabled(False)

    def _clear_inputs_form(self) -> None:
        while self._inputs_form.rowCount() > 0:
            self._inputs_form.removeRow(0)
        self._input_combos.clear()

    def _load_editor(self, sid: str) -> None:
        cls = self.ctx.scripts.get_class(sid)
        if cls is None:
            self._clear_right_pane()
            return
        self._title.setText(cls.display_name or cls.__name__)
        doc = (cls.__doc__ or "").strip().splitlines()
        self._description.setText(doc[0] if doc else f"id: {cls.id}")

        # Inputs — one combo per declared input, populated from live hub channels.
        self._clear_inputs_form()
        live_ids = list(self.ctx.hub.channel_ids())
        inst = self.ctx.scripts.instance(sid)
        bindings_by_sym = {b.symbol: b.channel_id for b in (inst.bindings if inst else [])}

        for inp in cls.inputs:
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItem("(unbound — will read 0.0)", "")
            for cid in live_ids:
                label = channel_catalog.label_for(cid)
                combo.addItem(f"{cid}  —  {label}", cid)
            # Also add the default_channel if it isn't live yet, so the user can pre-arm it.
            if inp.default_channel and combo.findData(inp.default_channel) < 0:
                combo.addItem(f"{inp.default_channel}  —  (default, not live yet)", inp.default_channel)

            preferred = bindings_by_sym.get(inp.symbol) or inp.default_channel
            if preferred:
                idx = combo.findData(preferred)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            tip(combo, inp.description or f"Hub channel that feeds symbol '{inp.symbol}'.")
            self._input_combos[inp.symbol] = combo
            label = f"{inp.symbol}" + (f"  ({inp.unit})" if inp.unit else "")
            self._inputs_form.addRow(label, combo)

        # Rate + publish
        self._rate.blockSignals(True)
        self._rate.setValue(float(inst.rate_hz if inst else cls.default_rate_hz))
        self._rate.blockSignals(False)
        self._publish_lsl.blockSignals(True)
        self._publish_lsl.setChecked(inst.publish_lsl if inst else True)
        self._publish_lsl.blockSignals(False)

        # Outputs
        self._outputs_list.clear()
        for out in cls.outputs:
            cid = hub_channel_id_for(cls.id, out.symbol)
            unit = f" ({out.unit})" if out.unit else ""
            self._outputs_list.addItem(QListWidgetItem(f"{cid}{unit}  —  {out.description or ''}"))
        self._lsl_name_label.setText(f"LSL outlet: {lsl_stream_name_for(cls.id)}")

        self._enable_btn.setEnabled(True)
        self._enable_btn.setText("Disable" if inst else "Enable")
        if inst:
            self._render_status(inst)
        else:
            self._status.setText("Idle. Bind inputs and press Enable.")

    # ------------------------------------------------------------------ toggle / status

    def _toggle_enabled(self) -> None:
        sid = self._current_id
        if not sid:
            return
        inst = self.ctx.scripts.instance(sid)
        if inst is not None:
            self.ctx.scripts.disable(sid)
            self._status.setText("Disabled.")
        else:
            bindings = [
                ScriptBinding(symbol=sym, channel_id=(combo.currentData() or combo.currentText().split("—")[0].strip() or ""))
                for sym, combo in self._input_combos.items()
            ]
            new_inst = self.ctx.scripts.enable(
                sid,
                bindings=bindings,
                rate_hz=float(self._rate.value()),
                publish_lsl=self._publish_lsl.isChecked(),
            )
            if new_inst is None or not new_inst.status.enabled:
                err = (new_inst.status.last_error if new_inst else "") or "enable failed"
                QMessageBox.warning(self, "Scripts", f"Could not enable script: {err}")
                self._status.setText(err)
            else:
                self._status.setText("Enabled. Watch Channels for outputs.")
        # Refresh list dots + button text
        self._reload_list()
        for i in range(self._list.count()):
            if self._list.item(i).data(256) == sid:
                self._list.setCurrentRow(i)
                break

    def _render_status(self, inst) -> None:  # ScriptInstance
        s = inst.status
        parts = [
            f"Enabled at {inst.rate_hz:.1f} Hz",
            f"ticks={s.ticks}",
        ]
        if inst.publish_lsl:
            parts.append(f"LSL samples={s.samples_pushed_to_lsl}")
        if s.is_slow:
            parts.append("(slow tick)")
        if s.last_error:
            parts.append(f"last error: {s.last_error}")
        self._status.setText(" · ".join(parts))

    # ------------------------------------------------------------------ tick hook (called from MainWindow)

    def tick_refresh(self) -> None:
        """Called periodically by MainWindow — refresh status line + input-combo channel lists."""
        sid = self._current_id
        if not sid:
            return
        inst = self.ctx.scripts.instance(sid)
        if inst is not None:
            self._render_status(inst)
        # Keep input combos populated with newly-discovered hub channels.
        live_ids = set(self.ctx.hub.channel_ids())
        for combo in self._input_combos.values():
            existing = {combo.itemData(i) for i in range(combo.count()) if combo.itemData(i)}
            missing = live_ids - existing
            for cid in sorted(missing):
                combo.addItem(f"{cid}  —  {channel_catalog.label_for(cid)}", cid)
