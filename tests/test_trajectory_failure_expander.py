import csv
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.math import MATHBenchmark
from scripts.operator_library import ProblemFeatureExtractor, stable_problem_id
from scripts.trajectory_failure_expander import (
    ensure_disjoint_splits,
    generate_bundle,
    load_bundle_manifest,
    load_bundle_split,
    render_visible_design_cases,
)


class TrajectoryFailureExpanderTests(unittest.TestCase):
    def _source_files(self, root: Path) -> tuple[Path, Path]:
        source = root / "source.jsonl"
        problem = (
            "A painted point has position $(x,y)=(t^2,t^2)$ for "
            "$-1\\le t\\le2$. Find the length of its paint trail."
        )
        source.write_text(
            json.dumps(
                {
                    "problem": problem,
                    "level": "Level 5",
                    "type": "Precalculus",
                    "solution": r"The image is a segment, so $\boxed{4\sqrt{2}}$.",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        baseline = root / "baseline.csv"
        with baseline.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["index", "score", "prediction", "extracted"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "index": 0,
                    "score": 0,
                    "prediction": r"$\boxed{5\sqrt{2}}$",
                    "extracted": r"5\sqrt{2}",
                }
            )
        return source, baseline

    def _bundle(self, root: Path, name: str) -> Path:
        source, baseline = self._source_files(root)
        return generate_bundle(
            output_dir=root / name,
            seed=91001,
            design_count=5,
            gate_count=4,
            regression_count=4,
            source_data=source,
            source_index=0,
            source_run_csv=baseline,
        )

    def test_generation_is_deterministic_and_splits_are_disjoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = load_bundle_manifest(self._bundle(root, "one"))
            second = load_bundle_manifest(self._bundle(root, "two"))
            for split in ("design", "gate", "regression"):
                self.assertEqual(
                    first["splits"][split]["sha256"],
                    second["splits"][split]["sha256"],
                )
            rows = {
                split: load_bundle_split(first, split)
                for split in ("design", "gate", "regression")
            }
            ensure_disjoint_splits(rows)
            ids = [
                stable_problem_id(row["problem"])
                for split_rows in rows.values()
                for row in split_rows
            ]
            self.assertEqual(len(ids), len(set(ids)))

    def test_hidden_rows_are_not_rendered_for_operator_design(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = load_bundle_manifest(self._bundle(root, "bundle"))
            design = load_bundle_split(manifest, "design")
            gate = load_bundle_split(manifest, "gate")
            regression = load_bundle_split(manifest, "regression")
            rendered = render_visible_design_cases(design)
            self.assertIn(design[0]["problem"], rendered)
            for row in [*gate, *regression]:
                self.assertNotIn(row["problem"], rendered)
            self.assertIn("Only these design examples are visible", rendered)

    def test_generated_oracles_score_as_exact_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = load_bundle_manifest(self._bundle(root, "bundle"))
            scorer = MATHBenchmark("MATH", "", str(root))
            for split in ("design", "gate", "regression"):
                for row in load_bundle_split(manifest, split):
                    metadata = row["mechanism_metadata"]
                    if metadata["origin"] != "generated_exact_oracle":
                        continue
                    prediction = rf"The result is \boxed{{{metadata['oracle_answer']}}}."
                    score, _ = scorer.calculate_score(row["solution"], prediction)
                    self.assertEqual(score, 1, (split, metadata))

    def test_gate_contains_both_capabilities_and_regression_is_hard_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = load_bundle_manifest(self._bundle(root, "bundle"))
            gate = load_bundle_split(manifest, "gate")
            regression = load_bundle_split(manifest, "regression")
            gate_modes = {row["mechanism_metadata"]["mode"] for row in gate}
            regression_modes = {
                row["mechanism_metadata"]["mode"] for row in regression
            }
            self.assertEqual(gate_modes, {"retraced_trail", "monotone_trail"})
            self.assertEqual(regression_modes, {"distance_traveled"})

    def test_generated_problems_expose_safe_routing_features(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = load_bundle_manifest(self._bundle(root, "bundle"))
            extractor = ProblemFeatureExtractor()
            gate = load_bundle_split(manifest, "gate")
            for row in gate:
                features = extractor.extract(row["problem"]).features
                self.assertIn("parametric_curve", features)
                self.assertIn("trajectory_trace", features)
                self.assertIn("path_retracing_candidate", features)
            distance = next(
                row
                for row in load_bundle_split(manifest, "regression")
                if row["mechanism_metadata"]["mode"] == "distance_traveled"
            )
            distance_features = extractor.extract(distance["problem"]).features
            self.assertIn("distance_traveled_request", distance_features)

    def test_confirmatory_manifest_freezes_candidate_and_run_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline = self._source_files(root)
            path = generate_bundle(
                output_dir=root / "confirm",
                seed=92017,
                design_count=5,
                gate_count=4,
                regression_count=4,
                source_data=source,
                source_index=0,
                source_run_csv=baseline,
                experiment_role="confirmatory",
                parent_pilot_batch_id="pilot-1",
                primary_candidate="FrozenCandidate",
                confirmation_runs=3,
                pilot_record_sha256="abc123",
            )
            manifest = load_bundle_manifest(path)
            protocol = manifest["confirmation_protocol"]
            self.assertEqual(manifest["experiment_role"], "confirmatory")
            self.assertEqual(protocol["parent_pilot_batch_id"], "pilot-1")
            self.assertEqual(protocol["primary_candidate"], "FrozenCandidate")
            self.assertEqual(protocol["num_runs"], 3)
            self.assertEqual(protocol["pilot_record_sha256"], "abc123")
            self.assertFalse(protocol["promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
