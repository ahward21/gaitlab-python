"""Arithmetic formula language — port of Unity MetricExpression."""

from __future__ import annotations

from typing import Mapping


def normalize_expression(expression: str) -> str:
    """Strip optional ``lhs =`` prefix; evaluate RHS only."""
    if not expression:
        return ""
    eq = expression.find("=")
    if eq >= 0:
        return expression[eq + 1 :].strip()
    return expression.strip()


def try_evaluate(expression: str, variables: Mapping[str, float] | None = None) -> tuple[bool, float]:
    expr = normalize_expression(expression)
    if not expr:
        return False, 0.0
    try:
        index = 0
        result, index = _parse_expression(expr, index, variables or {})
        index = _skip_ws(expr, index)
        if index < len(expr):
            return False, 0.0
        return True, float(result)
    except Exception:
        return False, 0.0


def substitute(expression: str, variables: Mapping[str, float] | None = None) -> str:
    if not expression or not expression.strip():
        return ""
    variables = variables or {}
    parts: list[str] = []
    i = 0
    s = expression
    while i < len(s):
        c = s[i]
        if c.isalpha() or c == "_":
            start = i
            i += 1
            while i < len(s) and (s[i].isalnum() or s[i] == "_"):
                i += 1
            sym = s[start:i]
            if sym in variables:
                parts.append(f"{variables[sym]:.3f}")
            else:
                parts.append(sym)
        else:
            parts.append(c)
            i += 1
    return "".join(parts)


def extract_rhs_identifiers(expression: str) -> list[str]:
    if not expression or not expression.strip():
        return []
    rhs = expression
    eq = expression.find("=")
    if eq >= 0:
        rhs = expression[eq + 1 :]
    seen: set[str] = set()
    result: list[str] = []
    i = 0
    while i < len(rhs):
        c = rhs[i]
        if c.isalpha() or c == "_":
            start = i
            i += 1
            while i < len(rhs) and (rhs[i].isalnum() or rhs[i] == "_"):
                i += 1
            sym = rhs[start:i]
            if sym not in seen:
                seen.add(sym)
                result.append(sym)
        else:
            i += 1
    return result


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _parse_expression(s: str, i: int, vars_: Mapping[str, float]) -> tuple[float, int]:
    value, i = _parse_term(s, i, vars_)
    while True:
        i = _skip_ws(s, i)
        if i >= len(s):
            break
        op = s[i]
        if op == "+":
            i += 1
            rhs, i = _parse_term(s, i, vars_)
            value += rhs
        elif op == "-":
            i += 1
            rhs, i = _parse_term(s, i, vars_)
            value -= rhs
        else:
            break
    return value, i


def _parse_term(s: str, i: int, vars_: Mapping[str, float]) -> tuple[float, int]:
    value, i = _parse_factor(s, i, vars_)
    while True:
        i = _skip_ws(s, i)
        if i >= len(s):
            break
        op = s[i]
        if op == "*":
            i += 1
            rhs, i = _parse_factor(s, i, vars_)
            value *= rhs
        elif op == "/":
            i += 1
            denom, i = _parse_factor(s, i, vars_)
            if abs(denom) < 1e-9:
                raise ZeroDivisionError("division by zero")
            value /= denom
        else:
            break
    return value, i


def _parse_factor(s: str, i: int, vars_: Mapping[str, float]) -> tuple[float, int]:
    i = _skip_ws(s, i)
    if i >= len(s):
        raise ValueError("Unexpected end")

    if s[i] == "(":
        i += 1
        inner, i = _parse_expression(s, i, vars_)
        i = _skip_ws(s, i)
        if i >= len(s) or s[i] != ")":
            raise ValueError("Missing )")
        return inner, i + 1

    if s[i] == "-":
        i += 1
        v, i = _parse_factor(s, i, vars_)
        return -v, i

    if s[i] == "+":
        i += 1
        return _parse_factor(s, i, vars_)

    if s[i].isdigit() or s[i] == ".":
        return _parse_number(s, i)

    if s[i].isalpha() or s[i] == "_":
        return _parse_identifier(s, i, vars_)

    raise ValueError(f"Unexpected token at {i}")


def _parse_number(s: str, i: int) -> tuple[float, int]:
    start = i
    while i < len(s) and (s[i].isdigit() or s[i] == "."):
        i += 1
    token = s[start:i]
    return float(token), i


def _parse_identifier(s: str, i: int, vars_: Mapping[str, float]) -> tuple[float, int]:
    start = i
    while i < len(s) and (s[i].isalnum() or s[i] == "_"):
        i += 1
    sym = s[start:i]
    if sym not in vars_:
        raise KeyError(sym)
    return float(vars_[sym]), i
