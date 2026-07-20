"""Live hub channel table."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gaitlab import channels
from gaitlab.ui.context import LabContext
from gaitlab.ui.help import help_banner, tip


class ChannelsPanel(QWidget):
    def __init__(self, ctx: LabContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("filter channels…")
        self._filter.textChanged.connect(self.refresh)
        tip(self._filter, "Filter by channel id or label.")

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Channel", "Label", "Value", "Unit"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tip(self._table, "Live raw values from LSL / UDP. Use Metrics tab to build formulas from these.")

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)

        bar = QHBoxLayout()
        bar.addWidget(self._filter, 1)
        bar.addWidget(refresh_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Live hub channels"))
        layout.addWidget(
            help_banner(
                "Live channels from whatever you connected (LSL / Xsens UDP) plus custom metrics. "
                "Connect an Xsens suit and every segment channel (xsens.segNN.x/y/z, or LSL ch000…) "
                "appears here as data arrives. Record from the top bar to save CSV."
            )
        )
        layout.addLayout(bar)
        layout.addWidget(self._table, 1)

    def selected_channel_id(self) -> str | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._table.item(rows[0].row(), 0)
        return item.text() if item else None

    def refresh(self) -> None:
        filt = (self._filter.text() or "").strip().lower()
        # Only channels that exist on the hub (connected streams + applied metrics).
        snap = self.ctx.hub.snapshot_with_units()
        ids = list(snap.keys())

        selected = self.selected_channel_id()
        # In-place update when row set is unchanged (keeps selection).
        existing = [
            self._table.item(r, 0).text()
            for r in range(self._table.rowCount())
            if self._table.item(r, 0) is not None
        ]
        wanted: list[str] = []
        for cid in ids:
            label = channels.label_for(cid)
            if filt and filt not in cid.lower() and filt not in label.lower():
                continue
            wanted.append(cid)

        if existing == wanted:
            for row, cid in enumerate(wanted):
                if cid in snap:
                    val, unit = snap[cid]
                    val_s = f"{val:.4f}"
                    unit = unit or channels.unit_for(cid)
                else:
                    val_s = "—"
                    unit = channels.unit_for(cid)
                self._table.item(row, 2).setText(val_s)
                self._table.item(row, 3).setText(unit)
            return

        self._table.setRowCount(0)
        restore_row = -1
        for cid in wanted:
            label = channels.label_for(cid)
            if cid in snap:
                val, unit = snap[cid]
                val_s = f"{val:.4f}"
                unit = unit or channels.unit_for(cid)
            else:
                val_s = "—"
                unit = channels.unit_for(cid)
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(cid))
            self._table.setItem(row, 1, QTableWidgetItem(label))
            self._table.setItem(row, 2, QTableWidgetItem(val_s))
            self._table.setItem(row, 3, QTableWidgetItem(unit))
            if selected and cid == selected:
                restore_row = row
        if restore_row >= 0:
            self._table.selectRow(restore_row)
