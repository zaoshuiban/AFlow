import argparse
import json
import tempfile
import unittest
from pathlib import Path

from experiment_contextual_router_replay import (
    LEGACY_DATASET_SHA256,
    LEGACY_MATCHED_INDICES,
    LEGACY_V1_ARTIFACT_SHA256,
    build_replay,
    load_dataset,
    validate_source_summary,
)


ROOT = Path(__file__).resolve().parents[1]


def replay_args(**overrides):
    values = {
        "input_dir": str(ROOT / "workspace/MATH/operator_direct_combo_full"),
        "library": str(
            ROOT / "workspace/MATH/operator_library/operators.json"
        ),
        "data": str(ROOT / "data/datasets/math_validate.jsonl"),
        "num_runs": 3,
        "min_score": 0.70,
        "output": str(
            ROOT / "workspace/MATH/operator_library/replay_summary.json"
        ),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ContextualRouterReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.replay = build_replay(replay_args())

    def test_replay_recomputes_scores_and_separates_policy_versions(self):
        aggregate = self.replay["aggregate"]
        self.assertEqual(aggregate["unique_problems"], 24)
        self.assertEqual(aggregate["trial_instances"], 72)
        self.assertEqual(aggregate["generic_two_stage"]["correct"], 60)
        self.assertEqual(aggregate["legacy_v1_unconditional"]["correct"], 62)
        self.assertEqual(
            aggregate["legacy_v1_unconditional"]["wins"], 9
        )
        self.assertEqual(
            aggregate["legacy_v1_unconditional"]["ties"], 56
        )
        self.assertEqual(
            aggregate["legacy_v1_unconditional"]["losses"], 7
        )

        current = aggregate["current_safe_router_v2"]
        self.assertEqual(current["correct"], 60)
        self.assertEqual(current["selected_trial_instances"], 0)
        self.assertEqual(
            (current["wins"], current["ties"], current["losses"]),
            (0, 72, 0),
        )

        counterfactual = aggregate["legacy_v1_policy_counterfactual"]
        self.assertEqual(counterfactual["correct"], 62)
        self.assertEqual(counterfactual["selected_trial_instances"], 6)
        self.assertEqual(
            (
                counterfactual["wins"],
                counterfactual["ties"],
                counterfactual["losses"],
            ),
            (2, 70, 0),
        )
        self.assertEqual(aggregate["legacy_v1_policy_unique_selected"], 2)
        self.assertEqual(aggregate["legacy_v1_policy_unique_winning"], 1)
        self.assertEqual(
            aggregate["stored_score_changes_after_current_rescore"],
            {"generic_two_stage": 7, "evolved_combo": 8},
        )

    def test_replay_has_revision_bound_provenance(self):
        provenance = self.replay["provenance"]
        self.assertEqual(
            provenance["dataset"]["sha256"], LEGACY_DATASET_SHA256
        )
        self.assertEqual(provenance["library"]["operator_version"], 2)
        self.assertEqual(provenance["library"]["operator_status"], "candidate")
        self.assertEqual(
            provenance["artifacts"]["legacy_v1"]["artifact_sha256"],
            LEGACY_V1_ARTIFACT_SHA256,
        )
        self.assertTrue(all(self.replay["validation"].values()))
        self.assertEqual(len(provenance["source_experiment"]["files"]), 6)

    def test_only_legacy_counterfactual_selects_the_two_development_items(self):
        current_selected = {
            row["index"]
            for row in self.replay["rows"]
            if row["current_safe_router_v2"]["selected_operator"]
        }
        legacy_selected = {
            row["index"]
            for row in self.replay["rows"]
            if row["legacy_v1_policy_counterfactual"]["selected_operator"]
        }
        self.assertEqual(current_selected, set())
        self.assertEqual(legacy_selected, {102, 118})

    def test_dataset_digest_and_run_count_fail_closed(self):
        original = (ROOT / "data/datasets/math_validate.jsonl").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "changed.jsonl"
            changed.write_bytes(original + b"\n")
            with self.assertRaisesRegex(ValueError, "Dataset digest"):
                load_dataset(changed)

        with self.assertRaisesRegex(ValueError, "num-runs"):
            build_replay(replay_args(num_runs=2))

    def test_source_summary_must_keep_the_exact_legacy_index_set(self):
        data_path = ROOT / "data/datasets/math_validate.jsonl"
        summary_path = (
            ROOT / "workspace/MATH/operator_direct_combo_full/summary.json"
        )
        data = load_dataset(data_path)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["matched_indices"] = list(LEGACY_MATCHED_INDICES[:-1])

        with self.assertRaisesRegex(ValueError, "exact legacy index set"):
            validate_source_summary(
                summary,
                summary_path=summary_path,
                data_path=data_path,
                data=data,
                num_runs=3,
            )


if __name__ == "__main__":
    unittest.main()
