import asyncio
import tempfile
import unittest
from pathlib import Path

from scripts.operator_library import OperatorLibrary, ProblemFeatureExtractor
from scripts.router_end_to_end_experiment import (
    EndToEndRunner,
    OPERATOR_NAME,
    _near_miss_rows,
    summarize,
)
from scripts.parameterized_tools import PARENTHESIZATION_MECHANISM


class RouterEndToEndExperimentTests(unittest.TestCase):
    def test_evaluation_only_candidate_router_uses_rules_and_safe_fallback(self):
        class Specialist:
            async def __call__(self, problem):
                return {"status": "success", "response": "specialist"}

        class Fallback:
            async def __call__(self, problem):
                return {"status": "success", "response": "fallback"}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operators.json"
            library = OperatorLibrary(path)
            library.upsert(
                {
                    "name": OPERATOR_NAME,
                    "status": "candidate",
                    "artifact": {"artifact_sha256": "candidate-v2"},
                    "applicability_rules": [
                        {
                            "name": "v2",
                            "required_features": [PARENTHESIZATION_MECHANISM],
                            "weight": 0.8,
                        }
                    ],
                    "failure_boundaries": [],
                }
            )
            runner = EndToEndRunner.__new__(EndToEndRunner)
            runner.library_path = path
            runner.candidate_name = OPERATOR_NAME
            runner.candidate_factory = lambda llm: Specialist()
            positive = asyncio.run(
                runner._candidate_routed_call(
                    llm=None,
                    fallback=Fallback(),
                    problem=(
                        "The expression $2 \\cdot 3 + 4$ may be changed only by "
                        "inserting parentheses. How many values can be obtained?"
                    ),
                )
            )
            self.assertEqual(positive["response"], "specialist")
            self.assertFalse(positive["used_fallback"])
            negative = asyncio.run(
                runner._candidate_routed_call(
                    llm=None,
                    fallback=Fallback(),
                    problem="Fully parenthesize 2 + 3 * 4. What is the maximum value?",
                )
            )
            self.assertEqual(negative["response"], "fallback")
            self.assertTrue(negative["used_fallback"])

    def test_generated_near_misses_do_not_activate_specialist_feature(self):
        rows = _near_miss_rows(seed=88771, count=12)
        extractor = ProblemFeatureExtractor()
        self.assertEqual(len(rows), 12)
        self.assertEqual(
            len({row["mechanism_metadata"]["problem_id"] for row in rows}), 12
        )
        for row in rows:
            self.assertNotIn(
                PARENTHESIZATION_MECHANISM,
                extractor.extract(row["problem"]).features,
            )

    def test_summary_reports_required_end_to_end_metrics(self):
        details = []
        for index in range(2):
            details.append(
                {
                    "split": "specialist_positive",
                    "problem_id": f"p{index}",
                    "baseline_score": 0,
                    "routed_score": 1,
                    "baseline_timeout": True,
                    "routed_timeout": False,
                    "baseline_error": "TimeoutError",
                    "routed_error": "",
                    "baseline_tokens": 10,
                    "routed_tokens": 0,
                    "baseline_calls": 1,
                    "routed_calls": 0,
                    "routed_selected_operator": OPERATOR_NAME,
                    "routed_route_selected_operator": OPERATOR_NAME,
                    "routed_used_fallback": False,
                }
            )
        for index in range(2):
            details.append(
                {
                    "split": "non_specialist_negative",
                    "problem_id": f"n{index}",
                    "baseline_score": 1,
                    "routed_score": 1,
                    "baseline_timeout": False,
                    "routed_timeout": False,
                    "baseline_error": "",
                    "routed_error": "",
                    "baseline_tokens": 10,
                    "routed_tokens": 10,
                    "baseline_calls": 2,
                    "routed_calls": 2,
                    "routed_selected_operator": None,
                    "routed_route_selected_operator": None,
                    "routed_used_fallback": True,
                }
            )
        gates = {
            "specialist_accuracy_at_least": 0.95,
            "positive_route_recall": 1.0,
            "negative_false_route_rate": 0.0,
            "negative_accuracy_delta_at_least": -0.05,
            "routing_attributable_negative_regressions": 0,
        }
        result = summarize(details, gates)
        self.assertTrue(result["passed"])
        self.assertEqual(result["overall"]["baseline"]["correct"], 2)
        self.assertEqual(result["overall"]["routed_aflow"]["correct"], 4)
        self.assertEqual(
            result["non_specialist_questions"]["regression_observations"], 0
        )
        self.assertEqual(result["routing"]["negative_false_route_rate"], 0.0)
        self.assertEqual(result["overall"]["baseline"]["timeouts"], 2)


if __name__ == "__main__":
    unittest.main()
