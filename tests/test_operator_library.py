import ast
import json
import tempfile
import unittest
from pathlib import Path

from scripts.operator_library import (
    OperatorLibrary,
    ProblemFeatureExtractor,
    combined_artifact_sha256,
    semantic_python_sha256,
)


ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class FeatureExtractionTests(unittest.TestCase):
    def test_parenthesized_arithmetic_expression_is_algebra_and_counting(self):
        context = ProblemFeatureExtractor().extract(
            "Consider the expression 2 - 5 - 3 * 4. Fully parenthesize the "
            "expression in every possible way. How many distinct values are possible?"
        )
        self.assertIn("algebra_problem", context.features)
        self.assertIn("counting_problem", context.features)
        self.assertIn("distinct_objects", context.features)
        self.assertIn(
            "parenthesization_distinct_value_dedup", context.features
        )

    def test_parenthesization_near_misses_fail_closed(self):
        for problem in (
            "Fully parenthesize 2 + 3 * 4. What is the largest value?",
            "How many full parenthesizations of 2 + 3 * 4 are possible?",
            "How many full parenthesizations are possible? Do not count "
            "distinct values; count every form separately.",
        ):
            with self.subTest(problem=problem):
                context = ProblemFeatureExtractor().extract(problem)
                self.assertNotIn(
                    "parenthesization_distinct_value_dedup", context.features
                )

    @classmethod
    def setUpClass(cls):
        cls.validate = load_jsonl(ROOT / "data/datasets/math_validate.jsonl")
        cls.test = load_jsonl(ROOT / "data/datasets/math_test.jsonl")
        cls.extractor = ProblemFeatureExtractor()

    def test_finite_rotation_reflection_layout_is_burnside_safe(self):
        for index in (102, 118):
            features = self.extractor.extract(self.validate[index]["problem"]).features
            self.assertIn("burnside_safe", features)
            self.assertIn("explicit_rotation_equivalence", features)
            self.assertIn("explicit_reflection_equivalence", features)

    def test_circular_seating_semantics_are_not_reversed(self):
        features = self.extractor.extract(self.validate[112]["problem"]).features
        self.assertIn("circular_seating", features)
        self.assertIn("rotation_already_quotiented", features)
        self.assertIn("explicit_rotation_equivalence", features)
        self.assertNotIn("explicit_reflection_equivalence", features)

    def test_rule_development_language_variants(self):
        cube = self.extractor.extract(self.test[388]["problem"]).features
        self.assertIn("burnside_safe", cube)
        self.assertIn("explicit_rotation_equivalence", cube)
        self.assertNotIn("explicit_reflection_equivalence", cube)

        grid = self.extractor.extract(self.test[399]["problem"]).features
        self.assertIn("burnside_safe", grid)
        self.assertIn("explicit_rotation_equivalence", grid)
        self.assertIn("explicit_reflection_equivalence", grid)

        repeated_colors = self.extractor.extract(self.test[401]["problem"]).features
        self.assertIn("explicit_rotation_equivalence", repeated_colors)
        self.assertNotIn("burnside_safe", repeated_colors)

    def test_explicit_non_equivalence_fails_closed(self):
        for problem in (
            "How many ways can distinct beads be arranged on a grid if rotations and reflections are not considered the same?",
            "How many ways can distinct beads be arranged on a grid if rotations and reflections are considered distinct, not the same?",
        ):
            features = self.extractor.extract(problem).features
            self.assertNotIn("explicit_rotation_equivalence", features)
            self.assertNotIn("explicit_reflection_equivalence", features)
            self.assertNotIn("burnside_safe", features)


