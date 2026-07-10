"""User metric registry."""

from __future__ import annotations

from gaitlab import channels
from gaitlab.metrics.definitions import MetricDefinition, MetricVariableBinding


class MetricRegistry:
    def __init__(self) -> None:
        self._by_output: dict[str, MetricDefinition] = {}

    def all_definitions(self) -> list[MetricDefinition]:
        return [d.clone() for d in self._by_output.values()]

    def get_by_output(self, output_channel_id: str) -> MetricDefinition | None:
        if not output_channel_id:
            return None
        d = self._by_output.get(output_channel_id.lower())
        return d.clone() if d else None

    def upsert(self, definition: MetricDefinition) -> None:
        if definition is None or not definition.output_channel_id:
            return
        key = definition.output_channel_id.lower()
        self._by_output[key] = definition.clone()
        channels.register_user_channel(
            definition.output_channel_id,
            definition.display_name or definition.output_channel_id,
            definition.output_unit or "",
            channels.CAT_CUSTOM,
        )

    def remove(self, output_channel_id: str) -> None:
        if not output_channel_id:
            return
        self._by_output.pop(output_channel_id.lower(), None)

    def clear(self) -> None:
        self._by_output.clear()

    def create_new(self, display_name: str = "New metric", unit: str = "") -> MetricDefinition:
        label = (display_name or "New metric").strip() or "New metric"
        channel_id = channels.allocate_user_channel_id(label)
        defn = MetricDefinition(
            id=channel_id.replace(".", "_"),
            display_name=label,
            output_channel_id=channel_id,
            output_unit=unit or "",
            trigger_event="Every frame",
            formula_expression="y = x",
            formula_notes="Map variables to LSL / hub channels, then write the equation.",
            publish_from_expression=True,
            variables=[],
        )
        self.upsert(defn)
        return defn.clone()

    def load_definitions(self, definitions: list[MetricDefinition]) -> None:
        for d in definitions:
            self.upsert(d)
