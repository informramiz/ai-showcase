"""A safe arithmetic evaluator: walks the AST and allows numbers and operators only."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import cast

from sourcebook.domain.errors import ValidationError

_BINARY: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
MAX_EXPRESSION_LENGTH = 200
MAX_EXPONENT = 64


def evaluate(expression: str) -> float:
    """Evaluate `expression` or raise `ValidationError` with a reason the model can act on."""
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValidationError("Expression is too long.")
    try:
        tree = ast.parse(expression.replace(",", ""), mode="eval")
    except SyntaxError as exc:
        raise ValidationError(f"Not a valid arithmetic expression: {expression!r}") from exc
    try:
        result = _eval(tree.body)
    except ZeroDivisionError as exc:
        raise ValidationError("Division by zero.") from exc
    except OverflowError as exc:
        raise ValidationError("Result is too large.") from exc
    if isinstance(result, complex):
        raise ValidationError("Result is not a real number.")
    if not math.isfinite(result):
        raise ValidationError("Result is not a finite number.")
    return result


def format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    if 0 < abs(value) < 1e-4:
        return f"{value:.6g}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _eval(node: ast.expr) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return float(cast("float", node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ValidationError("Exponent is too large.")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    raise ValidationError("Only numbers and + - * / // % ** ( ) are allowed.")
