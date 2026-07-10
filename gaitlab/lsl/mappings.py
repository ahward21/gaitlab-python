"""LSL stream → hub channel mapping (JSON)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ChannelMapEntry:
    sample_index: int
    hub_channel_id: str
    unit: str = ""
    scale: float = 1.0
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_index": self.sample_index,
            "hub_channel_id": self.hub_channel_id,
            "unit": self.unit,
            "scale": self.scale,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChannelMapEntry:
        return cls(
            sample_index=int(data.get("sample_index", 0)),
            hub_channel_id=str(data.get("hub_channel_id", "")),
            unit=str(data.get("unit", "") or ""),
            scale=float(data.get("scale", 1.0) or 1.0),
            label=str(data.get("label", "") or ""),
        )


@dataclass
class StreamMapping:
    stream_name: str = ""
    stream_type: str = ""
    # If empty, auto-map every channel as lsl.<slug>.chNN
    channels: list[ChannelMapEntry] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_name": self.stream_name,
            "stream_type": self.stream_type,
            "enabled": self.enabled,
            "channels": [c.to_dict() for c in self.channels],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamMapping:
        ch = [ChannelMapEntry.from_dict(x) for x in (data.get("channels") or [])]
        return cls(
            stream_name=str(data.get("stream_name", "") or ""),
            stream_type=str(data.get("stream_type", "") or ""),
            enabled=bool(data.get("enabled", True)),
            channels=ch,
        )


def slug_stream(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "stream").strip().lower()).strip("_")
    return s or "stream"


def default_channel_id(stream_name: str, index: int) -> str:
    return f"lsl.{slug_stream(stream_name)}.ch{index:03d}"


def auto_map_channels(stream_name: str, channel_count: int, unit: str = "") -> list[ChannelMapEntry]:
    return [
        ChannelMapEntry(
            sample_index=i,
            hub_channel_id=default_channel_id(stream_name, i),
            unit=unit,
            scale=1.0,
            label=f"{stream_name}[{i}]",
        )
        for i in range(max(0, channel_count))
    ]


PRESET_MOCAP = StreamMapping(stream_name="XsensMVN", stream_type="MoCap", channels=[])
PRESET_HR = StreamMapping(
    stream_name="HeartRate",
    stream_type="HR",
    channels=[
        ChannelMapEntry(0, "physio.heart_rate_bpm", "bpm", 1.0, "Heart rate"),
        ChannelMapEntry(0, "physio.heart_rate_lsl_bpm", "bpm", 1.0, "Heart rate (LSL)"),
    ],
)


def load_mappings(path: Path | str) -> list[StreamMapping]:
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "streams" in data:
        items = data["streams"]
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [StreamMapping.from_dict(x) for x in items]


def save_mappings(path: Path | str, mappings: list[StreamMapping]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"streams": [m.to_dict() for m in mappings]}
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
