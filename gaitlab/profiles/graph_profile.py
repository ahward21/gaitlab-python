"""GraphProfile-compatible JSON (Unity GraphProfile shape + Python extras)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_SERIES = 6


@dataclass
class GraphPanel:
    title: str = "Panel"
    channel_ids: list[str] = field(default_factory=list)
    colors_hex: list[str] = field(default_factory=list)
    y_range_mode: str = "auto"  # auto | fixed
    y_min: float = 0.0
    y_max: float = 0.0
    display_mode: str = "line"
    # Python extras (ignored by Unity if present)
    plot_mode: str = "time"  # time | xy
    x_channel_id: str = ""  # used when plot_mode == xy
    window_seconds: float = 30.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "Title": self.title,
            "ChannelIds": list(self.channel_ids)[:MAX_SERIES],
            "ColorsHex": list(self.colors_hex),
            "YRangeMode": self.y_range_mode,
            "YMin": self.y_min,
            "YMax": self.y_max,
            "DisplayMode": self.display_mode,
            "ThresholdBands": [],
            "PlotMode": self.plot_mode,
            "XChannelId": self.x_channel_id,
            "WindowSeconds": self.window_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> GraphPanel:
        data = data or {}
        channels = list(data.get("ChannelIds", data.get("channel_ids", [])) or [])[:MAX_SERIES]
        return cls(
            title=str(data.get("Title", data.get("title", "Panel")) or "Panel"),
            channel_ids=channels,
            colors_hex=list(data.get("ColorsHex", data.get("colors_hex", [])) or []),
            y_range_mode=str(data.get("YRangeMode", data.get("y_range_mode", "auto")) or "auto"),
            y_min=float(data.get("YMin", data.get("y_min", 0.0)) or 0.0),
            y_max=float(data.get("YMax", data.get("y_max", 0.0)) or 0.0),
            display_mode=str(data.get("DisplayMode", data.get("display_mode", "line")) or "line"),
            plot_mode=str(data.get("PlotMode", data.get("plot_mode", "time")) or "time"),
            x_channel_id=str(data.get("XChannelId", data.get("x_channel_id", "")) or ""),
            window_seconds=float(data.get("WindowSeconds", data.get("window_seconds", 30.0)) or 30.0),
        )


@dataclass
class GraphProfile:
    profile_name: str = "Untitled"
    panels: list[GraphPanel] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ProfileName": self.profile_name,
            "Panels": [p.to_dict() for p in self.panels],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> GraphProfile:
        data = data or {}
        panels = [GraphPanel.from_dict(p) for p in (data.get("Panels", data.get("panels", [])) or [])]
        return cls(
            profile_name=str(data.get("ProfileName", data.get("profile_name", "Untitled")) or "Untitled"),
            panels=panels,
        )


def load_graph_profile(path: Path | str) -> GraphProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return GraphProfile.from_dict(data)


def save_graph_profile(path: Path | str, profile: GraphProfile) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
