"""Metric definition types (mirrors Unity MetricDefinition / MetricVariableBinding)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MetricVariableBinding:
    symbol: str = "x"
    description: str = ""
    channel_id: str = ""
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "Symbol": self.symbol,
            "Description": self.description,
            "ChannelId": self.channel_id,
            "Unit": self.unit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MetricVariableBinding:
        data = data or {}
        return cls(
            symbol=str(data.get("Symbol", data.get("symbol", "x")) or "x"),
            description=str(data.get("Description", data.get("description", "")) or ""),
            channel_id=str(data.get("ChannelId", data.get("channel_id", "")) or ""),
            unit=str(data.get("Unit", data.get("unit", "")) or ""),
        )


@dataclass
class MetricDefinition:
    id: str = ""
    display_name: str = "New metric"
    output_channel_id: str = ""
    output_unit: str = ""
    trigger_event: str = "Every frame"
    formula_expression: str = "y = x"
    formula_notes: str = ""
    publish_from_expression: bool = True
    variables: list[MetricVariableBinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "Id": self.id,
            "DisplayName": self.display_name,
            "OutputChannelId": self.output_channel_id,
            "OutputUnit": self.output_unit,
            "TriggerEvent": self.trigger_event,
            "FormulaExpression": self.formula_expression,
            "FormulaNotes": self.formula_notes,
            "PublishFromExpression": self.publish_from_expression,
            "Variables": [v.to_dict() for v in self.variables],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MetricDefinition:
        data = data or {}
        vars_raw = data.get("Variables", data.get("variables", [])) or []
        return cls(
            id=str(data.get("Id", data.get("id", "")) or ""),
            display_name=str(data.get("DisplayName", data.get("display_name", "New metric")) or "New metric"),
            output_channel_id=str(data.get("OutputChannelId", data.get("output_channel_id", "")) or ""),
            output_unit=str(data.get("OutputUnit", data.get("output_unit", "")) or ""),
            trigger_event=str(data.get("TriggerEvent", data.get("trigger_event", "Every frame")) or "Every frame"),
            formula_expression=str(
                data.get("FormulaExpression", data.get("formula_expression", "y = x")) or "y = x"
            ),
            formula_notes=str(data.get("FormulaNotes", data.get("formula_notes", "")) or ""),
            publish_from_expression=bool(
                data.get("PublishFromExpression", data.get("publish_from_expression", True))
            ),
            variables=[MetricVariableBinding.from_dict(v) for v in vars_raw],
        )

    def clone(self) -> MetricDefinition:
        return MetricDefinition.from_dict(self.to_dict())
