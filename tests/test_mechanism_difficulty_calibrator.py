import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.math import MATHBenchmark
from scripts.baseline_failure_mechanisms import file_sha256, load_bundle
from scripts.baseline_failure_mechanisms_v2 import (
    DIFFICULTY_LEVELS,
    MECHANISMS,
    _count_digit_matches,
)
from scripts.mechanism_difficulty_calibrator import (
    generate_calibration_pool,
    generate_hidden_confirmation,
    select_parameter_ranges,
)


class MechanismDifficultyCalibratorTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        path = root / "source.json"
        path.write_text(
            json.dumps({"selected_new_mechanisms": list(MECHANISMS)}) + "\n",
            encoding="utf-8",
        )
        return path

    def test_digit_dp_matches_brute_force(self):
        for limit, target, multiplicity in [(999, 0, 2), (5231, 3, 2), (20100, 0, 3)]:
            expected = sum(
                str(value).count(str(target)) >= multiplicity
                for value in range(1, limit + 1)
            )
            self.assertEqual(
                _count_digit_matches(limit, target, multiplicity), expected
            )

    def test_pool_is_balanced_exact_and_difficulty_labeled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = generate_calibration_pool(
                output_dir=root / "pool", source_report=self._source(root),
                seed=97001, cases_per_bin=12,
            )
            manifest, rows = load_bundle(manifest_path)
            self.assertEqual(len(rows), len(MECHANISMS) * len(DIFFICULTY_LEVELS) * 12)
            counts = {}
            scorer = MATHBenchmark("MATH", "", "")
            for row in rows:
                metadata = row["mechanism_metadata"]
                key = (metadata["family"], metadata["difficulty_bin"])
                counts[key] = counts.get(key, 0) + 1
                score, _ = scorer.calculate_score(
                    row["solution"], rf"\boxed{{{metadata['oracle_answer']}}}"
                )
                self.assertEqual(score, 1, metadata)
            self.assertEqual(set(counts.values()), {12})
            self.assertFalse(manifest["individual_problem_selection_allowed"])
            self.assertFalse(
                manifest["screening_data"]["visible_to_operator_generator"]
            )

    def test_selects_only_aggregate_bin_and_confirmation_is_disjoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = generate_calibration_pool(
                output_dir=root / "pool", source_report=self._source(root),
                seed=97001, cases_per_bin=12,
            )
            manifest, rows = load_bundle(manifest_path)
            selected_mechanism = "repeated_digit_interval_inclusion_exclusion"
            selected_difficulty = "hard"
            selected_ids = [
                row["mechanism_metadata"]["problem_id"] for row in rows
                if row["mechanism_metadata"]["family"] == selected_mechanism
                and row["mechanism_metadata"]["difficulty_bin"] == selected_difficulty
            ][:3]
            observations = []
            audits = []
            audit_number = 0
            for row in rows:
                metadata = row["mechanism_metadata"]
                for run in range(1, 4):
                    failure = metadata["problem_id"] in selected_ids
                    observations.append(
                        {
                            "mechanism_id": metadata["family"],
                            "problem_id": metadata["problem_id"],
                            "run": run,
                            "score": 0 if failure else 1,
                        }
                    )
                    if failure:
                        audit_number += 1
                        audits.append(
                            {
                                "audit_id": f"a{audit_number}",
                                "problem_id": metadata["problem_id"],
                                "run": run,
                                "audit_label": "true_model_error",
                            }
                        )
            observations_path = root / "observations.jsonl"
            observations_path.write_text(
                "".join(json.dumps(row) + "\n" for row in observations),
                encoding="utf-8",
            )
            audit_path = root / "audit.jsonl"
            audit_path.write_text(
                "".join(json.dumps(row) + "\n" for row in audits),
                encoding="utf-8",
            )
            raw_path = root / "raw.json"
            raw_path.write_text(
                json.dumps(
                    {
                        "manifest_sha256": manifest["_manifest_sha256"],
                        "observations": str(observations_path),
                        "observations_sha256": file_sha256(observations_path),
                    }
                ) + "\n",
                encoding="utf-8",
            )
            selection_path = root / "selection.json"
            report = select_parameter_ranges(
                calibration_manifest=manifest_path, raw_summary=raw_path,
                audit_queue=audit_path, output_path=selection_path,
            )
            self.assertEqual(
                report["selected_parameter_ranges"],
                [
                    {
                        "mechanism_id": selected_mechanism,
                        "difficulty_bin": selected_difficulty,
                        "parameter_range": report["selected_parameter_ranges"][0]["parameter_range"],
                    }
                ],
            )
            confirmation_path = generate_hidden_confirmation(
                output_dir=root / "confirmation", selection_report=selection_path,
                seed=98003, cases_per_selected_bin=12,
            )
            confirmation, confirmation_rows = load_bundle(confirmation_path)
            calibration_ids = {
                row["mechanism_metadata"]["problem_id"] for row in rows
            }
            confirmation_ids = {
                row["mechanism_metadata"]["problem_id"] for row in confirmation_rows
            }
            self.assertTrue(calibration_ids.isdisjoint(confirmation_ids))
            self.assertEqual(
                {row["mechanism_metadata"]["difficulty_bin"] for row in confirmation_rows},
                {selected_difficulty},
            )
            self.assertTrue(confirmation["hidden_from_operator_generator"])
            with self.assertRaises(ValueError):
                generate_hidden_confirmation(
                    output_dir=root / "bad", selection_report=selection_path,
                    seed=97001, cases_per_selected_bin=12,
                )


if __name__ == "__main__":
    unittest.main()
