import json
import tempfile
import unittest
from pathlib import Path

from scripts.contextual_operator_router import ContextAwareOperatorRouter
from scripts.operator_library import OperatorLibrary


POSITIVE = (
    "How many ways are there to arrange 6 beads of distinct colors in a "
    "2 x 3 grid if rotations and reflections are considered the same?"
)
POSITIVE_VARIANT = (
    "How many ways are there to arrange 9 beads of distinct colors in a 3 x 3 grid "
    "if rotations and reflections are considered equivalent?"
)
NEGATIVE = (
    "In how many ways can 8 people sit around a round table? "
    "Treat rotations as not distinct but reflections as distinct."
)


class FakeSpecialist:
    async def __call__(self, problem):
        return {"status": "success", "response": "specialist"}


class FakeErrorSpecialist:
    async def __call__(self, problem):
        return {"status": "error", "response": "", "observation": "bad"}


class FakeDecliningSpecialist:
    async def __call__(self, problem):
        return {
            "status": "declined",
            "response": "",
            "observation": "outside specialist scope",
        }


class FakeExceptionSpecialist:
    async def __call__(self, problem):
        raise RuntimeError("boom")


class FakeFallback:
    async def __call__(self, problem):
        return {"status": "success", "response": "fallback"}


class FakeExceptionFallback:
    async def __call__(self, problem):
        raise RuntimeError("fallback boom")


class FakeLLM:
    def __init__(self):
        self.prompts = []

    async def call_with_format(self, prompt, formatter):
        self.prompts.append(prompt)
        return {"status": "success", "response": f"fake-{len(self.prompts)}"}


