import json
import tempfile
import unittest
from pathlib import Path

from scripts.baseline_failure_mechanisms import file_sha256, load_bundle
from scripts.baseline_failure_mechanisms_v2 import MECHANISMS
from scripts.mechanism_difficulty_calibrator import generate_calibration_pool
from scripts.operational_headroom_gate import (
    DIFFICULTY,
    MECHANISM,
    calibration_operational_evidence,
    finalize_confirmation,
    generate_confirmation,
    promote_operational_family,
)
from scripts.trajectory_failure_expander import (
    ensure_disjoint_splits,
    load_bundle_manifest,
    load_bundle_split,
)


class OperationalHeadroomGateTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        path = root / "source.json"
        path.write_text(
            json.dumps({"selected_new_mechanisms": list(MECHANISMS)}) + "\n",
            encoding="utf-8",
        )
        return path

    def _saved_run(
        self, root: Path, manifest_path: Path, failure_ids: set[str],
    ) -> tuple[Path, Path]:
        manifest, rows = load_bundle(manifest_path)
        observations = []
        audits = []
        for row in rows:
            metadata = row["mechanism_metadata"]
            for run in range(1, 4):
                timeout = metadata["problem_id"] in failure_ids
                observations.append(
                    {
                        "mechanism_id": metadata["family"],
                        "problem_id": metadata["problem_id"],
                        "run": run,
                        "score": 0 if timeout else 1,
                    }
                )
                if timeout:
                    audits.append(
                        {
                            "audit_id": f"{metadata['problem_id']}-{run}",
                            "problem_id": metadata["problem_id"],
                            "run": run,
                            "audit_label": "execution_error",
                            "execution_error": "TimeoutError: ",
                        }
                    )
        observations_path = root / f"observations-{manifest['family_id']}.jsonl"
        observations_path.write_text(
            "".join(json.dumps(row) + "\n" for row in observations),
            encoding="utf-8",
        )
        audit_path = root / f"audit-{manifest['family_id']}.jsonl"
        audit_path.write_text(
            "".join(json.dumps(row) + "\n" for row in audits), encoding="utf-8"
        )
        raw_path = root / f"raw-{manifest['family_id']}.json"
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
        return raw_path, audit_path

    def test_fresh_confirmation_and_promotion_are_fail_closed_and_disjoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calibration_path = generate_calibration_pool(
                output_dir=root / "calibration", source_report=self._source(root),
                seed=97001, cases_per_bin=12,
            )
            _, calibration_rows = load_bundle(calibration_path)
            hard_ids = [
                row["mechanism_metadata"]["problem_id"]
                for row in calibration_rows
                if row["mechanism_metadata"]["family"] == MECHANISM
                and row["mechanism_metadata"]["difficulty_bin"] == DIFFICULTY
            ]
            raw, audit = self._saved_run(root, calibration_path, set(hard_ids[:4]))
            evidence = calibration_operational_evidence(
                calibration_manifest=calibration_path,
                raw_summary=raw, audit_queue=audit,
            )
            self.assertTrue(evidence["qualifies_for_fresh_confirmation"])
            confirmation_path = generate_confirmation(
                output_dir=root / "confirmation",
                calibration_manifest=calibration_path,
                raw_summary=raw, audit_queue=audit,
                seed=99007, cases=12,
            )
            confirmation, confirmation_rows = load_bundle(confirmation_path)
            calibration_ids = {
                row["mechanism_metadata"]["problem_id"] for row in calibration_rows
            }
            confirmation_ids = {
                row["mechanism_metadata"]["problem_id"] for row in confirmation_rows
            }
            self.assertTrue(calibration_ids.isdisjoint(confirmation_ids))
            self.assertTrue(confirmation["hidden_from_operator_generator"])
            with self.assertRaises(ValueError):
                generate_confirmation(
                    output_dir=root / "bad-confirmation",
                    calibration_manifest=calibration_path,
                    raw_summary=raw, audit_queue=audit,
                    seed=97001, cases=12,
                )

            confirmation_failure_ids = {
                row["mechanism_metadata"]["problem_id"]
                for row in confirmation_rows[:3]
            }
            confirmation_raw, confirmation_audit = self._saved_run(
                root, confirmation_path, confirmation_failure_ids
            )
            audited_path = root / "audited.json"
            report = finalize_confirmation(
                confirmation_manifest=confirmation_path,
                raw_summary=confirmation_raw, audit_queue=confirmation_audit,
                output_path=audited_path,
            )
            self.assertTrue(report["headroom_eligible"])
            self.assertEqual(report["metrics"]["stable_timeout_problems"], 3)
            family_path = promote_operational_family(
                output_dir=root / "family", audited_confirmation=audited_path,
                seed=100019, design_count=4, gate_count=4, regression_count=4,
            )
            family = load_bundle_manifest(family_path)
            self.assertEqual(family["mechanism"]["difficulty_bin"], "hard")
            splits = {
                split: load_bundle_split(family, split)
                for split in ("design", "gate", "regression")
            }
            ensure_disjoint_splits(splits)
            promoted_ids = {
                row["mechanism_metadata"]["problem_id"]
                for rows in splits.values() for row in rows
            }
            self.assertTrue(promoted_ids.isdisjoint(calibration_ids | confirmation_ids))
            self.assertEqual(
                {row["mechanism_metadata"]["difficulty_bin"] for row in splits["design"]},
                {"hard"},
            )
            self.assertEqual(
                {row["mechanism_metadata"]["difficulty_bin"] for row in splits["regression"]},
                {"medium"},
            )

    def test_same_protocol_supports_restricted_digit_prime_mechanism(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calibration_path = generate_calibration_pool(
                output_dir=root / "calibration", source_report=self._source(root),
                seed=97001, cases_per_bin=12,
            )
            _, calibration_rows = load_bundle(calibration_path)
            mechanism = "restricted_digit_prime_enumeration"
            hard_ids = [
                row["mechanism_metadata"]["problem_id"]
                for row in calibration_rows
                if row["mechanism_metadata"]["family"] == mechanism
                and row["mechanism_metadata"]["difficulty_bin"] == "hard"
            ]
            raw, audit = self._saved_run(root, calibration_path, set(hard_ids[:4]))
            evidence = calibration_operational_evidence(
                calibration_manifest=calibration_path,
                raw_summary=raw,
                audit_queue=audit,
                mechanism=mechanism,
                difficulty="hard",
            )
            self.assertTrue(evidence["qualifies_for_fresh_confirmation"])
            confirmation_path = generate_confirmation(
                output_dir=root / "prime-confirmation",
                calibration_manifest=calibration_path,
                raw_summary=raw,
                audit_queue=audit,
                mechanism=mechanism,
                difficulty="hard",
                seed=130003,
                cases=12,
            )
            confirmation, rows = load_bundle(confirmation_path)
            self.assertEqual(confirmation["mechanism"]["name"], mechanism)
            self.assertEqual(
                {row["mechanism_metadata"]["family"] for row in rows},
                {mechanism},
            )


if __name__ == "__main__":
    unittest.main()
