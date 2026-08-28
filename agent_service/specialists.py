"""Deterministic master routing and specialist implementations for the POC."""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SpecialistResult:
    name: str
    answer: str
    activity: str


MATH_WORDS = {"calculate", "math", "plus", "minus", "multiply", "times", "divide", "sum"}


def route_specialist(question: str) -> str:
    """A transparent stand-in for the master agent's LLM-based routing."""
    normalized = question.lower()
    contains_math_word = any(word in normalized for word in MATH_WORDS)
    contains_expression = bool(re.search(r"\d\s*[-+*/]\s*\d", normalized))
    return "math-specialist" if contains_math_word or contains_expression else "knowledge-specialist"


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        return _BINARY_OPERATORS[type(node.op)](_evaluate(node.left), _evaluate(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))
    raise ValueError("Only basic arithmetic (+, -, *, /) is supported in this POC")


def math_specialist(question: str) -> SpecialistResult:
    match = re.search(r"[-+*/().\d\s]+", question)
    expression = match.group(0).strip() if match else ""
    if not expression or not re.search(r"\d", expression):
        answer = "I was routed to the math specialist, but I could not find a basic arithmetic expression. Try: Calculate 24 * 7."
    else:
        try:
            value = _evaluate(ast.parse(expression, mode="eval"))
            display_value = int(value) if value.is_integer() else round(value, 6)
            answer = f"The math specialist calculated `{expression}` and the result is **{display_value}**."
        except (SyntaxError, ValueError, ZeroDivisionError) as exc:
            answer = f"The math specialist could not evaluate `{expression}`: {exc}."
    return SpecialistResult("math-specialist", answer, "Evaluating the arithmetic expression")


def knowledge_specialist(question: str) -> SpecialistResult:
    answer = (
        "The knowledge specialist received your question and performed a mock knowledge-base lookup. "
        f"For this POC, the grounded response is: **{question}** would be answered from approved enterprise documents. "
        "The synchronized state event carries the mock source separately from this answer."
    )
    return SpecialistResult("knowledge-specialist", answer, "Searching the mock managed knowledge base")


def run_specialist(name: str, question: str) -> SpecialistResult:
    if name == "math-specialist":
        return math_specialist(question)
    return knowledge_specialist(question)

