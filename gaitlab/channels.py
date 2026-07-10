"""Channel catalog + custom.* id allocation (mirrors Unity MetricChannels)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelInfo:
    id: str
    label: str
    unit: str = ""
    category: str = "Custom"


CAT_PHYSIO = "Physiology"
CAT_LSL = "Live / LSL"
CAT_CUSTOM = "Custom metrics"
CAT_DERIVED = "Derived"

# Built-in labels for known ids (raw LSL v1 — no gait.* / stride.* derivation).
_BUILTIN: list[ChannelInfo] = [
    ChannelInfo("physio.heart_rate_bpm", "Heart rate", "bpm", CAT_PHYSIO),
    ChannelInfo("physio.heart_rate_lsl_bpm", "Heart rate (LSL)", "bpm", CAT_PHYSIO),
    ChannelInfo("physio.resp_rate_bpm", "Respiration rate", "bpm", CAT_PHYSIO),
]

_user: dict[str, ChannelInfo] = {}
_live: dict[str, ChannelInfo] = {}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return s or "metric"


def register_user_channel(channel_id: str, label: str, unit: str = "", category: str = CAT_CUSTOM) -> None:
    if not channel_id:
        return
    _user[channel_id.lower()] = ChannelInfo(channel_id, label or channel_id, unit or "", category)


def register_live_channel(channel_id: str, label: str = "", unit: str = "") -> None:
    if not channel_id:
        return
    key = channel_id.lower()
    if key in {c.id.lower() for c in _BUILTIN} or key in _user:
        return
    _live[key] = ChannelInfo(channel_id, label or channel_id, unit or "", CAT_LSL)


def allocate_user_channel_id(display_name: str) -> str:
    base = f"custom.{_slug(display_name)}"
    if base.lower() not in _user and base.lower() not in {c.id.lower() for c in _BUILTIN}:
        return base
    i = 2
    while True:
        cand = f"{base}_{i}"
        if cand.lower() not in _user:
            return cand
        i += 1


def label_for(channel_id: str) -> str:
    info = try_get_info(channel_id)
    return info.label if info else channel_id


def unit_for(channel_id: str) -> str:
    info = try_get_info(channel_id)
    return info.unit if info else ""


def try_get_info(channel_id: str) -> ChannelInfo | None:
    if not channel_id:
        return None
    key = channel_id.lower()
    for c in _BUILTIN:
        if c.id.lower() == key:
            return c
    if key in _user:
        return _user[key]
    if key in _live:
        return _live[key]
    return None


def catalog_list() -> list[ChannelInfo]:
    out = list(_BUILTIN)
    out.extend(_user.values())
    out.extend(_live.values())
    return out


def clear_live() -> None:
    _live.clear()


def clear_user() -> None:
    _user.clear()
