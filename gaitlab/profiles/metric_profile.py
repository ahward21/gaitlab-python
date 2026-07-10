"""Metric profile JSON (VisualizationProfile-lite: Definitions only)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gaitlab.metrics.definitions import MetricDefinition


@dataclass
class MetricProfile:
    profile_name: str = "Untitled metrics"
    definitions: list[MetricDefinition] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ProfileName": self.profile_name,
            "Definitions": [d.to_dict() for d in self.definitions],
            "Rules": [],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MetricProfile:
        data = data or {}
        defs = [
            MetricDefinition.from_dict(d)
            for d in (data.get("Definitions", data.get("definitions", [])) or [])
        ]
        return cls(
            profile_name=str(data.get("ProfileName", data.get("profile_name", "Untitled metrics")) or "Untitled metrics"),
            definitions=defs,
        )


def load_metric_profile(path: Path | str) -> MetricProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return MetricProfile.from_dict(data)


def save_metric_profile(path: Path | str, profile: MetricProfile) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
