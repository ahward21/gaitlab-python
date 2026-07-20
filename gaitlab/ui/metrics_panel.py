"""Custom metric formula editor — labeled boxes for each step."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
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

        # --- left: metric list ---
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select)
        tip(self._list, "Saved custom metrics. Click + New, then edit the boxes on the right.")

        new_btn = QPushButton("+ New metric")
        new_btn.clicked.connect(self.new_metric)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self.delete_metric)
        load_btn = QPushButton("Load profile…")
        load_btn.clicked.connect(self.load_profile)
        save_btn = QPushButton("Save profile…")
        save_btn.clicked.connect(self.save_profile)

        left = QVBoxLayout()
        left.addWidget(QLabel("Your metrics"))
        left.addWidget(self._list, 1)
        lb = QHBoxLayout()
        lb.addWidget(new_btn)
        lb.addWidget(del_btn)
        left.addLayout(lb)
        left.addWidget(load_btn)
        left.addWidget(save_btn)

        # --- BOX A: name + formula ---
        self._name = QLineEdit()
        tip(self._name, "Display name for this metric.")
        self._unit = QLineEdit()
        self._unit.setPlaceholderText("e.g. m, bpm, —")
        self._output = QLineEdit()
        tip(self._output, "Hub id where the result is published (advanced).")
        self._formula = QTextEdit()
        self._formula.setAcceptRichText(False)
        self._formula.setMaximumHeight(90)
        self._formula.setPlaceholderText("Example:  y = sqrt(sq(x) + sq(z))")
        tip(
            self._formula,
            "Type math here. Letters like x,z are inputs you bind below.\n"
            "Functions: sqrt, sq, pow, abs, min, max, round, ^ or ** for power.\n"
            "Example: y = x^2    or    y = sqrt(x)",
        )
        self._publish = QCheckBox("Publish result live to Channels")
        self._publish.setChecked(True)
        self._advanced = QCheckBox("Show advanced fields")
        self._advanced.toggled.connect(self._toggle_advanced)
        self._notes = QTextEdit()
        self._notes.setAcceptRichText(False)
        self._notes.setMaximumHeight(50)
        self._notes_label = QLabel("Notes")

        box_a = QGroupBox("A — Name & formula  (type your equation in the Formula box)")
        form_a = QFormLayout(box_a)
        form_a.addRow("Name", self._name)
        form_a.addRow("Unit", self._unit)
        form_a.addRow("Formula", self._formula)
        form_a.addRow(self._publish)
        form_a.addRow(self._advanced)
        form_a.addRow("Output channel", self._output)
        form_a.addRow(self._notes_label, self._notes)
        fn_help = QLabel(
            "Math you can use: + − * /   ^ or **   sqrt(x)  sq(x)  pow(x,2)  abs(x)  min(a,b)  max(a,b)"
        )
        fn_help.setWordWrap(True)
        fn_help.setStyleSheet("color: #8a93a0; font-size: 11px;")
        form_a.addRow(fn_help)

        sync_btn = QPushButton("Find letters in formula →")
        tip(sync_btn, "Looks at the Formula box (A) and fills list B with letters that need a sensor.")
        sync_btn.clicked.connect(self.sync_vars)

        # --- BOX B: letters ---
        self._vars = QListWidget()
        tip(self._vars, "Click one letter here (e.g. x). Then pick a sensor in box C and press Bind.")
        box_b = QGroupBox("B — Letters that need a sensor  (click one row)")
        b_l = QVBoxLayout(box_b)
        b_l.addWidget(
            help_banner("After you edit the Formula, press “Find letters”. Then select x or z in this list.")
        )
        b_l.addWidget(self._vars, 1)
        b_l.addWidget(sync_btn)

        # --- BOX C: channel + bind ---
        self._channel_pick = QComboBox()
        self._channel_pick.setEditable(True)
        tip(self._channel_pick, "Pick which live sensor/channel feeds the letter selected in box B.")
        refresh_ch = QPushButton("Refresh list")
        refresh_ch.clicked.connect(self.refresh_channel_combo)
        bind_btn = QPushButton("Bind selected letter ← this channel")
        tip(bind_btn, "Connects the letter selected in B to the channel chosen in this dropdown.")
        bind_btn.clicked.connect(self.bind_selected)
        bind_btn.setStyleSheet("font-weight: bold;")

        box_c = QGroupBox("C — Sensor / channel to use for the selected letter")
        c_l = QVBoxLayout(box_c)
        c_l.addWidget(
            help_banner(
                "This list is filled from connected streams (LSL / Xsens). "
                "1) Select a letter in B  2) Choose a live channel here  3) Press Bind."
            )
        )
        c_row = QHBoxLayout()
        c_row.addWidget(self._channel_pick, 1)
        c_row.addWidget(refresh_ch)
        c_l.addLayout(c_row)
        c_l.addWidget(bind_btn)

        apply_btn = QPushButton("Apply metric")
        tip(apply_btn, "Saves this metric so it evaluates live and appears under Channels.")
        apply_btn.clicked.connect(self.apply_working)
        apply_btn.setStyleSheet("font-weight: bold;")

        self._preview = QLabel("Preview: —")
        self._preview.setWordWrap(True)
        self._status = QLabel("")
        self._status.setWordWrap(True)

        right = QVBoxLayout()
        right.addWidget(
            help_banner(
                "How to use this tab:  (A) write a formula using letters  →  "
                "(B) Find letters & click one  →  (C) pick a channel & Bind  →  Apply. "
                "Connect streams first so channels exist."
            )
        )
        right.addWidget(box_a)
        right.addWidget(box_b, 1)
        right.addWidget(box_c)
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
        self._output.setVisible(on)
        # find label for output — form row visibility: hide widget is enough
        self._notes.setVisible(on)
        self._notes_label.setVisible(on)

    def refresh_channel_combo(self) -> None:
        """Prefer live hub channels from connected streams; fall back to catalog."""
        current = self._channel_pick.currentData()
        self._channel_pick.blockSignals(True)
        self._channel_pick.clear()
        live = list(self.ctx.hub.channel_ids())
        if live:
            for cid in live:
                label = channel_catalog.label_for(cid)
                self._channel_pick.addItem(f"{cid}  —  {label}", cid)
        else:
            self._channel_pick.addItem("(connect a stream — live channels appear here)", "")
            for info in channel_catalog.catalog_list():
                self._channel_pick.addItem(f"{info.id}  —  {info.label} (catalog)", info.id)
        self._channel_pick.blockSignals(False)
        if current:
            idx = self._channel_pick.findData(current)
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
        self._status.setText("New metric created — edit Formula in box A, then Find letters.")

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
                text = f"{v.symbol}   ←   {v.channel_id}"
            else:
                text = f"{v.symbol}   ←   (pick a channel in box C, then Bind)"
            item = QListWidgetItem(text)
            item.setData(256, v.symbol)
            self._vars.addItem(item)

    def sync_vars(self) -> None:
        if not self._working:
            QMessageBox.information(self, "Metrics", "Click + New metric first (left list).")
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
        if ordered:
            self._vars.setCurrentRow(0)
            self._status.setText(
                f"Box B now lists: {', '.join(s.symbol for s in ordered)}. "
                "Select one, choose a channel in box C, press Bind."
            )
        else:
            self._status.setText("No letters found in the formula (only numbers/functions?).")

    def bind_selected(self) -> None:
        if not self._working:
            QMessageBox.information(self, "Bind", "Create a metric first (+ New).")
            return
        if self._vars.currentRow() < 0:
            QMessageBox.information(
                self,
                "Bind",
                "First click a letter in box B (e.g. the row that says “x”).\n"
                "Then choose a channel in box C and press Bind again.",
            )
            return
        cid = self._channel_pick.currentData()
        if not cid:
            cid = self._channel_pick.currentText().split("—")[0].strip()
        if not cid:
            QMessageBox.information(self, "Bind", "Choose a channel in the dropdown in box C.")
            return
        if not self.ctx.hub.channel_ids() and cid.startswith("lsl."):
            pass
        sym = self._vars.currentItem().data(256)
        for v in self._working.variables:
            if v.symbol == sym:
                v.channel_id = str(cid)
                v.description = channel_catalog.label_for(str(cid))
                break
        self._rebuild_var_list()
        # reselect same symbol
        for i in range(self._vars.count()):
            if self._vars.item(i).data(256) == sym:
                self._vars.setCurrentRow(i)
                break
        self._status.setText(f"Bound: in the formula, “{sym}” now means {cid}")
        self.refresh_preview()

    def apply_working(self) -> None:
        if not self._working:
            return
        self._working.display_name = self._name.text().strip() or self._working.display_name
        out = self._output.text().strip()
        if out:
            self._working.output_channel_id = out
        self._working.output_unit = self._unit.text().strip()
        self._working.formula_expression = self._formula.toPlainText().strip()
        self._working.formula_notes = self._notes.toPlainText()
        self._working.publish_from_expression = self._publish.isChecked()
        self.sync_vars()
        unbound = [v.symbol for v in self._working.variables if not v.channel_id]
        self.ctx.registry.upsert(self._working)
        self.reload_list()
        if unbound:
            self._status.setText(
                f"Applied, but still unbound: {', '.join(unbound)}. Bind them in boxes B+C."
            )
        else:
            self._status.setText("Applied — result will show under Channels when inputs are live.")
        self.refresh_preview()

    def refresh_preview(self) -> None:
        if not self._working:
            self._preview.setText("Preview: —")
            return
        formula = self._formula.toPlainText()
        var_map = {
            v.symbol: self.ctx.hub.get_or_default(v.channel_id)
            for v in self._working.variables
            if v.symbol and v.channel_id
        }
        sub = substitute(formula, var_map)
        ok, val = try_evaluate(formula, var_map)
        if ok:
            self._preview.setText(f"Preview: {sub}  →  {val:.4f}")
        else:
            self._preview.setText(f"Preview: {sub}  →  (bind letters / fix math)")

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
