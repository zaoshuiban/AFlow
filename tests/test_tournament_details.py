import json
import tempfile
import unittest
from pathlib import Path

from run_operator_evolution_pipeline import (
    _generation_policy,
    _load_unopened_comparison_batch,
    _write_detailed_rows,
)


class TournamentDetailTests(unittest.TestCase):
    def test_generation_tracks_are_explicit_and_disjoint(self):
        prompt, prompt_source = _generation_policy("prompt_only")
        tool, tool_source = _generation_policy("tool_augmented")
        self.assertEqual(prompt["execution_mode"], "prompt_only")
        self.assertFalse(prompt["programmer_allowed"])
        self.assertEqual(tool["execution_mode"], "tool_augmented")
        self.assertTrue(tool["programmer_allowed"])
        self.assertEqual(tool["allowed_skeleton_types"], ["programmer_step"])
        self.assertNotEqual(prompt_source, tool_source)

    def test_comparison_rejects_a_batch_after_hidden_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "pending_tournament",
                        "batch_context": {
                            "operator_generation_policy": {
                                "execution_mode": "prompt_only"
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = _load_unopened_comparison_batch(path, "prompt_only")
            self.assertEqual(loaded["status"], "pending_tournament")
            (root / "tournament_summary.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                _load_unopened_comparison_batch(path, "prompt_only")

    def test_invalidated_batch_requires_explicit_rerun_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "invalidated_infrastructure",
                        "batch_context": {
                            "operator_generation_policy": {
                                "execution_mode": "tool_augmented"
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit):
                _load_unopened_comparison_batch(path, "tool_augmented")
            loaded = _load_unopened_comparison_batch(
                path, "tool_augmented", allow_invalidated=True
            )
            self.assertEqual(loaded["status"], "invalidated_infrastructure")

    def test_details_preserve_full_baseline_and_candidate_outputs(self):
        row = {
            "run": 1,
            "index": 0,
            "split": "target",
            "problem_id": "p1",
            "baseline_score": 0,
            "candidate_score": 1,
            "effective_candidate_score": 1,
            "baseline_prediction": "baseline full reasoning \\boxed{2}",
            "baseline_extracted": "2",
            "candidate_prediction": "candidate full reasoning \\boxed{3}",
            "candidate_extracted": "3",
            "declined": False,
            "error": "",
        }
        result = {
            "ranking": [
                {
                    "operator_name": "Candidate",
                    "metrics": {},
                    "rows": [row],
                }
            ]
        }
        data = [
            {
                "problem": "What is the answer?",
                "solution": "The answer is \\boxed{3}",
                "mechanism_metadata": {"case_id": "case-1"},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "details.jsonl"
            _write_detailed_rows(result, data, path)
            detail = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(detail["baseline_prediction"], row["baseline_prediction"])
        self.assertEqual(detail["candidate_prediction"], row["candidate_prediction"])
        self.assertEqual(detail["baseline_extracted"], "2")
        self.assertEqual(detail["candidate_extracted"], "3")
        self.assertEqual(detail["reference_solution"], data[0]["solution"])
        self.assertEqual(detail["mechanism_metadata"]["case_id"], "case-1")


if __name__ == "__main__":
    unittest.main()
