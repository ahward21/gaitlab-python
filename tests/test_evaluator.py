from gaitlab.metrics.definitions import MetricDefinition, MetricVariableBinding
from gaitlab.metrics.registry import MetricRegistry
from gaitlab.metrics.evaluator import DerivedMetricEvaluator
from gaitlab.hub import DataHub


def test_registry_create_and_evaluate():
    hub = DataHub()
    hub.publish("a", 10.0)
    hub.publish("b", 5.0)
    reg = MetricRegistry()
    defn = reg.create_new("Sum AB")
    defn.formula_expression = "y = x + z"
    defn.variables = [
        MetricVariableBinding("x", channel_id="a"),
        MetricVariableBinding("z", channel_id="b"),
    ]
    defn.publish_from_expression = True
    reg.upsert(defn)

    DerivedMetricEvaluator(hub, reg).tick()
    out = hub.get_or_default(defn.output_channel_id)
    assert abs(out - 15.0) < 1e-6
