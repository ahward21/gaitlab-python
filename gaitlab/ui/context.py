"""Shared application services for the desktop UI."""

from __future__ import annotations

from pathlib import Path

from gaitlab.hub import DataHub
from gaitlab.lsl.manager import LslManager
from gaitlab.metrics.evaluator import DerivedMetricEvaluator
from gaitlab.metrics.registry import MetricRegistry
from gaitlab.record.csv_recorder import CsvRecorder
from gaitlab.scripts.discovery import (
    DiscoveredOfflineAnalysis,
    DiscoveredScript,
    discover_offline_analyses,
    discover_scripts,
)
from gaitlab.scripts.offline_runner import OfflineRegistry
from gaitlab.scripts.runner import ScriptRegistry
from gaitlab.xsens.udp_source import XsensUdpSource

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PACKAGE_ROOT / "data"
MAPPINGS_DIR = DATA_DIR / "mappings"
PROFILES_DIR = DATA_DIR / "profiles"
SCRIPTS_DIR = DATA_DIR / "scripts"
USER_SCRIPTS_DIR = Path.home() / "GaitLabScripts"


class LabContext:
    def __init__(self) -> None:
        self.hub = DataHub()
        self.registry = MetricRegistry()
        self.lsl = LslManager(self.hub)
        self.xsens = XsensUdpSource(self.hub)
        self.evaluator = DerivedMetricEvaluator(self.hub, self.registry)
        self.recorder = CsvRecorder(self.hub)
        self.scripts = ScriptRegistry(self.hub)
        self.offline = OfflineRegistry()
        MAPPINGS_DIR.mkdir(parents=True, exist_ok=True)
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        USER_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    def discover_scripts(self) -> list[DiscoveredScript]:
        """Walk both script folders and return the DiscoveredScript entries.

        Caller is responsible for feeding the ``cls`` list into ``self.scripts``.
        """
        return discover_scripts(package_folder=SCRIPTS_DIR, user_folder=USER_SCRIPTS_DIR)

    def discover_offline_analyses(self) -> list[DiscoveredOfflineAnalysis]:
        """Walk both script folders and return the DiscoveredOfflineAnalysis
        entries. Same folders as ``discover_scripts`` — a single .py file
        can host both online and offline analyses.

        Caller is responsible for feeding the ``cls`` list into
        ``self.offline`` (``self.offline.clear_discovered()`` +
        ``register_classes([...])``).
        """
        return discover_offline_analyses(
            package_folder=SCRIPTS_DIR, user_folder=USER_SCRIPTS_DIR
        )
