"""Evaluate publish_from_expression metrics into the DataHub."""

from __future__ import annotations

from gaitlab.hub import DataHub
from gaitlab.metrics.expression import try_evaluate
from gaitlab.metrics.registry import MetricRegistry


class DerivedMetricEvaluator:
    def __init__(self, hub: DataHub, registry: MetricRegistry) -> None:
        self.hub = hub
        self.registry = registry

    def tick(self) -> None:
        for defn in self.registry.all_definitions():
            if not defn.publish_from_expression:
                continue
            if not defn.output_channel_id or not defn.formula_expression:
                continue
            var_map: dict[str, float] = {}
            for v in defn.variables:
                if not v.symbol:
                    continue
                var_map[v.symbol] = self.hub.get_or_default(v.channel_id)
            ok, value = try_evaluate(defn.formula_expression, var_map)
            if ok:
                self.hub.publish(defn.output_channel_id, value, defn.output_unit or "")
