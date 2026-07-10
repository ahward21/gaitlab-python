"""Unit tests for MetricExpression port."""

from gaitlab.metrics.expression import extract_rhs_identifiers, substitute, try_evaluate


def test_try_evaluate_basic():
    ok, val = try_evaluate("1 + 2 * 3", {})
    assert ok
    assert abs(val - 7.0) < 1e-6


def test_try_evaluate_parens_and_vars():
    ok, val = try_evaluate("y = (a + b) / 2", {"a": 10.0, "b": 4.0})
    assert ok
    assert abs(val - 7.0) < 1e-6


def test_try_evaluate_unary_and_div():
    ok, val = try_evaluate("-x * 2", {"x": 3.0})
    assert ok
    assert abs(val - (-6.0)) < 1e-6


def test_try_evaluate_missing_var():
    ok, _ = try_evaluate("x + y", {"x": 1.0})
    assert not ok


def test_substitute():
    s = substitute("y = x + z", {"x": 1.5, "z": 2.0})
    assert "1.500" in s
    assert "2.000" in s


def test_extract_rhs_identifiers():
    ids = extract_rhs_identifiers("y = x + Z + foot_x")
    assert ids == ["x", "Z", "foot_x"]


def test_extract_no_equals():
    ids = extract_rhs_identifiers("a * b")
    assert ids == ["a", "b"]
