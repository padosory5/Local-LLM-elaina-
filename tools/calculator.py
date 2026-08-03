from __future__ import annotations

import ast
from dataclasses import dataclass


class CalculationError(ValueError):
    """
    Raised when a calculation step cannot be trusted.

    Covers both unsafe expression syntax and runtime failures (division by
    zero, an exponent large enough to be a denial-of-service risk, missing
    fields). The caller's only correct response to any of these is the same:
    fall back to the language model's own best-effort answer instead of
    reporting a broken tool result as verified.
    """


# Only plain arithmetic is ever allowed. The expression text is written by a
# language model, so this must reject function calls, names, attribute
# access, and anything else that is not a numeric literal or an arithmetic
# operator -- never fall back to eval() or ast.literal_eval() extensions.
_ALLOWED_BINARY_OPERATORS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.FloorDiv,
)
_ALLOWED_UNARY_OPERATORS = (ast.UAdd, ast.USub)
_MAX_EXPRESSION_LENGTH = 300
_MAX_EXPONENT_MAGNITUDE = 12


def evaluate_expression(expression: str) -> float:
    """Evaluate a plain arithmetic expression exactly, without using eval()."""
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise CalculationError(
            f"Expression is too long to trust: {expression!r}"
        )

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise CalculationError(
            f"Could not parse expression: {expression!r}"
        ) from error

    try:
        return _evaluate_node(tree.body, expression)
    except ZeroDivisionError as error:
        raise CalculationError(
            f"Division by zero in expression: {expression!r}"
        ) from error
    except OverflowError as error:
        raise CalculationError(
            f"Expression overflowed: {expression!r}"
        ) from error


def _evaluate_node(node: ast.AST, expression: str) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(
            node.value, (int, float)
        ):
            raise CalculationError(
                f"Non-numeric literal in expression: {expression!r}"
            )
        return float(node.value)

    if isinstance(node, ast.BinOp) and isinstance(
        node.op, _ALLOWED_BINARY_OPERATORS
    ):
        left = _evaluate_node(node.left, expression)
        right = _evaluate_node(node.right, expression)

        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Pow):
            if abs(right) > _MAX_EXPONENT_MAGNITUDE:
                raise CalculationError(
                    f"Exponent too large to trust: {expression!r}"
                )
            return left ** right

    if isinstance(node, ast.UnaryOp) and isinstance(
        node.op, _ALLOWED_UNARY_OPERATORS
    ):
        value = _evaluate_node(node.operand, expression)
        return value if isinstance(node.op, ast.UAdd) else -value

    raise CalculationError(
        f"Disallowed syntax in expression: {expression!r}"
    )


@dataclass(frozen=True)
class CalculationStep:
    label: str
    expression: str
    value: float
