"""Reusable, parameterized tools for stable failure mechanisms.

Each tool owns a narrow detector, parser, exact algorithm, and refusal boundary.
It receives only the current problem text and never reads datasets or answers.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Optional


PARENTHESIZATION_MECHANISM = "parenthesization_distinct_value_dedup"


@dataclass(frozen=True)
class ParameterizedToolResult:
    status: str
    mechanism: str = ""
    response: str = ""
    observation: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ParenthesizationDistinctValueTool:
    mechanism = PARENTHESIZATION_MECHANISM
    version = "2"
    max_operands = 13
    max_interval_values = 500_000

    _EXPRESSION = re.compile(
        r"(?<![\w.])\d+(?:\s*(?:\+|-|\*)\s*\d+){1,}(?!\w)(?!\.\d)"
    )

    @classmethod
    def detect(cls, problem: str) -> bool:
        text = " ".join(str(problem).lower().split())
        explicit_distinct_request = bool(
            re.search(r"\bdistinct values?\b|\bdifferent values?\b", text)
        )
        natural_value_request = bool(
            re.search(
                r"(?:how many|number of)\s+(?:different\s+)?values?\s+"
                r"(?:can|could|may|are|is|will|would)?\s*"
                r"(?:be\s+)?(?:obtained|produced|formed|made|result)",
                text,
            )
        )
        negated_intent = bool(
            re.search(
                r"(?:do not|don't|not|rather than|instead of)\s+"
                r"(?:count(?:ing)?\s+)?(?:the\s+)?(?:distinct|different)\s+"
                r"(?:numerical\s+)?values?",
                text,
            )
        )
        asks_parenthesization = bool(
            "parenthes" in text
            or re.search(
                r"(?:insert|inserting|place|placing|add|adding)\s+"
                r"(?:pairs?\s+of\s+)?(?:round\s+)?(?:parentheses|brackets)",
                text,
            )
        )
        return (
            asks_parenthesization
            and (explicit_distinct_request or natural_value_request)
            and not negated_intent
        )

    @staticmethod
    def _normalize_expression_surface(problem: str) -> str:
        text = str(problem)
        for source, target in (
            (r"\cdot", "*"),
            (r"\times", "*"),
            ("×", "*"),
            ("·", "*"),
            ("−", "-"),
        ):
            text = text.replace(source, target)
        return (
            text.replace(r"\left", "")
            .replace(r"\right", "")
            .replace(r"\,", " ")
            .replace(r"\!", "")
        )

    @classmethod
    def _extract_expression(cls, problem: str) -> Optional[str]:
        normalized = cls._normalize_expression_surface(problem)
        candidates = [
            match.group(0).strip() for match in cls._EXPRESSION.finditer(normalized)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda value: (len(re.findall(r"[+*-]", value)), len(value)))

    @staticmethod
    def _parse(expression: str) -> tuple[list[int], list[str]]:
        tokens = re.findall(r"\d+|[+*-]", expression)
        if not tokens or len(tokens) % 2 == 0:
            raise ValueError("malformed operand/operator sequence")
        numbers: list[int] = []
        operators: list[str] = []
        for index, token in enumerate(tokens):
            if index % 2 == 0:
                if not token.isdigit():
                    raise ValueError("only non-negative integer operands are supported")
                numbers.append(int(token))
            else:
                if token not in {"+", "-", "*"}:
                    raise ValueError(f"unsupported operator: {token}")
                operators.append(token)
        if len(operators) != len(numbers) - 1:
            raise ValueError("malformed operand/operator sequence")
        return numbers, operators

    @classmethod
    def _solve_exact(cls, numbers: list[int], operators: list[str]) -> int:
        n = len(numbers)
        table: list[list[set[int]]] = [[set() for _ in range(n)] for _ in range(n)]
        for index, value in enumerate(numbers):
            table[index][index].add(value)
        for length in range(2, n + 1):
            for start in range(0, n - length + 1):
                end = start + length - 1
                values = table[start][end]
                for split in range(start, end):
                    operator = operators[split]
                    for left in table[start][split]:
                        for right in table[split + 1][end]:
                            if operator == "+":
                                values.add(left + right)
                            elif operator == "-":
                                values.add(left - right)
                            else:
                                values.add(left * right)
                            if len(values) > cls.max_interval_values:
                                raise OverflowError("interval value set exceeds safe boundary")
        return len(table[0][n - 1])

    @classmethod
    def solve(cls, problem: str) -> ParameterizedToolResult:
        started = time.perf_counter()
        if not cls.detect(problem):
            return ParameterizedToolResult(status="no_match")
        expression = cls._extract_expression(problem)
        if expression is None:
            return ParameterizedToolResult(
                status="refused",
                mechanism=cls.mechanism,
                observation="known_mechanism_but_expression_not_extractable",
            )
        try:
            numbers, operators = cls._parse(expression)
            if len(numbers) < 2:
                raise ValueError("at least two operands are required")
            if len(numbers) > cls.max_operands:
                return ParameterizedToolResult(
                    status="refused",
                    mechanism=cls.mechanism,
                    observation=(
                        f"operand_count={len(numbers)} exceeds max={cls.max_operands}"
                    ),
                    parameters={"expression": expression, "operand_count": len(numbers)},
                )
            answer = cls._solve_exact(numbers, operators)
        except (ValueError, OverflowError) as exc:
            return ParameterizedToolResult(
                status="refused",
                mechanism=cls.mechanism,
                observation=f"parameterized_tool_boundary:{exc}",
                parameters={"expression": expression},
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ParameterizedToolResult(
            status="success",
            mechanism=cls.mechanism,
            response=f"Reusable exact interval-DP result: {answer}\n\\boxed{{{answer}}}",
            parameters={
                "expression": expression,
                "numbers": numbers,
                "operators": operators,
            },
            elapsed_ms=elapsed_ms,
        )


class ParameterizedToolRegistry:
    """Deterministic registry; ordering is explicit and auditable."""

    def __init__(self, enabled_mechanisms: Optional[Iterable[str]] = None):
        available = {
            PARENTHESIZATION_MECHANISM: ParenthesizationDistinctValueTool,
        }
        enabled = set(enabled_mechanisms or available)
        unknown = enabled - set(available)
        if unknown:
            raise ValueError(f"unknown parameterized mechanisms: {sorted(unknown)}")
        self.tools = [available[name] for name in sorted(enabled)]

    def solve(self, problem: str) -> ParameterizedToolResult:
        for tool in self.tools:
            result = tool.solve(problem)
            if result.status != "no_match":
                return result
        return ParameterizedToolResult(
            status="no_match",
            observation="no_registered_parameterized_mechanism_matched",
        )