class OperatorLibraryTests(unittest.TestCase):
    def test_seed_archive_and_mutable_template_match_declared_v2_hash(self):
        library = OperatorLibrary(
            ROOT / "workspace/MATH/operator_library/operators.json",
            auto_create=False,
        )
        record = library.get("CombinatorialSymmetryDecomposer")
        artifact = record["artifact"]

        class_code = (ROOT / artifact["class_code_path"]).read_text(
            encoding="utf-8"
        )
        prompt_code = (ROOT / artifact["prompt_code_path"]).read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            semantic_python_sha256(class_code), artifact["class_sha256"]
        )
        self.assertEqual(
            semantic_python_sha256(prompt_code), artifact["prompt_sha256"]
        )
        self.assertEqual(
            combined_artifact_sha256(class_code, prompt_code),
            artifact["artifact_sha256"],
        )

        operator_source = (
            ROOT / "workspace/MATH/workflows/template/operator.py"
        ).read_text(encoding="utf-8")
        operator_tree = ast.parse(operator_source)
        class_node = next(
            node
            for node in operator_tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "CombinatorialSymmetryDecomposer"
        )
        mutable_class = ast.get_source_segment(operator_source, class_node)

        prompt_source = (
            ROOT / "workspace/MATH/workflows/template/op_prompt.py"
        ).read_text(encoding="utf-8")
        prompt_tree = ast.parse(prompt_source)
        assignments = {}
        for node in prompt_tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = ast.get_source_segment(
                        prompt_source, node
                    )
        mutable_prompt = "\n".join(
            assignments[name] for name in artifact["prompt_names"]
        )
        self.assertEqual(
            semantic_python_sha256(mutable_class), artifact["class_sha256"]
        )
        self.assertEqual(
            semantic_python_sha256(mutable_prompt), artifact["prompt_sha256"]
        )

    def test_seed_v2_candidate_is_not_routable_with_legacy_v1_evidence(self):
        library = OperatorLibrary(
            ROOT / "workspace/MATH/operator_library/operators.json",
            auto_create=False,
        )
        validate = load_jsonl(ROOT / "data/datasets/math_validate.jsonl")

        positive = library.retrieve(validate[102]["problem"])
        self.assertIsNone(positive.selected)

        negative = library.retrieve(validate[112]["problem"])
        self.assertIsNone(negative.selected)
        self.assertFalse(
            any(
                row.get("operator_name") == "CombinatorialSymmetryDecomposer"
                for row in negative.blocked
            )
        )

    def test_evidence_and_upsert_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            library = OperatorLibrary(Path(directory) / "operators.json")
            library.upsert({"name": "Example", "status": "candidate"})
            evidence = {
                "id": "same-event",
                "scope": "overall",
                "wins": 1,
                "ties": 0,
                "losses": 0,
            }
            library.record_evidence("Example", evidence)
            library.record_evidence("Example", evidence)
            self.assertEqual(len(library.get("Example")["evidence"]), 1)

            library.upsert(library.get("Example"))
            self.assertEqual(len(library.get("Example")["evidence"]), 1)

    def test_invalid_evidence_and_retrieval_arguments_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            library = OperatorLibrary(Path(directory) / "operators.json")
            library.upsert({"name": "Example", "status": "candidate"})
            with self.assertRaises(ValueError):
                library.record_evidence(
                    "Example", {"wins": -1, "ties": 0, "losses": 0}
                )
            with self.assertRaises(ValueError):
                library.record_evidence(
                    "Example",
                    {
                        "wins": 0,
                        "ties": 1,
                        "losses": 0,
                        "unique_problems": "unknown",
                    },
                )
            with self.assertRaises(ValueError):
                library.retrieve("problem", top_k=0)
            with self.assertRaises(ValueError):
                library.set_status("Example", "typo-status")

    def test_unknown_required_feature_does_not_broaden_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            library = OperatorLibrary(Path(directory) / "operators.json")
            library.upsert(
                {
                    "name": "Unsafe",
                    "status": "active",
                    "applicability_rules": [
                        {
                            "name": "bad-rule",
                            "required_features": ["not_a_real_feature"],
                            "weight": 1.0,
                        }
                    ],
                }
            )
            self.assertIsNone(library.retrieve("How many arrangements?").selected)

    def test_routing_evidence_must_match_artifact_revision_and_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            library = OperatorLibrary(Path(directory) / "operators.json")
            library.register_evolved_candidate(
                name="RevisionBound",
                description="revision test",
                module="unused",
                skeleton_type="two_stage",
                applicability_features=(
                    "counting_problem,burnside_safe,"
                    "explicit_rotation_equivalence,"
                    "explicit_reflection_equivalence"
                ),
                class_code="class RevisionBound:\n    pass\n",
                prompt_code='REVISION_PROMPT = r"""test"""\n',
            )
            library.set_status("RevisionBound", "experimental", "test")
            validate = load_jsonl(ROOT / "data/datasets/math_validate.jsonl")
            problem = validate[102]["problem"]

            library.record_evidence(
                "RevisionBound",
                {
                    "scope": "generated_applicability",
                    "operator_version": 0,
                    "artifact_sha256": "wrong-revision",
                    "baseline": "generic_two_stage",
                    "protocol": "paired_equal_call_test",
                    "routing_eligible": True,
                    "wins": 6,
                    "ties": 0,
                    "losses": 0,
                    "unique_problems": 2,
                    "unique_winning_problems": 2,
                },
            )
            self.assertIsNone(library.retrieve(problem).selected)

            library.record_evidence(
                "RevisionBound",
                {
                    "id": "correct-revision",
                    "scope": "generated_applicability",
                    "baseline": "generic_two_stage",
                    "protocol": "paired_equal_call_test",
                    "routing_eligible": True,
                    "wins": 6,
                    "ties": 0,
                    "losses": 0,
                    "unique_problems": 2,
                    "unique_winning_problems": 2,
                },
            )
            self.assertEqual(
                library.retrieve(problem).selected.operator_name,
                "RevisionBound",
            )

    def test_archived_artifact_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template"
            template.mkdir()
            (template / "operator.py").write_text(
                "class Operator:\n    pass\n\n"
                "class ArchivedSolver(Operator):\n    stale = True\n",
                encoding="utf-8",
            )
            (template / "op_prompt.py").write_text(
                'ARCHIVED_PROMPT = "stale"\n', encoding="utf-8"
            )
            (template / "operator.json").write_text("{}\n", encoding="utf-8")

            library = OperatorLibrary(root / "library" / "operators.json")
            library.register_evolved_candidate(
                name="ArchivedSolver",
                description="test artifact",
                module="unused",
                skeleton_type="single_step",
                class_code="class ArchivedSolver(Operator):\n    pass\n",
                prompt_code='ARCHIVED_PROMPT = r"""test"""\n',
            )
            library.register_evolved_candidate(
                name="UnverifiedCandidate",
                description="unverified",
                module="unused",
                skeleton_type="single_step",
                class_code="class UnverifiedCandidate(Operator):\n    pass\n",
            )
            library.set_status("ArchivedSolver", "experimental", "test evidence")
            library.install_routable_artifacts(template)
            library.install_routable_artifacts(template)

            operator_text = (template / "operator.py").read_text(encoding="utf-8")
            prompt_text = (template / "op_prompt.py").read_text(encoding="utf-8")
            self.assertEqual(operator_text.count("class ArchivedSolver"), 1)
            self.assertNotIn("stale = True", operator_text)
            self.assertNotIn("class UnverifiedCandidate", operator_text)
            self.assertEqual(prompt_text.count("ARCHIVED_PROMPT ="), 1)
            self.assertNotIn('ARCHIVED_PROMPT = "stale"', prompt_text)
            self.assertEqual(
                prompt_text.count(
                    "# ========== Restored revision-bound library prompts =========="
                ),
                1,
            )
            metadata = json.loads(
                (template / "operator.json").read_text(encoding="utf-8")
            )
            self.assertTrue(metadata["ArchivedSolver"]["contextual_routing"])


if __name__ == "__main__":
    unittest.main()
