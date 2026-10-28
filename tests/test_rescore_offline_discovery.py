import json
import tempfile
import unittest
from pathlib import Path

from scripts.offline_loop_protocol import LoopConfig, atomic_json, digest, file_hash
from scripts.rescore_offline_discovery import rescore_discovery, tree_hashes


class OfflineDiscoveryRescoreTests(unittest.TestCase):
    def fixture(self, root: Path) -> None:
        config = LoopConfig(opt_model="opt", exec_model="exec", discovery_runs=2)
        atomic_json(root / "manifest.json", {
            "pipeline": "aflow_offline_loop_v1", "config": config.__dict__,
            "counts": {"discovery": 3},
            "runtime_environment": {"scorer_version": "math_equivalence_v8"},
        })
        rows = [
            {"problem_id": "choice", "problem": r"Pick: \text{(A) } 2\quad\text{(B) } 3",
             "solution": r"\boxed{2}"},
            {"problem_id": "text", "problem": "Name the color", "solution": r"\boxed{orange}"},
            {"problem_id": "error", "problem": "x", "solution": r"\boxed{1}"},
        ]
        atomic_json(root / "inputs/discovery.json", rows)
        observations = []
        values = {"choice": ("A", ""), "text": ("Orange", ""), "error": ("", "TimeoutError")}
        for row in rows:
            prediction, error = values[row["problem_id"]]
            for run in (1, 2):
                saved = {"score": 0, "prediction": rf"\boxed{{{prediction}}}" if prediction else "",
                         "extracted": prediction, "error": error}
                wrapper = {"input_sha256": "fixture", "output_sha256": digest(saved), "output": saved}
                atomic_json(root / "checkpoints/discovery/baseline" / row["problem_id"] / f"{run}.json", wrapper)
                observations.append({**saved, "problem_id": row["problem_id"], "run": run})
        old_screen = {"selected": None, "stable_problem_count": 2, "groups": []}
        atomic_json(root / "checkpoints/screen.json", {
            "input_sha256": "fixture", "output_sha256": digest(old_screen), "output": old_screen,
        })
        atomic_json(root / "summary.json", {"status": "archived_no_stable_mechanism"})

    def test_rescore_is_read_only_deterministic_and_excludes_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            self.fixture(root)
            before = tree_hashes(root)
            output = Path(directory) / "audit"
            summary_path, report = rescore_discovery(root, output)
            self.assertEqual(tree_hashes(root), before)
            self.assertEqual(report["counts"], {
                "observations": 6, "original_correct": 0, "original_wrong": 4,
                "rescored_correct": 4, "rescored_wrong": 0, "execution_errors": 2,
                "score_changes": 4,
                "changes_by_reason": {"choice_label_mapping": 2,
                                      "plain_text_case_normalization": 2},
            })
            self.assertEqual(report["rescored_screen"]["stable_problem_count"], 0)
            self.assertEqual(report["request_files_before_after"], [0, 0])
            self.assertTrue(summary_path.is_file())
            details = [json.loads(line) for line in (output / "details.jsonl").read_text().splitlines()]
            self.assertEqual(details[0]["predicted_resolved"], "2")
            self.assertTrue((output / "human_audit.md").is_file())
            with self.assertRaises(FileExistsError):
                rescore_discovery(root, output)

    def test_checkpoint_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            self.fixture(root)
            path = root / "checkpoints/discovery/baseline/choice/1.json"
            wrapper = json.loads(path.read_text())
            wrapper["output"]["score"] = 1
            path.write_text(json.dumps(wrapper))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                rescore_discovery(root, Path(directory) / "audit")

    def test_audit_cannot_write_inside_frozen_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            self.fixture(root)
            before = tree_hashes(root)
            with self.assertRaisesRegex(ValueError, "outside the frozen run"):
                rescore_discovery(root, root / "audit")
            self.assertEqual(tree_hashes(root), before)


if __name__ == "__main__":
    unittest.main()
