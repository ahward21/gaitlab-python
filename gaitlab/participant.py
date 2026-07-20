"""Participant / session info for recording metadata."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Participant:
    participant_id: str = "P01"
    name: str = ""
    age: str = ""
    height_cm: str = ""
    weight_kg: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Participant:
        data = data or {}
        return cls(
            participant_id=str(data.get("participant_id", data.get("id", "P01")) or "P01"),
            name=str(data.get("name", "") or ""),
            age=str(data.get("age", "") or ""),
            height_cm=str(data.get("height_cm", "") or ""),
            weight_kg=str(data.get("weight_kg", "") or ""),
            notes=str(data.get("notes", "") or ""),
        )

    def slug(self) -> str:
        raw = (self.participant_id or "P01").strip() or "P01"
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", raw)


@dataclass
class SessionInfo:
    session_name: str = "session"
    task: str = ""  # e.g. walk, run, trial_1
    condition: str = ""  # e.g. shoes_A
    notes: str = ""
    participant: Participant = field(default_factory=Participant)

    def filename_base(self) -> str:
        parts = [self.participant.slug()]
        sess = re.sub(r"[^a-zA-Z0-9_-]+", "_", (self.session_name or "session").strip()) or "session"
        parts.append(sess)
        if self.task.strip():
            parts.append(re.sub(r"[^a-zA-Z0-9_-]+", "_", self.task.strip()))
        return "_".join(parts)

    def metadata(self) -> dict[str, str]:
        p = self.participant
        return {
            "participant_id": p.participant_id,
            "participant_name": p.name,
            "age": p.age,
            "height_cm": p.height_cm,
            "weight_kg": p.weight_kg,
            "participant_notes": p.notes,
            "session_name": self.session_name,
            "task": self.task,
            "condition": self.condition,
            "session_notes": self.notes,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_name": self.session_name,
            "task": self.task,
            "condition": self.condition,
            "notes": self.notes,
            "participant": self.participant.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SessionInfo:
        data = data or {}
        return cls(
            session_name=str(data.get("session_name", "session") or "session"),
            task=str(data.get("task", "") or ""),
            condition=str(data.get("condition", "") or ""),
            notes=str(data.get("notes", "") or ""),
            participant=Participant.from_dict(data.get("participant")),
        )


def save_participant(path: Path | str, participant: Participant) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(participant.to_dict(), indent=2), encoding="utf-8")


def load_participant(path: Path | str) -> Participant:
    return Participant.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
