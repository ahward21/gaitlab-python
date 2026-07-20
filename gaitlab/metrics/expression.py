"""Arithmetic formula language — Unity MetricExpression + common math functions."""

from __future__ import annotations

import math
from typing import Mapping

# Built-in functions (not treated as formula variables).
FUNCTIONS: dict[str, int] = {
    "sqrt": 1,
    "abs": 1,
    "sq": 1,  # square: sq(x) == x*x
    "pow": 2,
    "min": 2,
    "max": 2,
    "round": 1,
    "floor": 1,
    "ceil": 1,
    "log": 1,  # natural log
    "exp": 1,
    "sin": 1,
    "cos": 1,
}


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
    """Variable names on the RHS; skips known function names."""
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
            j = _skip_ws(rhs, i)
            # function call: name(
            if j < len(rhs) and rhs[j] == "(":
                continue
            if sym in FUNCTIONS:
                continue
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
    value, i = _parse_power(s, i, vars_)
    while True:
        i = _skip_ws(s, i)
        if i >= len(s):
            break
        op = s[i]
        if op == "*" and not (i + 1 < len(s) and s[i + 1] == "*"):
            i += 1
            rhs, i = _parse_power(s, i, vars_)
            value *= rhs
        elif op == "/":
            i += 1
            denom, i = _parse_power(s, i, vars_)
            if abs(denom) < 1e-9:
                raise ZeroDivisionError("division by zero")
            value /= denom
        else:
            break
    return value, i


def _parse_power(s: str, i: int, vars_: Mapping[str, float]) -> tuple[float, int]:
    """Exponentiation (^ or **) — right-associative, higher than * /."""
    value, i = _parse_factor(s, i, vars_)
    i = _skip_ws(s, i)
    if i >= len(s):
        return value, i
    if s[i] == "^" or (s[i] == "*" and i + 1 < len(s) and s[i + 1] == "*"):
        if s[i] == "*":
            i += 2
        else:
            i += 1
        exp, i = _parse_power(s, i, vars_)  # right-assoc
        value = value**exp
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
        return _parse_identifier_or_call(s, i, vars_)

    raise ValueError(f"Unexpected token at {i}")


def _parse_number(s: str, i: int) -> tuple[float, int]:
    start = i
    while i < len(s) and (s[i].isdigit() or s[i] == "."):
        i += 1
    token = s[start:i]
    return float(token), i


def _parse_identifier_or_call(s: str, i: int, vars_: Mapping[str, float]) -> tuple[float, int]:
    start = i
    while i < len(s) and (s[i].isalnum() or s[i] == "_"):
        i += 1
    sym = s[start:i]
    j = _skip_ws(s, i)
    if j < len(s) and s[j] == "(":
        return _parse_call(sym, s, j, vars_)
    if sym not in vars_:
        raise KeyError(sym)
    return float(vars_[sym]), i


def _parse_call(name: str, s: str, i: int, vars_: Mapping[str, float]) -> tuple[float, int]:
    """i points at '('."""
    if name not in FUNCTIONS:
        raise ValueError(f"Unknown function: {name}")
    arity = FUNCTIONS[name]
    i += 1  # skip (
    args: list[float] = []
    i = _skip_ws(s, i)
    if i < len(s) and s[i] == ")":
        if arity != 0:
            raise ValueError(f"{name}() needs {arity} argument(s)")
        return _eval_fn(name, args), i + 1

    while True:
        arg, i = _parse_expression(s, i, vars_)
        args.append(arg)
        i = _skip_ws(s, i)
        if i >= len(s):
            raise ValueError("Missing )")
        if s[i] == ",":
            i += 1
            continue
        if s[i] == ")":
            i += 1
            break
        raise ValueError(f"Unexpected token in {name}()")

    if len(args) != arity:
        raise ValueError(f"{name}() needs {arity} argument(s), got {len(args)}")
    return _eval_fn(name, args), i


def _eval_fn(name: str, args: list[float]) -> float:
    if name == "sqrt":
        if args[0] < 0:
            raise ValueError("sqrt of negative")
        return math.sqrt(args[0])
    if name == "abs":
        return abs(args[0])
    if name == "sq":
        return args[0] * args[0]
    if name == "pow":
        return args[0] ** args[1]
    if name == "min":
        return min(args[0], args[1])
    if name == "max":
        return max(args[0], args[1])
    if name == "round":
        return float(round(args[0]))
    if name == "floor":
        return float(math.floor(args[0]))
    if name == "ceil":
        return float(math.ceil(args[0]))
    if name == "log":
        return math.log(args[0])
    if name == "exp":
        return math.exp(args[0])
    if name == "sin":
        return math.sin(args[0])
    if name == "cos":
        return math.cos(args[0])
    raise ValueError(name)
