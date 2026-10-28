import asyncio
import json
import unittest
from pathlib import Path

from scripts.hybrid_parameterized_solver import HybridParameterizedSolver
from scripts.parameterized_tools import (
    PARENTHESIZATION_MECHANISM,
    ParameterizedToolRegistry,
    ParenthesizationDistinctValueTool,
)


def brute_values(numbers, operators):
    def recurse(left, right):
        if left == right:
            return {numbers[left]}
        values = set()
        for split in range(left, right):
            for a in recurse(left, split):
                for b in recurse(split + 1, right):
                    op = operators[split]
                    values.add(a + b if op == "+" else a - b if op == "-" else a * b)
        return values

    return recurse(0, len(numbers) - 1)


class ParameterizedToolTests(unittest.TestCase):
    def test_v2_handles_original_latex_natural_language_case(self):
        root = Path(__file__).resolve().parents[1]
        row = json.loads(
            (root / "data/datasets/math_validate.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[7]
        )
        result = ParenthesizationDistinctValueTool.solve(row["problem"])
        self.assertEqual(ParenthesizationDistinctValueTool.version, "2")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.parameters["operators"], ["*", "*", "*", "+"])
        self.assertIn("\\boxed{4}", result.response)

    def test_v2_accepts_cdot_and_times_but_rejects_near_miss_intent(self):
        for multiplication in (r"\cdot", r"\times", "×"):
            problem = (
                f"The expression $2 {multiplication} 3 + 4$ may be changed only "
                "by inserting parentheses. In total, how many values can be obtained?"
            )
            result = ParenthesizationDistinctValueTool.solve(problem)
            self.assertEqual(result.status, "success")
            self.assertEqual(result.parameters["operators"], ["*", "+"])
        rejected = ParenthesizationDistinctValueTool.solve(
            "How many full parenthesizations of 2 + 3 * 4 are possible? "
            "Do not count distinct values; count every form separately."
        )
        self.assertEqual(rejected.status, "no_match")

    def test_exact_tool_matches_independent_bruteforce(self):
        cases = [
            ([2, 5, 3, 6], ["-", "-", "*"]),
            ([7, 4, 8, 3, 7], ["*", "*", "-", "+"]),
            ([1, 2, 3, 4, 5], ["+", "*", "-", "+"]),
        ]
        for numbers, operators in cases:
            expression = " ".join(
                [
                    str(numbers[0]),
                    *[
                        token
                        for pair in zip(operators, map(str, numbers[1:]))
                        for token in pair
                    ],
                ]
            )
            problem = (
                f"Consider the expression {expression}. Fully parenthesize it in "
                "every possible way. How many distinct values are possible?"
            )
            result = ParenthesizationDistinctValueTool.solve(problem)
            self.assertEqual(result.status, "success")
            self.assertIn(
                f"\\boxed{{{len(brute_values(numbers, operators))}}}",
                result.response,
            )

    def test_registry_does_not_match_unknown_mechanism(self):
        result = ParameterizedToolRegistry().solve(
            "What is the probability of rolling a total of seven?"
        )
        self.assertEqual(result.status, "no_match")

    def test_known_mechanism_uses_reusable_tool_without_programmer(self):
        class FailingProgrammer:
            async def __call__(self, **kwargs):
                raise AssertionError("Programmer must not run for known mechanism")

        solver = HybridParameterizedSolver(
            None,
            "unused",
            registry=ParameterizedToolRegistry([PARENTHESIZATION_MECHANISM]),
            programmer=FailingProgrammer(),
        )
        result = asyncio.run(
            solver(
                "Consider the expression 2 - 5 - 3 * 6. Fully parenthesize it. "
                "How many distinct values are possible?"
            )
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["tool_trace"]["route"], "reusable_parameterized_tool"
        )

    def test_unknown_mechanism_uses_single_problem_programmer(self):
        class FakeProgrammer:
            calls = 0

            async def __call__(self, **kwargs):
                self.calls += 1
                return {"code": "def solve(): return 42", "output": "42"}

        programmer = FakeProgrammer()
        solver = HybridParameterizedSolver(
            None,
            "unused",
            registry=ParameterizedToolRegistry([PARENTHESIZATION_MECHANISM]),
            programmer=programmer,
        )
        result = asyncio.run(solver("What is 6 times 7?"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(programmer.calls, 1)
        self.assertEqual(
            result["tool_trace"]["route"],
            "single_problem_programmer_fallback",
        )
        self.assertIn("\\boxed{42}", result["response"])


if __name__ == "__main__":
    unittest.main()
