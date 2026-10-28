import unittest

from scripts.operator_tournament import calculate_operator_quality


def row(problem_id, baseline, candidate, *, split="target", declined=False, run=1):
    return {
        "run": run,
        "index": 0,
        "split": split,
        "problem_id": problem_id,
        "baseline_score": baseline,
        "candidate_score": candidate,
        "effective_candidate_score": baseline if declined and split == "regression" else candidate,
        "declined": declined,
        "error": "",
        "baseline_tokens": 100,
        "candidate_tokens": 110,
    }


class OperatorQualityTests(unittest.TestCase):
    def test_stable_unique_wins_pass_promotion(self):
        target = []
        for run in range(1, 4):
            target.extend(
                [
                    row("p1", 0, 1, run=run),
                    row("p2", 0, 1, run=run),
                ]
            )
        regression = [
            row("r1", 1, 0, split="regression", declined=True, run=run)
            for run in range(1, 4)
        ]
        result = calculate_operator_quality(target, regression)
        self.assertTrue(result["passed"], result["gate_failures"])
        self.assertEqual(result["unique_winning_problems"], 2)
        self.assertEqual(result["net_wins"], 6)
        self.assertEqual(result["regression_losses"], 0)
        self.assertEqual(result["stability"], 1.0)

    def test_one_memorized_win_cannot_pass(self):
        target = [row("same-problem", 0, 1, run=run) for run in range(1, 7)]
        result = calculate_operator_quality(target, [])
        self.assertFalse(result["passed"])
        self.assertIn("unique target problems", " ".join(result["gate_failures"]))

    def test_regression_loss_blocks_promotion(self):
        target = []
        for run in range(1, 4):
            target.extend([row("p1", 0, 1, run=run), row("p2", 0, 1, run=run)])
        regression = [
            row("r1", 1, 0, split="regression", run=run)
            for run in range(1, 4)
        ]
        result = calculate_operator_quality(target, regression)
        self.assertFalse(result["passed"])
        self.assertGreater(result["regression_loss_rate"], 0.10)


if __name__ == "__main__":
    unittest.main()

