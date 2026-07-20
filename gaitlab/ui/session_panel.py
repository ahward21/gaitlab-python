"""Participant + session form and recording output settings."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gaitlab.participant import Participant, SessionInfo, load_participant, save_participant
from gaitlab.ui.context import LabContext, DATA_DIR
from gaitlab.ui.help import help_banner, tip

PARTICIPANTS_DIR = DATA_DIR / "participants"


class SessionPanel(QWidget):
    def __init__(self, ctx: LabContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        PARTICIPANTS_DIR.mkdir(parents=True, exist_ok=True)

        self._pid = QLineEdit("P01")
        self._pname = QLineEdit()
        self._age = QLineEdit()
        self._height = QLineEdit()
        self._weight = QLineEdit()
        self._pnotes = QTextEdit()
        self._pnotes.setAcceptRichText(False)
        self._pnotes.setMaximumHeight(50)

        self._session = QLineEdit("session")
        self._task = QLineEdit()
        self._task.setPlaceholderText("e.g. overground_walk")
        self._condition = QLineEdit()
        self._condition.setPlaceholderText("e.g. barefoot / shoe_A")
        self._snotes = QTextEdit()
        self._snotes.setAcceptRichText(False)
        self._snotes.setMaximumHeight(50)

        tip(self._pid, "Participant ID used in CSV filenames (e.g. P01).")
        tip(self._session, "Session label for this capture (trial / day / block).")
        tip(self._task, "What the participant is doing in this recording.")
        tip(self._condition, "Experimental condition / footwear / intervention.")

        p_box = QGroupBox("Participant")
        p_form = QFormLayout(p_box)
        p_form.addRow("ID", self._pid)
        p_form.addRow("Name", self._pname)
        p_form.addRow("Age", self._age)
        p_form.addRow("Height (cm)", self._height)
        p_form.addRow("Weight (kg)", self._weight)
        p_form.addRow("Notes", self._pnotes)

        s_box = QGroupBox("This recording session")
        s_form = QFormLayout(s_box)
        s_form.addRow("Session name", self._session)
        s_form.addRow("Task", self._task)
        s_form.addRow("Condition", self._condition)
        s_form.addRow("Session notes", self._snotes)

        # Recording output controls
        self._output_name = QLineEdit()
        self._output_name.setPlaceholderText("Leave blank to auto-name from participant + session")
        tip(
            self._output_name,
            "Optional custom file name (without path/extension). "
            "Example: Pilot_P01_trial3 → GaitLab_Pilot_P01_trial3_<timestamp>.csv",
        )

        self._rate_mode = QComboBox()
        self._rate_mode.addItem("Frames per second (Hz)", "hz")
        self._rate_mode.addItem("Every N seconds (interval)", "interval")
        tip(
            self._rate_mode,
            "How often to write a CSV row.\n"
            "• Hz: e.g. 50 = about 50 rows/second\n"
            "• Interval: e.g. 1.0 = one row every second",
        )
        self._rate_mode.currentIndexChanged.connect(self._on_rate_mode)

        self._rate_value = QDoubleSpinBox()
        self._rate_value.setDecimals(3)
        self._rate_value.setRange(0.001, 1000.0)
        self._rate_value.setValue(50.0)
        tip(self._rate_value, "Sample rate value — meaning depends on the mode above.")

        r_box = QGroupBox("Recording output")
        r_form = QFormLayout(r_box)
        r_form.addRow("Output file name", self._output_name)
        r_form.addRow("Sample rate mode", self._rate_mode)
        r_form.addRow("Rate value", self._rate_value)
        self._rate_hint = QLabel("Writing ≈ 50 rows per second.")
        self._rate_hint.setStyleSheet("color: #8a93a0; font-size: 11px;")
        r_form.addRow(self._rate_hint)
        self._rate_value.valueChanged.connect(self._update_rate_hint)

        save_btn = QPushButton("Save participant…")
        save_btn.clicked.connect(self.save_participant_file)
        load_btn = QPushButton("Load participant…")
        load_btn.clicked.connect(self.load_participant_file)
        row = QHBoxLayout()
        row.addWidget(load_btn)
        row.addWidget(save_btn)
        row.addStretch(1)

        self._preview = QLabel("")
        self._preview.setWordWrap(True)
        for w in (self._pid, self._session, self._task, self._condition, self._output_name):
            w.textChanged.connect(self._update_preview)
        # After _preview exists — rate mode can update preview safely.
        self._on_rate_mode()
        self._update_preview()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Participant & session"))
        layout.addWidget(
            help_banner(
                "Set participant + session, then optionally name the CSV and choose how often to sample. "
                "Record writes under ~/GaitLabCaptures/."
            )
        )
        layout.addWidget(p_box)
        layout.addLayout(row)
        layout.addWidget(s_box)
        layout.addWidget(r_box)
        layout.addWidget(self._preview)
        layout.addStretch(1)

    def _on_rate_mode(self) -> None:
        mode = self._rate_mode.currentData()
        if mode == "interval":
            if self._rate_value.value() > 60:
                self._rate_value.setValue(1.0)
            self._rate_value.setSuffix(" s")
        else:
            if self._rate_value.value() < 0.1:
                self._rate_value.setValue(50.0)
            self._rate_value.setSuffix(" Hz")
        self._update_rate_hint()
        self._update_preview()

    def _update_rate_hint(self) -> None:
        mode = self._rate_mode.currentData()
        v = self._rate_value.value()
        if mode == "interval":
            self._rate_hint.setText(f"Writing one CSV row every {v:g} second(s).")
        else:
            self._rate_hint.setText(f"Writing ≈ {v:g} rows per second.")

    def session_info(self) -> SessionInfo:
        return SessionInfo(
            session_name=self._session.text().strip() or "session",
            task=self._task.text().strip(),
            condition=self._condition.text().strip(),
            notes=self._snotes.toPlainText().strip(),
            participant=Participant(
                participant_id=self._pid.text().strip() or "P01",
                name=self._pname.text().strip(),
                age=self._age.text().strip(),
                height_cm=self._height.text().strip(),
                weight_kg=self._weight.text().strip(),
                notes=self._pnotes.toPlainText().strip(),
            ),
        )

    def recording_options(self) -> dict:
        return {
            "output_name": self._output_name.text().strip(),
            "sample_mode": str(self._rate_mode.currentData() or "hz"),
            "sample_value": float(self._rate_value.value()),
        }

    def _update_preview(self) -> None:
        info = self.session_info()
        opts = self.recording_options()
        base = opts["output_name"] or info.filename_base()
        mode = opts["sample_mode"]
        v = opts["sample_value"]
        rate = f"every {v:g}s" if mode == "interval" else f"{v:g} Hz"
        self._preview.setText(f"Record → GaitLab_{base}_<timestamp>.csv   ·   sample {rate}")

    def save_participant_file(self) -> None:
        info = self.session_info()
        path = PARTICIPANTS_DIR / f"{info.participant.slug()}.json"
        save_participant(path, info.participant)
        self._preview.setText(f"Saved participant → {path}")

    def load_participant_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load participant", str(PARTICIPANTS_DIR), "JSON (*.json)"
        )
        if not path:
            return
        p = load_participant(path)
        self._pid.setText(p.participant_id)
        self._pname.setText(p.name)
        self._age.setText(p.age)
        self._height.setText(p.height_cm)
        self._weight.setText(p.weight_kg)
        self._pnotes.setPlainText(p.notes)
        self._update_preview()
