import json
import tempfile
import unittest
from pathlib import Path

from scripts.rescore_operator_confirmation import rescore_confirmation


class OfflineConfirmationRescoreTests(unittest.TestCase):
    def test_saved_answers_are_rescored_without_overwriting_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "raw_summary.json"
            details = root / "raw_details.jsonl"
            summary.write_text(
                json.dumps(
                    {
                        "scorer_version": "math_equivalence_v4",
                        "experiment_role": "confirmatory",
                        "primary_candidate": "Candidate",
                        "num_runs": 1,
                        "ranking": [
                            {
                                "operator_name": "Candidate",
                                "metrics": {"wins": 1, "ties": 0, "losses": 0},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            row = {
                "operator_name": "Candidate",
                "run": 1,
                "index": 0,
                "split": "target",
                "problem_id": "p1",
                "problem": "Find the length.",
                "reference_solution": r"The answer is \boxed{121\sqrt{2}}.",
                "baseline_prediction": r"\boxed{121\sqrt2}",
                "candidate_prediction": r"\boxed{121\sqrt{2}}",
                "baseline_extracted": r"121\sqrt2",
                "candidate_extracted": r"121\sqrt{2}",
                "baseline_score": 0,
                "candidate_score": 1,
                "effective_candidate_score": 1,
                "declined": False,
                "error": "",
                "baseline_tokens": 100,
                "candidate_tokens": 50,
            }
            details.write_text(json.dumps(row) + "\n", encoding="utf-8")
            source_summary = summary.read_bytes()
            source_details = details.read_bytes()

            output_summary, output_details, report = rescore_confirmation(
                details_path=details,
                summary_path=summary,
                output_prefix=root / "rescored",
            )

            self.assertEqual(summary.read_bytes(), source_summary)
            self.assertEqual(details.read_bytes(), source_details)
            self.assertTrue(output_summary.is_file())
            self.assertTrue(output_details.is_file())
            self.assertEqual(report["scorer_version"], "math_equivalence_v10")
            self.assertFalse(report["primary_candidate_passed"])
            metrics = report["ranking"][0]["metrics"]
            self.assertEqual((metrics["wins"], metrics["ties"], metrics["losses"]), (0, 1, 0))
            rescored_row = json.loads(output_details.read_text(encoding="utf-8"))
            self.assertEqual(rescored_row["original_baseline_score"], 0)
            self.assertEqual(rescored_row["baseline_score"], 1)


if __name__ == "__main__":
    unittest.main()