class ContextualRouterTests(unittest.IsolatedAsyncioTestCase):
    def make_router(
        self,
        directory,
        specialist_factory=FakeSpecialist,
        include_evidence=True,
        status="experimental",
    ):
        path = Path(directory) / "operators.json"
        library = OperatorLibrary(path)
        library.upsert(
            {
                "name": "Specialist",
                "class_name": "Specialist",
                "status": status,
                "version": 1,
                "artifact": {"artifact_sha256": "test-artifact-v1"},
                "applicability_rules": [
                    {
                        "name": "finite_geometric_symmetry",
                        "required_features": [
                            "burnside_safe",
                            "explicit_rotation_equivalence",
                            "explicit_reflection_equivalence",
                        ],
                        "weight": 0.8,
                    }
                ],
                "failure_boundaries": [
                    {
                        "name": "round-table",
                        "features_any": ["circular_seating"],
                        "hard_block": True,
                        "reason": "known negative transfer",
                    }
                ],
            }
        )
        if include_evidence:
            library.record_evidence(
                "Specialist",
                {
                    "scope": "finite_geometric_symmetry",
                    "baseline": "generic_two_stage",
                    "protocol": "paired_equal_call_unit_test",
                    "routing_eligible": True,
                    "wins": 2,
                    "ties": 4,
                    "losses": 0,
                    "unique_problems": 2,
                    "unique_winning_problems": 2,
                },
            )
        return ContextAwareOperatorRouter(
            llm=None,
            library_path=str(path),
            operator_module="unused",
            operator_factories={"Specialist": lambda _llm: specialist_factory()},
            fallback=FakeFallback(),
        )

    async def test_positive_uses_specialist_and_negative_uses_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.make_router(directory)
            positive = await router(POSITIVE)
            self.assertEqual(positive["response"], "specialist")
            self.assertEqual(positive["selected_operator"], "Specialist")
            self.assertFalse(positive["used_fallback"])

            negative = await router(NEGATIVE)
            self.assertEqual(negative["response"], "fallback")
            self.assertTrue(negative["used_fallback"])
            self.assertEqual(negative["fallback_reason"], "no_safe_match")
            self.assertTrue(negative["route"]["blocked"])

    async def test_experimental_operator_without_evidence_is_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.make_router(directory, include_evidence=False)
            result = await router(POSITIVE)
            self.assertEqual(result["response"], "fallback")
            self.assertTrue(result["used_fallback"])
            self.assertIsNone(result["route"]["selected"])
            self.assertFalse(result["route"]["candidates"][0]["eligible"])
            self.assertTrue(
                result["route"]["candidates"][0]["evidence_gate_reasons"]
            )

    async def test_specialist_error_and_exception_fall_back(self):
        for factory in (FakeErrorSpecialist, FakeExceptionSpecialist):
            with self.subTest(factory=factory.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    router = self.make_router(directory, factory)
                    result = await router(POSITIVE)
                    self.assertEqual(result["response"], "fallback")
                    self.assertTrue(result["used_fallback"])
                self.assertIn("specialist_", result["fallback_reason"])

    async def test_specialist_decline_uses_generic_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.make_router(directory, FakeDecliningSpecialist)
            result = await router(POSITIVE)
            self.assertEqual(result["response"], "fallback")
            self.assertTrue(result["used_fallback"])
            self.assertIn("specialist_declined", result["fallback_reason"])

    async def test_missing_or_malformed_library_falls_back_without_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            router = ContextAwareOperatorRouter(
                llm=None,
                library_path=str(missing),
                operator_module="unused",
                fallback=FakeFallback(),
            )
            result = await router(POSITIVE)
            self.assertTrue(result["used_fallback"])
            self.assertIn("library_unavailable", result["fallback_reason"])
            self.assertFalse(missing.exists())

            malformed = Path(directory) / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            router = ContextAwareOperatorRouter(
                llm=None,
                library_path=str(malformed),
                operator_module="unused",
                fallback=FakeFallback(),
            )
            result = await router(POSITIVE)
            self.assertTrue(result["used_fallback"])
            self.assertIn("library_unavailable", result["fallback_reason"])

            invalid_record = Path(directory) / "invalid-record.json"
            invalid_record.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "operators": {"Broken": {"status": "active"}},
                    }
                ),
                encoding="utf-8",
            )
            router = ContextAwareOperatorRouter(
                llm=None,
                library_path=str(invalid_record),
                operator_module="unused",
                fallback=FakeFallback(),
            )
            result = await router(POSITIVE)
            self.assertTrue(result["used_fallback"])
            self.assertIn("library_unavailable", result["fallback_reason"])

    async def test_fallback_exception_is_returned_as_auditable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.make_router(directory, include_evidence=False)
            router.fallback = FakeExceptionFallback()
            result = await router(POSITIVE)
            self.assertEqual(result["status"], "error")
            self.assertTrue(result["used_fallback"])
            self.assertIn("fallback failed", result["observation"])

    async def test_feedback_records_paired_win_once(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.make_router(
                directory, include_evidence=False, status="candidate"
            )
            router.record_feedback(
                problem=POSITIVE,
                operator_name="Specialist",
                operator_correct=True,
                baseline_correct=False,
                source="unit-test",
                run_id="run-1",
            )
            router.record_feedback(
                problem=POSITIVE,
                operator_name="Specialist",
                operator_correct=True,
                baseline_correct=False,
                source="unit-test",
                run_id="run-1",
            )
            evidence = router.library.get("Specialist")["evidence"]
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["wins"], 1)
            self.assertEqual(evidence[0]["scope"], "finite_geometric_symmetry")

    async def test_candidate_can_collect_scoped_evidence_then_be_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.make_router(
                directory, include_evidence=False, status="candidate"
            )
            for problem_index, problem in enumerate((POSITIVE, POSITIVE_VARIANT)):
                for run_index in range(3):
                    router.record_feedback(
                        problem=problem,
                        operator_name="Specialist",
                        operator_correct=True,
                        baseline_correct=False,
                        source="unit-test",
                        run_id=f"problem-{problem_index}-run-{run_index}",
                    )

            before_promotion = await router(POSITIVE)
            self.assertTrue(before_promotion["used_fallback"])

            router.library.set_status(
                "Specialist", "experimental", "paired evidence collected"
            )
            after_promotion = await router(POSITIVE)
            self.assertFalse(after_promotion["used_fallback"])
            self.assertEqual(after_promotion["selected_operator"], "Specialist")

    async def test_unverified_math_template_candidate_always_falls_back(self):
        from workspace.MATH.workflows.template.operator import (
            ContextAwareOperatorRouter as TemplateRouter,
        )

        root = Path(__file__).resolve().parents[1]
        rows = [
            json.loads(line)
            for line in (root / "data/datasets/math_validate.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        llm = FakeLLM()
        router = TemplateRouter(llm)

        positive = await router(rows[102]["problem"])
        self.assertIsNone(positive["selected_operator"])
        self.assertTrue(positive["used_fallback"])
        self.assertEqual(len(llm.prompts), 2)

        negative = await router(rows[112]["problem"])
        self.assertIsNone(negative["selected_operator"])
        self.assertTrue(negative["used_fallback"])
        self.assertEqual(len(llm.prompts), 4)


if __name__ == "__main__":
    unittest.main()
