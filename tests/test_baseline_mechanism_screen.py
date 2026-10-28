import inspect
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.math import MATHBenchmark
from run_baseline_mechanism_screen import (
    apply_audit_labels,
    build_audit_queue,
    finalize_audit,
    summarize_raw_observations,
)
import run_baseline_mechanism_screen as screen_module
from scripts.baseline_failure_mechanisms import (
    MECHANISMS,
    file_sha256,
    generate_bundle,
    load_bundle,
)


class BaselineFailureMechanismTests(unittest.TestCase):
    def _sources(self, root: Path) -> tuple[Path, Path]:
        report = root / "failures.json"
        dataset = root / "data.jsonl"
        report.write_text("{}\n", encoding="utf-8")
        dataset.write_text("{}\n", encoding="utf-8")
        return report, dataset

    def _bundle(self, root: Path, name: str) -> Path:
        report, dataset = self._sources(root)
        return generate_bundle(
            output_dir=root / name,
            seed=94017,
            cases_per_mechanism=12,
            source_report=report,
            source_dataset=dataset,
        )

    def test_generation_is_deterministic_exact_and_balanced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_manifest, first_rows = load_bundle(self._bundle(root, "first"))
            second_manifest, second_rows = load_bundle(self._bundle(root, "second"))
            self.assertEqual(
                first_manifest["screening_data"]["sha256"],
                second_manifest["screening_data"]["sha256"],
            )
            self.assertEqual(len(first_rows), 12 * len(MECHANISMS))
            counts = {
                mechanism: sum(
                    row["mechanism_metadata"]["family"] == mechanism
                    for row in first_rows
                )
                for mechanism in MECHANISMS
            }
            self.assertEqual(set(counts.values()), {12})
            self.assertEqual(
                len({row["mechanism_metadata"]["problem_id"] for row in first_rows}),
                len(first_rows),
            )
            scorer = MATHBenchmark("MATH", "", str(root))
            for row in first_rows:
                oracle = row["mechanism_metadata"]["oracle_answer"]
                score, _ = scorer.calculate_score(
                    row["solution"], rf"\boxed{{{oracle}}}"
                )
                self.assertEqual(score, 1, row["mechanism_metadata"])
            self.assertEqual(
                [row["problem"] for row in first_rows],
                [row["problem"] for row in second_rows],
            )

    def test_manifest_forbids_every_candidate_execution_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = load_bundle(self._bundle(root, "bundle"))
            contract = manifest["baseline_only_contract"]
            self.assertFalse(contract["candidate_generation_allowed"])
            self.assertFalse(contract["candidate_execution_allowed"])
            self.assertFalse(contract["operator_library_access_allowed"])
            self.assertFalse(contract["routing_allowed"])
        globals_used = vars(screen_module)
        self.assertNotIn("OperatorLibrary", globals_used)
        self.assertNotIn("ContextAwareOperatorRouter", globals_used)
        source = inspect.getsource(screen_module.BaselineTwoStageSolver)
        self.assertNotIn("candidate", source.lower())
        self.assertNotIn("routing", source.lower())

    def test_raw_threshold_requires_rate_and_three_stable_problems(self):
        protocol = {
            "stable_failure_min_failed_runs": 2,
            "min_audited_error_rate": 0.20,
            "min_stable_failed_problems": 3,
        }
        observations = []
        for problem in range(4):
            for run in range(1, 4):
                observations.append(
                    {
                        "mechanism_id": "eligible",
                        "problem_id": f"p{problem}",
                        "run": run,
                        "score": 0 if problem < 3 and run <= 2 else 1,
                    }
                )
                observations.append(
                    {
                        "mechanism_id": "too_few_stable",
                        "problem_id": f"q{problem}",
                        "run": run,
                        "score": 0 if problem == 0 and run <= 2 else 1,
                    }
                )
        summaries = {
            row["mechanism_id"]: row
            for row in summarize_raw_observations(observations, protocol)
        }
        self.assertTrue(summaries["eligible"]["preliminary_headroom_eligible"])
        self.assertFalse(
            summaries["too_few_stable"]["preliminary_headroom_eligible"]
        )

    def test_manual_audit_can_remove_scorer_false_negatives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observations = []
            for problem in range(3):
                for run in range(1, 4):
                    score = 0 if run <= 2 else 1
                    observations.append(
                        {
                            "mechanism_id": "m",
                            "problem_id": f"p{problem}",
                            "run": run,
                            "score": score,
                            "problem": "problem",
                            "reference_solution": r"\boxed{1}",
                            "oracle_answer": "1",
                            "prediction": r"\boxed{2}" if score == 0 else r"\boxed{1}",
                            "extracted": "2" if score == 0 else "1",
                            "error": "",
                        }
                    )
            observations_path = root / "observations.jsonl"
            observations_path.write_text(
                "".join(json.dumps(row) + "\n" for row in observations),
                encoding="utf-8",
            )
            queue = build_audit_queue(observations)
            for row in queue:
                row["audit_label"] = (
                    "scorer_false_negative"
                    if row["problem_id"] == "p2"
                    else "true_model_error"
                )
            queue_path = root / "queue.jsonl"
            queue_path.write_text(
                "".join(json.dumps(row) + "\n" for row in queue),
                encoding="utf-8",
            )
            raw_path = root / "raw.json"
            raw_path.write_text(
                json.dumps(
                    {
                        "observations": str(observations_path),
                        "observations_sha256": file_sha256(observations_path),
                        "scorer_version": "math_equivalence_v6",
                        "protocol": {
                            "stable_failure_min_failed_runs": 2,
                            "min_audited_error_rate": 0.20,
                            "min_stable_failed_problems": 3,
                        },
                    }
                ),
                encoding="utf-8",
            )
            report = finalize_audit(
                raw_summary_path=raw_path,
                audit_queue_path=queue_path,
                output_path=root / "final.json",
            )
            mechanism = report["mechanisms"][0]
            self.assertEqual(mechanism["audited_true_failures"], 4)
            self.assertEqual(mechanism["audited_scorer_false_negatives"], 2)
            self.assertEqual(mechanism["stable_failed_problems"], 2)
            self.assertFalse(mechanism["headroom_eligible"])

    def test_apply_audit_labels_requires_exact_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "queue.jsonl"
            queue_path.write_text(
                json.dumps({"audit_id": "a", "audit_label": None}) + "\n",
                encoding="utf-8",
            )
            ledger_path = root / "labels.json"
            ledger_path.write_text(
                json.dumps(
                    {
                        "labels": [
                            {
                                "audit_id": "a",
                                "audit_label": "true_model_error",
                                "audit_notes": "independently checked",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            counts = apply_audit_labels(
                audit_queue_path=queue_path,
                labels_path=ledger_path,
            )
            self.assertEqual(counts, {"true_model_error": 1})
            updated = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["audit_label"], "true_model_error")
            self.assertEqual(updated["audit_notes"], "independently checked")

    def test_apply_audit_labels_automatically_classifies_saved_execution_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "queue.jsonl"
            rows = [
                {"audit_id": "timeout", "audit_label": None, "execution_error": "TimeoutError:"},
                {"audit_id": "math", "audit_label": None, "execution_error": ""},
            ]
            queue_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            ledger_path = root / "labels.json"
            ledger_path.write_text(
                json.dumps(
                    {
                        "labels": [
                            {
                                "audit_id": "math",
                                "audit_label": "true_model_error",
                                "audit_notes": "checked",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            counts = apply_audit_labels(
                audit_queue_path=queue_path, labels_path=ledger_path
            )
            self.assertEqual(counts, {"execution_error": 1, "true_model_error": 1})
            updated = [json.loads(line) for line in queue_path.read_text().splitlines()]
            self.assertEqual(updated[0]["audit_label"], "execution_error")


if __name__ == "__main__":
    unittest.main()
