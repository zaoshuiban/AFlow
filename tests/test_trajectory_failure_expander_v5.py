import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.math import MATHBenchmark
from scripts.operator_library import ProblemFeatureExtractor, stable_problem_id
from scripts.trajectory_failure_expander import (
    ensure_disjoint_splits,
    load_bundle_manifest,
    load_bundle_split,
)
from scripts.trajectory_failure_expander_v5 import generate_bundle


class TrajectoryFailureExpanderV5Tests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        path = root / "source.jsonl"
        path.write_text(
            json.dumps(
                {
                    "problem": "A marker traces $(t^2,t^2)$ for $-1\\le t\\le2$. Find its ink-trail length.",
                    "solution": r"The answer is \boxed{4\sqrt{2}}.",
                    "level": "Level 5",
                    "type": "Precalculus",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _bundle(self, root: Path, name: str) -> Path:
        return generate_bundle(
            output_dir=root / name,
            seed=93031,
            design_count=7,
            gate_count=12,
            regression_count=6,
            source_data=self._source(root),
            source_index=0,
        )

    def test_v5_is_deterministic_disjoint_and_loadable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = load_bundle_manifest(self._bundle(root, "one"))
            second = load_bundle_manifest(self._bundle(root, "two"))
            self.assertEqual(first["generator_version"], "trajectory_retrace_v5")
            rows = {}
            for split in ("design", "gate", "regression"):
                self.assertEqual(
                    first["splits"][split]["sha256"],
                    second["splits"][split]["sha256"],
                )
                rows[split] = load_bundle_split(first, split)
            ensure_disjoint_splits(rows)

    def test_v5_gate_enforces_the_harder_mechanism_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = load_bundle_manifest(self._bundle(root, "bundle"))
            gate = load_bundle_split(manifest, "gate")
            generated = [row["mechanism_metadata"] for row in gate]
            self.assertEqual({row["polynomial_degree"] for row in generated}, {3, 4})
            self.assertEqual(
                {row["latent_family"] for row in generated},
                {"cubic_s_curve", "quartic_double_well"},
            )
            self.assertEqual(
                {row["representation"] for row in generated},
                {"dual_expanded", "mixed_latent"},
            )
            retraced = [row for row in generated if row["mode"] == "multi_retraced_trail"]
            monotone = [row for row in generated if row["mode"] == "monotone_trail"]
            self.assertTrue(retraced)
            self.assertTrue(all(row["retraces"] for row in retraced))
            self.assertTrue(all(row["internal_turning_points"] >= 2 for row in retraced))
            self.assertEqual(
                {row["representation"] for row in retraced},
                {"dual_expanded", "mixed_latent"},
            )
            self.assertEqual(
                {row["representation"] for row in monotone},
                {"dual_expanded", "mixed_latent"},
            )
            self.assertTrue(all(row["parameters"]["shift_den"] in {2, 3} for row in generated))

    def test_v5_exact_oracles_score_and_routing_contract_holds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = load_bundle_manifest(self._bundle(root, "bundle"))
            scorer = MATHBenchmark("MATH", "", str(root))
            extractor = ProblemFeatureExtractor()
            rows = {
                split: load_bundle_split(manifest, split)
                for split in ("design", "gate", "regression")
            }
            for split, split_rows in rows.items():
                for row in split_rows:
                    metadata = row["mechanism_metadata"]
                    if metadata["origin"] == "generated_exact_oracle":
                        prediction = rf"\boxed{{{metadata['oracle_answer']}}}"
                        score, _ = scorer.calculate_score(row["solution"], prediction)
                        self.assertEqual(score, 1, (split, stable_problem_id(row["problem"])))
            for row in rows["gate"]:
                features = extractor.extract(row["problem"]).features
                self.assertIn("parametric_curve", features)
                self.assertIn("trajectory_trace", features)
                self.assertIn("path_retracing_candidate", features)
            for row in rows["regression"]:
                features = extractor.extract(row["problem"]).features
                self.assertIn("distance_traveled_request", features)


if __name__ == "__main__":
    unittest.main()
