import json
import tempfile
import unittest
from pathlib import Path

from scripts.optimizer_utils.validation_utils import CodeValidator


def graph_with(operator_name):
    return f"""
class Workflow:
    def __init__(self, name, llm_config, dataset):
        self.solver = operator.{operator_name}(None)

    async def __call__(self, problem):
        result = await self.solver(problem=problem)
        return result['response'], 0.0
"""


class ManagedOperatorRoutingValidationTests(unittest.TestCase):
    def test_direct_managed_operator_is_rejected(self):
        valid, error = CodeValidator.validate_graph_and_prompt(
            graph_with("CombinatorialSymmetryDecomposer"),
            'PROMPT = "test"\n',
            round_number=999,
            dataset="MATH",
        )
        self.assertFalse(valid)
        self.assertIn("ContextAwareOperatorRouter", error)

    def test_context_router_is_allowed(self):
        valid, error = CodeValidator.validate_graph_and_prompt(
            graph_with("ContextAwareOperatorRouter"),
            'PROMPT = "test"\n',
            round_number=999,
            dataset="MATH",
        )
        self.assertTrue(valid, error)

    def test_explicit_library_path_is_used_for_nondefault_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            library_path = Path(directory) / "operator_library" / "operators.json"
            library_path.parent.mkdir()
            library_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "operators": {"PathSpecificSolver": {"name": "PathSpecificSolver"}},
                    }
                ),
                encoding="utf-8",
            )
            valid, error = CodeValidator.validate_graph_and_prompt(
                graph_with("PathSpecificSolver"),
                'PROMPT = "test"\n',
                round_number=999,
                dataset="MATH",
                library_path=str(library_path),
            )
            self.assertFalse(valid)
            self.assertIn("PathSpecificSolver", error)


if __name__ == "__main__":
    unittest.main()
