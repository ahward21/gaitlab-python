"""Custom metric formula editor with clear variable ↔ channel binding."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gaitlab import channels as channel_catalog
from gaitlab.metrics.definitions import MetricDefinition, MetricVariableBinding
from gaitlab.metrics.expression import extract_rhs_identifiers, substitute, try_evaluate
from gaitlab.profiles.metric_profile import MetricProfile, load_metric_profile, save_metric_profile
from gaitlab.ui.context import LabContext, PROFILES_DIR
from gaitlab.ui.help import help_banner, tip


class MetricsPanel(QWidget):
    def __init__(self, ctx: LabContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._working: MetricDefinition | None = None

        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select)
        tip(self._list, "Your custom metrics. Select one to edit its formula and inputs.")

        self._name = QLineEdit()
        tip(self._name, "Display name shown in the list and when saving.")
        self._output = QLineEdit()
        tip(self._output, "Hub channel id this metric publishes to (e.g. custom.my_metric).")
        self._unit = QLineEdit()
        self._formula = QTextEdit()
        self._formula.setAcceptRichText(False)
        self._formula.setMaximumHeight(80)
        tip(
            self._formula,
            "Equation using letters on the right of =  (example: y = x + z). "
            "Then bind each letter to a live channel below.",
        )
        self._notes = QTextEdit()
        self._notes.setAcceptRichText(False)
        self._notes.setMaximumHeight(60)
        self._publish = QCheckBox("Publish result to hub")
        self._publish.setChecked(True)
        tip(self._publish, "When on, the evaluated value is written to the output channel every tick.")
        self._advanced = QCheckBox("Advanced (output id / notes)")
        self._advanced.toggled.connect(self._toggle_advanced)

        self._vars = QListWidget()
        tip(self._vars, "Each letter in your formula. Select a letter, pick a channel, then Bind.")
        self._channel_pick = QComboBox()
        self._channel_pick.setEditable(True)
        tip(
            self._channel_pick,
            "Live hub channels (from LSL / UDP). Choose one, then click Bind to selected letter.",
        )
        self._preview = QLabel("Preview: —")
        self._preview.setWordWrap(True)
        self._status = QLabel("")
        self._status.setWordWrap(True)

        new_btn = QPushButton("+ New")
        new_btn.clicked.connect(self.new_metric)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self.delete_metric)
        sync_btn = QPushButton("1. Read letters from formula")
        tip(sync_btn, "Scans the formula for letters (x, z, …) and builds the variable list.")
        sync_btn.clicked.connect(self.sync_vars)
        bind_btn = QPushButton("2. Bind channel → letter")
        tip(
            bind_btn,
            "Select a letter in the list, choose a channel in the dropdown, then click this. "
            "That letter now means that sensor value.",
        )
        bind_btn.clicked.connect(self.bind_selected)
        refresh_ch = QPushButton("Refresh channels")
        tip(refresh_ch, "Reload the channel dropdown from the live hub.")
        refresh_ch.clicked.connect(self.refresh_channel_combo)
        save_btn = QPushButton("Save profile…")
        save_btn.clicked.connect(self.save_profile)
        load_btn = QPushButton("Load profile…")
        load_btn.clicked.connect(self.load_profile)
        apply_btn = QPushButton("Apply")
        tip(apply_btn, "Save this metric into the live registry so it evaluates every tick.")
        apply_btn.clicked.connect(self.apply_working)

        left = QVBoxLayout()
        left.addWidget(QLabel("Metrics"))
        left.addWidget(self._list, 1)
        left_btns = QHBoxLayout()
        left_btns.addWidget(new_btn)
        left_btns.addWidget(del_btn)
        left.addLayout(left_btns)
        left.addWidget(load_btn)
        left.addWidget(save_btn)

        form = QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Output channel", self._output)
        form.addRow("Unit", self._unit)
        form.addRow("Formula", self._formula)
        form.addRow(self._publish)
        form.addRow(self._advanced)
        self._notes_label = QLabel("Notes")
        form.addRow(self._notes_label, self._notes)

        right = QVBoxLayout()
        right.addWidget(
            help_banner(
                "Workflow: write a formula → Read letters → select a letter → pick a channel → Bind → Apply. "
                "Example: y = x + z with x = heart rate and z = a custom sensor."
            )
        )
        right.addLayout(form)
        right.addWidget(QLabel("Letters in this formula"))
        right.addWidget(self._vars, 1)
        right.addWidget(QLabel("Channel to bind"))
        right.addWidget(self._channel_pick)
        var_btns = QHBoxLayout()
        var_btns.addWidget(sync_btn)
        var_btns.addWidget(bind_btn)
        var_btns.addWidget(refresh_ch)
        right.addLayout(var_btns)
        right.addWidget(apply_btn)
        right.addWidget(self._preview)
        right.addWidget(self._status)

        root = QHBoxLayout(self)
        root.addLayout(left, 1)
        root.addLayout(right, 2)

        self._toggle_advanced(False)
        self.reload_list()
        self.refresh_channel_combo()

    def _toggle_advanced(self, on: bool) -> None:
        self._output.setEnabled(on)
        self._notes.setVisible(on)
        self._notes_label.setVisible(on)

    def refresh_channel_combo(self) -> None:
        current = self._channel_pick.currentText()
        self._channel_pick.blockSignals(True)
        self._channel_pick.clear()
        ids = list(self.ctx.hub.channel_ids())
        for info in channel_catalog.catalog_list():
            if info.id not in ids:
                ids.append(info.id)
        for cid in ids:
            label = channel_catalog.label_for(cid)
            self._channel_pick.addItem(f"{cid}  —  {label}", cid)
        self._channel_pick.blockSignals(False)
        if current:
            idx = self._channel_pick.findData(current)
            if idx < 0:
                # try match by text prefix
                for i in range(self._channel_pick.count()):
                    if self._channel_pick.itemText(i).startswith(current):
                        idx = i
                        break
            if idx >= 0:
                self._channel_pick.setCurrentIndex(idx)

    def reload_list(self) -> None:
        self._list.clear()
        for d in self.ctx.registry.all_definitions():
            item = QListWidgetItem(d.display_name)
            item.setData(256, d.output_channel_id)
            self._list.addItem(item)

    def new_metric(self) -> None:
        name, ok = QInputDialog.getText(self, "New metric", "Display name:", text="New metric")
        if not ok:
            return
        defn = self.ctx.registry.create_new(name)
        self.reload_list()
        self._load_working(defn)

    def delete_metric(self) -> None:
        if self._working is None:
            return
        self.ctx.registry.remove(self._working.output_channel_id)
        self._working = None
        self.reload_list()
        self._clear_form()

    def _on_select(self, row: int) -> None:
        if row < 0:
            return
        item = self._list.item(row)
        if not item:
            return
        defn = self.ctx.registry.get_by_output(item.data(256))
        if defn:
            self._load_working(defn)

    def _clear_form(self) -> None:
        self._name.clear()
        self._output.clear()
        self._unit.clear()
        self._formula.clear()
        self._notes.clear()
        self._vars.clear()
        self._preview.setText("Preview: —")

    def _load_working(self, defn: MetricDefinition) -> None:
        self._working = defn.clone()
        self._name.setText(defn.display_name)
        self._output.setText(defn.output_channel_id)
        self._unit.setText(defn.output_unit)
        self._formula.setPlainText(defn.formula_expression)
        self._notes.setPlainText(defn.formula_notes)
        self._publish.setChecked(defn.publish_from_expression)
        self._rebuild_var_list()
        self.refresh_channel_combo()
        self.refresh_preview()

    def _rebuild_var_list(self) -> None:
        self._vars.clear()
        if not self._working:
            return
        for v in self._working.variables:
            if v.channel_id:
                text = f"{v.symbol}  ←  {v.channel_id}"
            else:
                text = f"{v.symbol}  ←  (not bound yet)"
            item = QListWidgetItem(text)
            item.setData(256, v.symbol)
            self._vars.addItem(item)

    def sync_vars(self) -> None:
        if not self._working:
            QMessageBox.information(self, "Metrics", "Create or select a metric first.")
            return
        formula = self._formula.toPlainText()
        symbols = extract_rhs_identifiers(formula)
        by_sym = {v.symbol: v for v in self._working.variables}
        ordered: list[MetricVariableBinding] = []
        for sym in symbols:
            if sym in by_sym:
                ordered.append(by_sym.pop(sym))
            else:
                ordered.append(MetricVariableBinding(symbol=sym))
        if self._advanced.isChecked():
            ordered.extend(by_sym.values())
        self._working.variables = ordered
        self._working.formula_expression = formula
        self._rebuild_var_list()
        self._status.setText(f"Found {len(ordered)} letter(s). Select one and Bind a channel.")

    def bind_selected(self) -> None:
        if not self._working:
            QMessageBox.information(self, "Bind", "Create or select a metric first.")
            return
        if self._vars.currentRow() < 0:
            QMessageBox.information(
                self,
                "Bind",
                "Select a letter in the list (e.g. x), then choose a channel, then Bind.",
            )
            return
        cid = self._channel_pick.currentData()
        if not cid:
            cid = self._channel_pick.currentText().split("—")[0].strip()
        if not cid:
            QMessageBox.information(self, "Bind", "Pick a channel from the dropdown.")
            return
        sym = self._vars.currentItem().data(256)
        for v in self._working.variables:
            if v.symbol == sym:
                v.channel_id = str(cid)
                v.description = channel_catalog.label_for(str(cid))
                break
        self._rebuild_var_list()
        self._status.setText(f"Bound {sym} ← {cid}")
        self.refresh_preview()

    def apply_working(self) -> None:
        if not self._working:
            return
        self._working.display_name = self._name.text().strip() or self._working.display_name
        self._working.output_channel_id = self._output.text().strip() or self._working.output_channel_id
        self._working.output_unit = self._unit.text().strip()
        self._working.formula_expression = self._formula.toPlainText().strip()
        self._working.formula_notes = self._notes.toPlainText()
        self._working.publish_from_expression = self._publish.isChecked()
        self.sync_vars()
        self.ctx.registry.upsert(self._working)
        self.reload_list()
        self._status.setText("Applied — metric will update from live channels")
        self.refresh_preview()

    def refresh_preview(self) -> None:
        if not self._working:
            self._preview.setText("Preview: —")
            return
        formula = self._formula.toPlainText()
        var_map = {
            v.symbol: self.ctx.hub.get_or_default(v.channel_id)
            for v in self._working.variables
            if v.symbol
        }
        sub = substitute(formula, var_map)
        ok, val = try_evaluate(formula, var_map)
        if ok:
            self._preview.setText(f"Preview: {sub}  →  {val:.4f}")
        else:
            self._preview.setText(f"Preview: {sub}  →  (need bindings / valid math)")

    def save_profile(self) -> None:
        self.apply_working()
        name, ok = QInputDialog.getText(self, "Save profile", "Profile name:", text="My metrics")
        if not ok or not name.strip():
            return
        path = PROFILES_DIR / f"{name.strip()}.json"
        profile = MetricProfile(profile_name=name.strip(), definitions=self.ctx.registry.all_definitions())
        save_metric_profile(path, profile)
        self._status.setText(f"Saved {path}")

    def load_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load metric profile", str(PROFILES_DIR), "JSON (*.json)"
        )
        if not path:
            return
        profile = load_metric_profile(path)
        self.ctx.registry.clear()
        self.ctx.registry.load_definitions(profile.definitions)
        self.reload_list()
        self._status.setText(f"Loaded {len(profile.definitions)} definition(s)")
