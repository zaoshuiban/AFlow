import unittest
import asyncio
import tempfile
from pathlib import Path

from scripts.optimizer_utils.operator_evolution import (
    FailureDiagnosis,
    OperatorEvolveResponse,
    OperatorEvolver,
)


def make_response(**overrides):
    values = {
        "operator_name": "FiniteSymmetrySolver",
        "description": "Solves a narrow finite symmetry slice.",
        "reasoning": "Use an orbit-counting representation.",
        "skeleton_type": "two_stage",
        "prompt_1": 'Analyze "orbits" and preserve \\boxed{}.',
        "prompt_1_name": "FINITE_ANALYSIS_PROMPT",
        "prompt_2": "Solve carefully and put the answer in \\boxed{}.",
        "prompt_2_name": "FINITE_SOLVE_PROMPT",
        "applicability_features": (
            "counting_problem,burnside_safe,explicit_reflection_equivalence"
        ),
        "failure_boundary_features": "circular_seating",
    }
    values.update(overrides)
    return OperatorEvolveResponse(**values)


class OperatorEvolutionMetadataTests(unittest.TestCase):
    def setUp(self):
        self.evolver = OperatorEvolver.__new__(OperatorEvolver)

    def test_generated_prompt_source_preserves_latex_backslashes(self):
        response = make_response()
        code = self.evolver._generate_prompt_code(response)
        namespace = {}
        exec(compile(code, "<generated-prompts>", "exec"), namespace)
        self.assertIn("\\boxed", namespace["FINITE_ANALYSIS_PROMPT"])
        self.assertNotIn("\x08oxed", namespace["FINITE_ANALYSIS_PROMPT"])
        self.assertIn('"orbits"', namespace["FINITE_ANALYSIS_PROMPT"])

    def test_unknown_declarative_feature_is_rejected(self):
        valid, error = self.evolver._validate_response(
            make_response(applicability_features="burnside_safe,arbitrary_code")
        )
        self.assertFalse(valid)
        self.assertIn("arbitrary_code", error)

    def test_known_declarative_features_are_accepted(self):
        valid, error = self.evolver._validate_response(make_response())
        self.assertTrue(valid, error)

    def test_prompt_only_policy_rejects_programmer_skeleton(self):
        valid, error = self.evolver._validate_design_policy(
            make_response(skeleton_type="multi_strategy"),
            {
                "programmer_allowed": False,
                "allowed_skeleton_types": ["single_step", "two_stage", "verify_fix"],
                "max_llm_calls_per_problem": 2,
            },
        )
        self.assertFalse(valid)
        self.assertIn("multi_strategy", error)
        self.assertIn("programmer_allowed=false", error)
        self.assertIn("exceeding max 2", error)

    def test_prompt_only_policy_accepts_two_stage_skeleton(self):
        valid, error = self.evolver._validate_design_policy(
            make_response(skeleton_type="two_stage"),
            {
                "programmer_allowed": False,
                "allowed_skeleton_types": ["single_step", "two_stage", "verify_fix"],
                "max_llm_calls_per_problem": 2,
            },
        )
        self.assertTrue(valid, error)

    def test_tool_policy_accepts_only_programmer_step(self):
        valid, error = self.evolver._validate_design_policy(
            make_response(
                skeleton_type="programmer_step",
                prompt_2="",
                prompt_2_name="",
            ),
            {
                "programmer_allowed": True,
                "allowed_skeleton_types": ["programmer_step"],
                "max_llm_calls_per_problem": 1,
            },
        )
        self.assertTrue(valid, error)

    def test_prompt_only_policy_rejects_programmer_step(self):
        valid, error = self.evolver._validate_design_policy(
            make_response(
                skeleton_type="programmer_step",
                prompt_2="",
                prompt_2_name="",
            ),
            {
                "programmer_allowed": False,
                "allowed_skeleton_types": ["single_step", "two_stage"],
                "max_llm_calls_per_problem": 2,
            },
        )
        self.assertFalse(valid)
        self.assertIn("programmer_allowed=false", error)

    def test_compiled_programmer_step_preserves_code_and_output(self):
        response = make_response(
            operator_name="ExactToolSolver",
            skeleton_type="programmer_step",
            prompt_1="Use interval dynamic programming and return one integer.",
            prompt_1_name="EXACT_TOOL_PROMPT",
            prompt_2="",
            prompt_2_name="",
            applicability_features="counting_problem",
            failure_boundary_features="geometry_problem",
        )
        class_code = self.evolver._generate_class_code(response)

        class FakeOperator:
            def __init__(self, llm, name):
                self.llm = llm

        class FakeProgrammer:
            def __init__(self, llm, name):
                pass

            async def __call__(self, problem, analysis):
                return {"code": "solve = 17", "output": "17"}

        namespace = {
            "Operator": FakeOperator,
            "AsyncLLM": object,
            "Programmer": FakeProgrammer,
            "EXACT_TOOL_PROMPT": response.prompt_1,
        }
        exec(compile(class_code, "<programmer-step>", "exec"), namespace)
        specialist = namespace[response.operator_name](None)
        result = asyncio.run(
            specialist(
                "How many distinct values can be obtained from an algebra expression?"
            )
        )
        self.assertEqual(result["status"], "success")
        self.assertIn("\\boxed{17}", result["response"])
        self.assertEqual(result["tool_trace"]["code"], "solve = 17")
        self.assertEqual(result["tool_trace"]["output"], "17")

    def test_routing_condition_rejects_arbitrary_code(self):
        valid, error = self.evolver._validate_response(
            make_response(
                skeleton_type="multi_strategy",
                routing_condition="__import__('os').system('echo unsafe') == 0",
                prompt_2="",
                prompt_2_name="",
            )
        )
        self.assertFalse(valid)
        self.assertIn("越界", error)

    def test_compiled_specialist_declines_outside_declared_scope(self):
        response = make_response(
            skeleton_type="single_step",
            prompt_2="",
            prompt_2_name="",
            applicability_features="counting_problem,explicit_rotation_equivalence",
            failure_boundary_features="circular_seating",
        )
        class_code = self.evolver._generate_class_code(response)

        class FakeOperator:
            def __init__(self, llm, name):
                self.llm = llm

        class FakeCustom:
            calls = 0

            def __init__(self, llm, name):
                pass

            async def __call__(self, input, instruction):
                self.calls += 1
                return {"status": "success", "response": "answer \\boxed{1}"}

        namespace = {
            "Operator": FakeOperator,
            "AsyncLLM": object,
            "Custom": FakeCustom,
            response.prompt_1_name: response.prompt_1,
        }
        exec(compile(class_code, "<compiled-specialist>", "exec"), namespace)
        specialist = namespace[response.operator_name](None)
        declined = asyncio.run(
            specialist("In how many ways can 8 people sit around a round table?")
        )
        self.assertEqual(declined["status"], "declined")
        self.assertIn("specialist_not_applicable", declined["observation"])
        self.assertEqual(specialist.custom.calls, 0)


class StagedEvolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_batch_is_archived_without_template_install(self):
        class FakeEvolver(OperatorEvolver):
            async def diagnose_failures(self, **kwargs):
                return FailureDiagnosis(
                    dominant_error_type="counting",
                    root_cause="missing orbit representation",
                    missing_capability="canonical symmetry counting",
                    evidence="cases 1 and 2",
                    applicable_when="finite layouts",
                    not_applicable_when="round tables",
                    success_criteria="two unique wins",
                )

            async def design_candidates(self, **kwargs):
                first = make_response(
                    operator_name="OrbitCanonicalizer",
                    skeleton_type="single_step",
                    prompt_2="",
                    prompt_2_name="",
                    mechanism="canonical forms",
                )
                second = make_response(
                    operator_name="BurnsideTableSolver",
                    skeleton_type="single_step",
                    prompt_2="",
                    prompt_2_name="",
                    mechanism="fixed-point table",
                )
                return [first, second]

        with tempfile.TemporaryDirectory() as directory:
            evolver = FakeEvolver(
                root_path=directory,
                optimize_llm=None,
                dataset="MATH",
            )
            batch = await evolver.evolve_candidates(
                bottleneck_info="stagnation",
                failure_cases="two failures",
                graph_code="class Workflow: pass",
                operator_descriptions="Custom",
                candidate_count=2,
            )
            self.assertEqual(len(batch["candidate_names"]), 2)
            self.assertTrue(Path(batch["manifest_path"]).is_file())
            self.assertFalse((Path(directory) / "workflows" / "template").exists())
            for name in batch["candidate_names"]:
                self.assertEqual(
                    evolver.operator_library.get(name)["status"], "candidate"
                )


if __name__ == "__main__":
    unittest.main()
