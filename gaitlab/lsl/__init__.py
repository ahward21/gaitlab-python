from gaitlab.lsl.manager import DiscoveredStream, LslManager
from gaitlab.lsl.mappings import (
    PRESET_HR,
    PRESET_MOCAP,
    ChannelMapEntry,
    StreamMapping,
    auto_map_channels,
    load_mappings,
    save_mappings,
)

__all__ = [
    "LslManager",
    "DiscoveredStream",
    "StreamMapping",
    "ChannelMapEntry",
    "PRESET_HR",
    "PRESET_MOCAP",
    "auto_map_channels",
    "load_mappings",
    "save_mappings",
]
