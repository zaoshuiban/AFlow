import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.math import MATHBenchmark
from scripts.baseline_failure_mechanisms import load_bundle
from scripts.baseline_failure_mechanisms_v2 import (
    EXCLUDED_PRIOR_MECHANISMS,
    MECHANISMS,
    generate_screen,
    promote_family,
)
from scripts.trajectory_failure_expander import (
    ensure_disjoint_splits,
    load_bundle_manifest,
    load_bundle_split,
)


class BaselineFailureMechanismsV2Tests(unittest.TestCase):
    def _source_report(self, root: Path) -> Path:
        path = root / "audited119.json"
        path.write_text(
            json.dumps({"selected_new_mechanisms": list(MECHANISMS)}) + "\n",
            encoding="utf-8",
        )
        return path

    def test_screen_is_balanced_deterministic_and_baseline_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source_report(root)
            first_path = generate_screen(
                output_dir=root / "first", source_report=source,
                seed=95021, cases_per_mechanism=12,
            )
            second_path = generate_screen(
                output_dir=root / "second", source_report=source,
                seed=95021, cases_per_mechanism=12,
            )
            first, rows = load_bundle(first_path)
            second, _ = load_bundle(second_path)
            self.assertEqual(
                first["screening_data"]["sha256"], second["screening_data"]["sha256"]
            )
            self.assertEqual(len(rows), 12 * len(MECHANISMS))
            self.assertFalse(
                first["baseline_only_contract"]["candidate_generation_allowed"]
            )
            with self.assertRaises(ValueError):
                load_bundle_manifest(first_path)
            self.assertTrue(set(MECHANISMS).isdisjoint(EXCLUDED_PRIOR_MECHANISMS))
            scorer = MATHBenchmark("MATH", "", "")
            for row in rows:
                oracle = row["mechanism_metadata"]["oracle_answer"]
                score, _ = scorer.calculate_score(row["solution"], rf"\boxed{{{oracle}}}")
                self.assertEqual(score, 1, row["mechanism_metadata"])
                metadata = row["mechanism_metadata"]
                if metadata["family"] == "domain_filtered_trigonometric_branches":
                    domain = metadata["parameters"]["domain"]
                    accepted = metadata["audit_facts"]["accepted_angles"]
                    if domain == "A is obtuse":
                        self.assertTrue(all(angle > 90 for angle in accepted))
                    elif domain == "A is acute":
                        self.assertTrue(all(angle < 90 for angle in accepted))

    def test_promotion_requires_audited_acceptance_and_makes_disjoint_splits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root / "audit.json"
            audit.write_text(
                json.dumps({"status": "complete", "accepted_mechanisms": []}) + "\n",
                encoding="utf-8",
            )
            mechanism = MECHANISMS[0]
            with self.assertRaises(ValueError):
                promote_family(
                    output_dir=root / "rejected", audited_summary=audit,
                    mechanism=mechanism, seed=1, design_count=4,
                    gate_count=4, regression_count=4,
                )
            audit.write_text(
                json.dumps(
                    {"status": "complete", "accepted_mechanisms": [mechanism]}
                ) + "\n",
                encoding="utf-8",
            )
            manifest_path = promote_family(
                output_dir=root / "accepted", audited_summary=audit,
                mechanism=mechanism, seed=1, design_count=4,
                gate_count=4, regression_count=4,
            )
            manifest = load_bundle_manifest(manifest_path)
            splits = {
                split: load_bundle_split(manifest, split)
                for split in ("design", "gate", "regression")
            }
            ensure_disjoint_splits(splits)
            self.assertTrue(
                manifest["splits"]["design"]["visible_to_operator_generator"]
            )
            self.assertFalse(
                manifest["splits"]["gate"]["visible_to_operator_generator"]
            )


if __name__ == "__main__":
    unittest.main()
