import asyncio
import types
import unittest
from unittest.mock import patch

from scripts.operator_tournament import GenericProgrammerSolver
from scripts.formatter import CodeFormatter


class GenericProgrammerControlTests(unittest.TestCase):
    def test_code_formatter_exposes_programmer_code_field(self):
        valid, payload = CodeFormatter(function_name="solve").validate_response(
            "def solve():\n    return 7\n"
        )
        self.assertTrue(valid)
        self.assertEqual(payload["code"], payload["response"])

    def test_control_executes_programmer_and_saves_trace(self):
        class FakeProgrammer:
            def __init__(self, llm, name):
                self.name = name

            async def __call__(self, problem, analysis):
                self.problem = problem
                self.analysis = analysis
                return {"code": "def solve(): return 23", "output": "23"}

        module = types.SimpleNamespace(Programmer=FakeProgrammer)
        with patch(
            "scripts.operator_tournament.importlib.import_module",
            return_value=module,
        ):
            solver = GenericProgrammerSolver(None, "fake.operator")
            result = asyncio.run(solver("Count the exact values."))
        self.assertEqual(result["status"], "success")
        self.assertIn("\\boxed{23}", result["response"])
        self.assertEqual(result["tool_trace"]["output"], "23")
        self.assertIn("def solve", result["tool_trace"]["code"])

    def test_control_reports_execution_failure_with_trace(self):
        class FakeProgrammer:
            def __init__(self, llm, name):
                pass

            async def __call__(self, problem, analysis):
                return {
                    "code": "def solve(): return open('hidden')",
                    "output": "Execution error: prohibited name: open",
                }

        module = types.SimpleNamespace(Programmer=FakeProgrammer)
        with patch(
            "scripts.operator_tournament.importlib.import_module",
            return_value=module,
        ):
            solver = GenericProgrammerSolver(None, "fake.operator")
            result = asyncio.run(solver("Count the exact values."))
        self.assertEqual(result["status"], "error")
        self.assertIn("prohibited name", result["observation"])
        self.assertTrue(result["tool_trace"]["code"])


if __name__ == "__main__":
    unittest.main()
