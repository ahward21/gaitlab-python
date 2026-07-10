"""Shared application services for the desktop UI."""

from __future__ import annotations

from pathlib import Path

from gaitlab.hub import DataHub
from gaitlab.lsl.manager import LslManager
from gaitlab.metrics.evaluator import DerivedMetricEvaluator
from gaitlab.metrics.registry import MetricRegistry
from gaitlab.record.csv_recorder import CsvRecorder
from gaitlab.xsens.udp_source import XsensUdpSource

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PACKAGE_ROOT / "data"
MAPPINGS_DIR = DATA_DIR / "mappings"
PROFILES_DIR = DATA_DIR / "profiles"


class LabContext:
    def __init__(self) -> None:
        self.hub = DataHub()
        self.registry = MetricRegistry()
        self.lsl = LslManager(self.hub)
        self.xsens = XsensUdpSource(self.hub)
        self.evaluator = DerivedMetricEvaluator(self.hub, self.registry)
        self.recorder = CsvRecorder(self.hub)
        MAPPINGS_DIR.mkdir(parents=True, exist_ok=True)
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
