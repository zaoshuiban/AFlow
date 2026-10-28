import concurrent
import re
import sys
import traceback
from pathlib import Path
from typing import List, Optional

from tenacity import retry, stop_after_attempt, wait_fixed

from scripts.formatter import BaseFormatter, FormatError, XmlFormatter, CodeFormatter, TextFormatter
from workspace.MATH.workflows.template.operator_an import *
from workspace.MATH.workflows.template.op_prompt import *
from scripts.async_llm import AsyncLLM
from scripts.logs import logger
import asyncio


from scripts.operators import Operator
from scripts.restricted_code_execution import execute_restricted_code


class Custom(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Custom"):
        super().__init__(llm, name)

    async def __call__(self, input, instruction):
        prompt = instruction + input
        response = await self._fill_node(GenerateOp, prompt, mode="single_fill")
        return response

def run_code(code):
    return execute_restricted_code(code)


class Programmer(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Programmer"):
        super().__init__(llm, name)

    async def exec_code(self, code, timeout=30):
        loop = asyncio.get_running_loop()
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
            try:
                future = loop.run_in_executor(executor, run_code, code)
                result = await asyncio.wait_for(future, timeout=timeout)
                return result
            except asyncio.TimeoutError:
                executor.shutdown(wait=False, cancel_futures=True)
                return "Error", "Code execution timed out"
            except Exception as e:
                return "Error", f"Unknown error: {str(e)}"

    async def code_generate(self, problem, analysis, feedback, mode):
        prompt = PYTHON_CODE_VERIFIER_PROMPT.format(
            problem=problem, analysis=analysis, feedback=feedback
        )
        response = await self._fill_node(CodeGenerateOp, prompt, mode, function_name="solve")
        return response

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    async def __call__(self, problem: str, analysis: str = "None"):
        code = None
        output = None
        feedback = ""
        for i in range(3):
            code_response = await self.code_generate(problem, analysis, feedback, mode="code_fill")
            code = code_response.get("code")
            if not code:
                return {"code": code, "output": "No code generated"}
            status, output = await self.exec_code(code)
            if status == "Success":
                return {"code": code, "output": output}
            else:
                print(f"Execution error on attempt {i + 1}, error message: {output}")
                feedback = (
                    f"\nThe result of the error from the code you wrote in the previous round:\n"
                    f"Code: {code}\n\nStatus: {status}, {output}"
                )
        return {"code": code, "output": output}


class ScEnsemble(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "ScEnsemble"):
        super().__init__(llm, name)

    async def __call__(self, solutions: List[str], problem: str):
        answer_mapping = {}
        solution_text = ""
        for index, solution in enumerate(solutions):
            answer_mapping[chr(65 + index)] = index
            solution_text += f"{chr(65 + index)}: \n{str(solution)}\n\n\n"
        prompt = SC_ENSEMBLE_PROMPT.format(problem=problem, solutions=solution_text)
        response = await self._fill_node(ScEnsembleOp, prompt, mode="xml_fill")
        answer = response.get("solution_letter", "").strip().upper()
        return {"response": solutions[answer_mapping[answer]]}


class AnswerFormatter(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "AnswerFormatter"):
        super().__init__(llm, name)

    async def __call__(self, problem: str, solution: str):
        prompt = ANSWER_FORMAT_PROMPT.format(problem=problem, solution=solution)
        response = await self._fill_node(AnswerFormatOp, prompt, mode="xml_fill")
        formatted = response.get("formatted_answer", "")
        if formatted:
            if "\\boxed" not in formatted:
                formatted = f"\\boxed{{{formatted}}}"
            return {"response": formatted}
        return {"response": solution}


class ProblemRouter(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "ProblemRouter"):
        super().__init__(llm, name)

    async def __call__(self, problem: str) -> dict:
        prompt = PROBLEM_CLASSIFY_PROMPT.format(problem=problem)
        response = await self._fill_node(ProblemClassifyOp, prompt, mode="xml_fill")
        category = response.get("category", "algebra").strip().lower()
        if category not in VALID_CATEGORIES:
            category = "algebra"
        return {"category": category, "thought": response.get("thought", "")}


class MultiStrategySolver(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "MultiStrategySolver"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")
        self.programmer = Programmer(llm, "Programmer")

    async def __call__(self, problem: str):
        solutions = []
        sol1 = await self.custom(input=problem,
            instruction="Solve this problem step by step using algebraic reasoning. Show all work clearly. Put the final answer in \\boxed{{}}.")
        solutions.append(sol1["response"])
        code_result = await self.programmer(problem=problem, analysis="Solve using Python code for precise computation")
        if code_result.get("output") and code_result["output"] not in ("No code generated", "Error"):
            code_sol = f"The answer computed by Python code is {code_result['output']}. \\boxed{{{code_result['output']}}}"
            solutions.append(code_sol)
        else:
            sol2 = await self.custom(input=problem,
                instruction="Solve this problem using a different approach than direct algebra. Try working backwards from the answer or using substitution. Put the final answer in \\boxed{{}}.")
            solutions.append(sol2["response"])
        sol3 = await self.custom(input=problem,
            instruction="Solve this math problem carefully. After finding your answer, verify it by substituting back into the original conditions. Put the verified final answer in \\boxed{{}}.")
        solutions.append(sol3["response"])
        solution_text = ""
        for index, sol in enumerate(solutions):
            solution_text += f"Strategy {chr(65 + index)}:\n{sol}\n\n"
        prompt = STRATEGY_SELECT_PROMPT.format(problem=problem, solutions=solution_text)
        response = await self._fill_node(StrategySelectOp, prompt, mode="xml_fill")
        best = response.get("best_solution", "")
        if best:
            return {"response": best}
        if len(solutions) > 1:
            result = await ScEnsemble(self.llm)(solutions=solutions, problem=problem)
            return result
        return {"response": solutions[0]}


class SymbolicVerifier(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "SymbolicVerifier"):
        super().__init__(llm, name)

    @staticmethod
    def _extract_boxed_answer(text: str) -> str:
        matches = re.findall(r"\\boxed\{((?:[^{}]|{[^{}]*})*)\}", str(text))
        if matches:
            return matches[-1].strip()
        return text.strip()

    async def __call__(self, problem: str, solution: str):
        proposed_answer = self._extract_boxed_answer(solution)
        prompt = SYMBOLIC_VERIFY_PROMPT.format(problem=problem, answer=proposed_answer)
        response = await self._fill_node(SymbolicVerifyOp, prompt, mode="xml_fill")
        if response.get("is_consistent", True):
            return {"response": solution}
        else:
            corrected = response.get("corrected_answer", "")
            if corrected:
                if "\\boxed" not in corrected:
                    corrected = f"\\boxed{{{corrected}}}"
                return {"response": f"{solution}\n\nVerification found inconsistency. Corrected answer: {corrected}"}
            return {"response": solution}

# ========== Auto-evolved operator: GeometricCombinatorialDecomposer ==========

class GeometricCombinatorialDecomposer(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "GeometricCombinatorialDecomposer"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")

    async def __call__(self, problem: str) -> dict:
        # Stage 1: Analyze
        analysis = await self.custom(input=problem, instruction=DECOMPOSITION_ANALYSIS_PROMPT)
        if analysis.get("status") == "error":
            return {"status": "error", "response": "", "observation": f"Stage 1 failed: {analysis.get('observation', '')}"}
        # Stage 2: Solve with analysis context
        combined_input = f"Problem: {problem}\n\nAnalysis:\n{analysis['response']}"
        result = await self.custom(input=combined_input, instruction=STRUCTURED_SOLVE_PROMPT)
        if result.get("status") == "error":
            return {"status": "error", "response": "", "observation": f"Stage 2 failed: {result.get('observation', '')}"}
        return {"status": "success", "response": result['response'], "observation": ""}

# ========== Auto-evolved operator: CombinatorialSymmetryDecomposer ==========

# Archived fragment. It is appended to a compatible MATH operator template.
class CombinatorialSymmetryDecomposer(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "CombinatorialSymmetryDecomposer"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")

    async def __call__(self, problem: str) -> dict:
        analysis = await self.custom(input=problem, instruction=SYMMETRY_ANALYSIS_PROMPT)
        if analysis.get("status") == "error":
            return {
                "status": "error",
                "response": "",
                "observation": f"Stage 1 failed: {analysis.get('observation', '')}",
            }
        combined_input = f"Problem: {problem}\n\nAnalysis:\n{analysis['response']}"
        result = await self.custom(input=combined_input, instruction=SYMMETRY_SOLVE_PROMPT)
        if result.get("status") == "error":
            return {
                "status": "error",
                "response": "",
                "observation": f"Stage 2 failed: {result.get('observation', '')}",
            }
        return {"status": "success", "response": result["response"], "observation": ""}


class ContextAwareOperatorRouter(Operator):
    """Select an evidence-backed specialist or use an equal-budget fallback."""

    def __init__(self, llm: AsyncLLM, name: str = "ContextAwareOperatorRouter"):
        super().__init__(llm, name)
        from scripts.contextual_operator_router import (
            ContextAwareOperatorRouter as RuntimeContextAwareOperatorRouter,
        )

        self.runtime_router = RuntimeContextAwareOperatorRouter(
            llm=llm,
            library_path=str(
                Path(__file__).resolve().parents[2]
                / "operator_library"
                / "operators.json"
            ),
            operator_module=__name__,
            min_score=0.70,
        )

    async def __call__(self, problem: str) -> dict:
        return await self.runtime_router(problem)

# ========== Restored library operator: ReusableParenthesizationIntervalDPTool ==========

class ReusableParenthesizationIntervalDPTool(Operator):
    operator_revision = 2
    tool_runtime_sha256 = "224a2a2c4030e485573e4eec24846c73891d5652a842536238d661438eb30990"
    _required_features = ("parenthesization_distinct_value_dedup",)
    _blocked_features = (
        "geometry_problem", "probability_problem", "continuous_probability",
        "number_theory_problem", "parametric_curve", "trajectory_trace",
        "distance_traveled_request",
    )

    def __init__(self, llm: AsyncLLM, name: str = "ReusableParenthesizationIntervalDPTool"):
        super().__init__(llm, name)

    def _preflight(self, problem: str):
        from scripts.operator_library import ProblemFeatureExtractor
        context = ProblemFeatureExtractor().extract(problem)
        missing = sorted(set(self._required_features) - set(context.features))
        blocked = sorted(set(self._blocked_features) & set(context.features))
        if missing or blocked:
            reasons = []
            if missing:
                reasons.append("missing=" + ",".join(missing))
            if blocked:
                reasons.append("blocked=" + ",".join(blocked))
            return {
                "status": "declined", "response": "",
                "observation": "specialist_not_applicable:" + ";".join(reasons),
            }
        return None

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        from scripts.parameterized_tools import (
            PARENTHESIZATION_MECHANISM, ParameterizedToolRegistry,
        )
        result = ParameterizedToolRegistry(
            [PARENTHESIZATION_MECHANISM]
        ).solve(problem)
        trace = {
            "route": "reusable_parameterized_tool_v2",
            "operator_revision": self.operator_revision,
            "tool_runtime_sha256": self.tool_runtime_sha256,
            **result.to_dict(),
        }
        if result.status == "success":
            return {
                "status": "success", "response": result.response,
                "observation": "", "tool_trace": trace,
            }
        return {
            "status": "declined", "response": "",
            "observation": result.observation or result.status,
            "tool_trace": trace,
        }

# ========== Restored library operator: ReusableRestrictedDigitPrimeEnumerator ==========

class ReusableRestrictedDigitPrimeEnumerator(Operator):
    operator_revision = 2
    tool_runtime_sha256 = "a48c1927b8ca6b52ac919531940ac37ea67192887df13e9617e16df9a7e6bdd2"
    _required_features = ("restricted_digit_prime_enumeration",)
    _blocked_features = (
        "probability_problem", "continuous_probability", "geometry_problem",
        "algebra_problem", "people_seating", "circular_seating",
        "parametric_curve", "trajectory_trace", "distance_traveled_request",
    )

    def __init__(self, llm: AsyncLLM, name: str = "ReusableRestrictedDigitPrimeEnumerator"):
        super().__init__(llm, name)

    def _preflight(self, problem: str):
        from scripts.operator_library import ProblemFeatureExtractor
        context = ProblemFeatureExtractor().extract(problem)
        missing = sorted(set(self._required_features) - set(context.features))
        blocked = sorted(set(self._blocked_features) & set(context.features))
        if missing or blocked:
            reasons = []
            if missing:
                reasons.append("missing=" + ",".join(missing))
            if blocked:
                reasons.append("blocked=" + ",".join(blocked))
            return {
                "status": "declined", "response": "",
                "observation": "specialist_not_applicable:" + ";".join(reasons),
            }
        return None

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        from scripts.restricted_prime_tool import RestrictedDigitPrimeEnumerationTool
        result = RestrictedDigitPrimeEnumerationTool.solve(problem)
        trace = {
            "route": "reusable_restricted_prime_tool_v2",
            "operator_revision": self.operator_revision,
            "tool_runtime_sha256": self.tool_runtime_sha256,
            **result.to_dict(),
        }
        if result.status == "success":
            return {
                "status": "success", "response": result.response,
                "observation": "", "tool_trace": trace,
            }
        return {
            "status": "declined", "response": "",
            "observation": result.observation or result.status,
            "tool_trace": trace,
        }
