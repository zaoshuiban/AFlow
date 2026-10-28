# -*- coding: utf-8 -*-
"""
MATH 实验 B v5: 扩展6算子 + 全套验证 + 宽松触发的算子自动进化

对比 v4:
- v4: AST验证 + 冒烟测试 + 瓶颈检测(3轮) + 算子进化
- v5: bottleneck_window=2(宽松触发) + enable_prompt_guidance=True + 冷却3轮

核心验证: 宽松触发 + Prompt引导的算子进化闭环能否突破停滞
"""

import json
import os
import shutil
import glob
import time
import multiprocessing
from datetime import datetime

MODEL_NAME = "deepseek-chat"
MAX_ROUNDS = int(os.environ.get("MAX_ROUNDS", "15"))
VALIDATION_ROUNDS = 1
SAMPLE = 4
EXPERIMENT_NAME = os.environ.get(
    "EXPERIMENT_NAME", "expB_evolution_v8_contextual_library"
)
ALLOW_OVERWRITE = os.environ.get("ALLOW_OVERWRITE", "false").lower() == "true"

WORKFLOWS_DIR = "workspace/MATH/workflows"
TEMPLATE_DIR = f"{WORKFLOWS_DIR}/template"
RESULTS_BASE = "workspace/MATH/experiment_ablation"

EXTENDED_OPERATORS = ["Custom", "ScEnsemble", "Programmer", "ProblemRouter",
                      "AnswerFormatter", "MultiStrategySolver",
                      "SymbolicVerifier", "ContextAwareOperatorRouter"]

SEED_GRAPH_PY = '''from typing import Literal
import workspace.MATH.workflows.template.operator as operator
import workspace.MATH.workflows.round_1.prompt as prompt_custom
from scripts.async_llm import create_llm_instance

from scripts.evaluator import DatasetType

class Workflow:
    def __init__(
        self,
        name: str,
        llm_config,
        dataset: DatasetType,
    ) -> None:
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = operator.Custom(self.llm)

    async def __call__(self, problem: str):
        solution = await self.custom(input=problem, instruction="")
        return solution['response'], self.llm.get_usage_summary()["total_cost"]
'''

SEED_PROMPT_PY = '''# SEED_PROMPT = """
#
# Solve the problem. Output your final answer within \boxed{{}}.
#
# """
'''
SEED_INIT_PY = ''

EXTENDED_OPERATOR_JSON = {
    "Custom": {
        "description": "Generates anything based on customized input and instruction.",
        "interface": "custom(input: str, instruction: str) -> dict with key 'response' of type str"
    },
    "ScEnsemble": {
        "description": "Uses self-consistency to select the solution that appears most frequently in the solution list, improve the selection to enhance the choice of the best solution.",
        "interface": "sc_ensemble(solutions: List[str], problem: str) -> dict with key 'response' of type str"
    },
    "Programmer": {
        "description": "Automatically writes, executes Python code, and returns the solution based on the provided problem description and analysis. The `output` only contains the final answer. If you want to see the detailed solution process, it's recommended to retrieve the `code`.",
        "interface": "programmer(problem: str, analysis: str = 'None') -> dict with keys 'code' and 'output' of type str"
    },
    "ProblemRouter": {
        "description": "Classifies a math problem into one of 6 categories (geometry, algebra, number_theory, combinatorics, probability, trigonometry) to enable routing to the most suitable solver. Returns dict with key 'category'.",
        "interface": "problem_router(problem: str) -> dict with key 'category' of type str"
    },
    "AnswerFormatter": {
        "description": "Extracts the final answer from a solution and reformats it with proper LaTeX notation, ensuring correct \\boxed{} wrapping. Handles percentages (\\%), units (\\text{}), degrees (^\\circ), fractions (\\frac{}{}), and square roots (\\sqrt{}). Critical for MATH where format mismatches cause 40% of failures.",
        "interface": "answer_formatter(problem: str, solution: str) -> dict with key 'response' of type str"
    },
    "MultiStrategySolver": {
        "description": "Generates solutions using three distinct strategies (algebraic reasoning, code-based computation, verification-oriented reasoning), then uses LLM-based comparison to select the most reliable one. Addresses reasoning errors by combining diverse solution approaches with redundancy.",
        "interface": "multi_strategy_solver(problem: str) -> dict with key 'response' of type str"
    },
    "SymbolicVerifier": {
        "description": "Verifies a proposed answer against the problem's constraints using symbolic reasoning. Substitutes the answer back into original conditions, checks geometric properties, and verifies logical consistency. If inconsistent, provides a corrected answer. Addresses complex problem failures where the model's reasoning direction is wrong.",
        "interface": "symbolic_verifier(problem: str, solution: str) -> dict with key 'response' of type str"
    },
    "ContextAwareOperatorRouter": {
        "description": "Uses persistent applicability rules, hard failure boundaries, and historical evidence to select a safe evolved operator, otherwise falling back to generic two-stage reasoning.",
        "interface": "context_aware_operator_router(problem: str) -> dict with response and route metadata"
    }
}

EXTENDED_OPERATOR_AN_PY = '''# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field


class GenerateOp(BaseModel):
    response: str = Field(default="", description="Your solution for this problem")


class CodeGenerateOp(BaseModel):
    code: str = Field(default="", description="Your complete code solution for this problem")


class ScEnsembleOp(BaseModel):
    solution_letter: str = Field(default="", description="The letter of most consistent solution.")


class ProblemClassifyOp(BaseModel):
    thought: str = Field(default="", description="Brief analysis of keywords and math concepts in the problem that indicate its category.")
    category: str = Field(default="algebra", description="One of: geometry, algebra, number_theory, combinatorics, probability, trigonometry")


class AnswerFormatOp(BaseModel):
    thought: str = Field(default="", description="Analysis of what the correct answer format should be based on the problem type.")
    formatted_answer: str = Field(default="", description="The answer reformatted with proper LaTeX notation, wrapped in \\\\boxed{{}}.")


class StrategySelectOp(BaseModel):
    thought: str = Field(default="", description="Comparison of the different strategy solutions and analysis of which is most reliable.")
    best_solution: str = Field(default="", description="The complete solution text from the most reliable strategy, with final answer in \\\\boxed{{}}.")


class SymbolicVerifyOp(BaseModel):
    is_consistent: bool = Field(default=True, description="Whether the proposed answer is consistent with the problem constraints.")
    verification_detail: str = Field(default="", description="Step-by-step verification of whether the answer satisfies the problem constraints.")
    corrected_answer: str = Field(default="", description="If the answer is inconsistent, provide the corrected answer in \\\\boxed{{}} notation.")
'''

EXTENDED_OP_PROMPT_PY = r'''SC_ENSEMBLE_PROMPT = """
Given the question described as follows: {problem}
Several solutions have been generated to address the given question. They are as follows:
{solutions}

Carefully evaluate these solutions and identify the answer that appears most frequently across them. This consistency in answers is crucial for determining the most reliable solution.

In the "thought" field, provide a detailed explanation of your thought process. In the "solution_letter" field, output only the single letter ID (A, B, C, etc.) corresponding to the most consistent solution. Do not include any additional text or explanation in the "solution_letter" field.
"""

PYTHON_CODE_VERIFIER_PROMPT = """
You are a professional Python programmer. Your task is to write complete, self-contained code based on a given mathematical problem and output the answer. The code should include all necessary imports and dependencies, and be ready to run without additional setup or environment configuration.

Problem description: {problem}
Other analysis: {analysis}
{feedback}

Your code should:
1. Implement the calculation steps described in the problem.
2. Define a function named `solve` that performs the calculation and returns the result. The `solve` function should not require any input parameters; instead, it should obtain all necessary inputs from within the function or from globally defined variables.
3. `solve` function return the final calculation result.

Please ensure your code is efficient, well-commented, and follows Python best practices. The output should be limited to basic data types such as strings, integers, and floats. It is prohibited to transmit images or other file formats. The code output is intended for a text-based language model.
"""

VALID_CATEGORIES = {"geometry", "algebra", "number_theory", "combinatorics", "probability", "trigonometry"}

PROBLEM_CLASSIFY_PROMPT = """
Classify the following math problem into exactly ONE of these categories based on its dominant mathematical topic:

- geometry: problems about shapes, angles, triangles, circles, areas, volumes, coordinate geometry, vectors, transformations, reflections, projections
- algebra: problems about equations, inequalities, functions, polynomials, systems of equations, sequences, series (non-trig), logarithms
- number_theory: problems about divisibility, primes, modular arithmetic, GCD/LCM, digit problems, Diophantine equations
- combinatorics: problems about counting, permutations, combinations, binomial coefficients, inclusion-exclusion
- probability: problems about probability, expected value, random variables
- trigonometry: problems specifically about trigonometric identities, trigonometric equations, sine/cosine sums or products

Problem: {problem}

Analyze the key mathematical concepts and keywords, then classify.
"""

ANSWER_FORMAT_PROMPT = """
You are a mathematical answer formatting assistant. Your task is to extract the final answer from a solution and format it correctly in LaTeX notation.

Problem: {problem}
Solution: {solution}

Formatting rules:
1. The answer MUST be wrapped in \\boxed{{}} notation.
2. For percentages: use format like \\boxed{{28\\%}} (with \\% not %).
3. For units (area, length, etc.): include the unit in \\text{{}}, e.g., \\boxed{{100\\text{{ square units}}}}.
4. For angles in degrees: use \\boxed{{120^\\circ}}.
5. For fractions: use LaTeX format \\boxed{{\\frac{{a}}{{b}}}} unless the fraction simplifies to an integer.
6. For square roots: use \\boxed{{\\sqrt{{n}}}} or \\boxed{{a\\sqrt{{b}}}}.
7. For mixed numbers: convert to improper fractions, e.g., 2⅓ → \\boxed{{\\frac{{7}}{{3}}}}.
8. For simple integers/decimals: just \\boxed{{42}} or \\boxed{{3.14}}.
9. If the answer is a list or set, use comma separation inside \\boxed{{}}.
10. Do NOT include any explanation or extra text inside \\boxed{{}}.

Analyze what format the answer should be in, then provide the formatted answer.
"""

STRATEGY_SELECT_PROMPT = """
You are evaluating multiple solutions to the same math problem, each generated using a different strategy.

Problem: {problem}

Strategy solutions:
{solutions}

Your task:
1. Compare the final answers from each strategy.
2. Check which solutions have correct reasoning and consistent final answers.
3. If multiple strategies agree on the same answer, that answer is more likely correct.
4. If strategies disagree, carefully verify which reasoning is correct.
5. Select the most reliable solution.

In the "thought" field, provide your comparison and analysis.
In the "best_solution" field, provide the COMPLETE solution text from the most reliable strategy (including all reasoning steps), with the final answer in \\boxed{{}}.
"""

SYMBOLIC_VERIFY_PROMPT = """
You are a mathematical verification assistant. Your task is to verify whether a proposed answer to a math problem is consistent with the problem's constraints and conditions.

Problem: {problem}
Proposed answer: {answer}

Verification steps:
1. Identify the key constraints and conditions in the problem.
2. Substitute the proposed answer back into the original problem conditions.
3. Check if ALL conditions are satisfied.
4. For algebraic problems: verify by substitution.
5. For geometric problems: verify the answer satisfies the given geometric properties.
6. For counting problems: verify by enumeration or logical reasoning.
7. For word problems: verify the answer makes sense in the real-world context.

If the answer is consistent with all constraints, set is_consistent to True.
If the answer is inconsistent, set is_consistent to False, explain the inconsistency, and provide the corrected answer in \\boxed{{}} notation.
"""
'''

EXTENDED_OPERATOR_PY = r'''import concurrent
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


class Custom(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Custom"):
        super().__init__(llm, name)

    async def __call__(self, input, instruction):
        prompt = instruction + input
        response = await self._fill_node(GenerateOp, prompt, mode="single_fill")
        return response

def run_code(code):
    try:
        global_namespace = {}
        disallowed_imports = [
            "os", "sys", "subprocess", "multiprocessing",
            "matplotlib", "seaborn", "plotly", "bokeh", "ggplot",
            "pylab", "tkinter", "PyQt5", "wx", "pyglet"
        ]
        for lib in disallowed_imports:
            if f"import {lib}" in code or f"from {lib}" in code:
                logger.info("Detected prohibited import: %s", lib)
                return "Error", f"Prohibited import: {lib} and graphing functionalities"
        exec(code, global_namespace)
        if 'solve' in global_namespace and callable(global_namespace['solve']):
            result = global_namespace['solve']
            return "Success", str(result)
        else:
            return "Error", "Function 'solve' not found"
    except Exception as e:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        tb_str = traceback.format_exception(exc_type, exc_value, exc_traceback)
        return "Error", f"Execution error: {str(e)}\n{''.join(tb_str)}"


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


class ContextAwareOperatorRouter(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "ContextAwareOperatorRouter"):
        super().__init__(llm, name)
        from scripts.contextual_operator_router import ContextAwareOperatorRouter as RuntimeRouter
        self.runtime_router = RuntimeRouter(
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
'''


def set_extended_template():
    """设置扩展6算子模板"""
    with open(f"{TEMPLATE_DIR}/operator.json", 'w', encoding='utf-8') as f:
        json.dump(EXTENDED_OPERATOR_JSON, f, indent=4, ensure_ascii=False)
    with open(f"{TEMPLATE_DIR}/operator_an.py", 'w', encoding='utf-8') as f:
        f.write(EXTENDED_OPERATOR_AN_PY)
    with open(f"{TEMPLATE_DIR}/op_prompt.py", 'w', encoding='utf-8') as f:
        f.write(EXTENDED_OP_PROMPT_PY)
    with open(f"{TEMPLATE_DIR}/operator.py", 'w', encoding='utf-8') as f:
        f.write(EXTENDED_OPERATOR_PY)

    # Restore evidence-backed artifacts after resetting the mutable template.
    # The archive lives outside optimizer snapshots and survives rollback.
    from scripts.operator_library import OperatorLibrary
    library = OperatorLibrary("workspace/MATH/operator_library/operators.json")
    library.install_routable_artifacts(TEMPLATE_DIR)

    for d in glob.glob(f"{TEMPLATE_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)
    for d in glob.glob(f"{WORKFLOWS_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)
    print("  已设置扩展6算子模板")


def clean_workspace():
    """清理工作区"""
    for d in glob.glob(f"{WORKFLOWS_DIR}/round_*"):
        shutil.rmtree(d)
    for f in ['results.json', 'processed_experience.json']:
        p = f"{WORKFLOWS_DIR}/{f}"
        if os.path.exists(p):
            os.remove(p)
    for d in glob.glob(f"{WORKFLOWS_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)


def ensure_seed():
    """确保种子文件存在"""
    round_dir = f"{WORKFLOWS_DIR}/round_1"
    os.makedirs(round_dir, exist_ok=True)
    with open(f"{round_dir}/graph.py", 'w') as f:
        f.write(SEED_GRAPH_PY)
    with open(f"{round_dir}/prompt.py", 'w') as f:
        f.write(SEED_PROMPT_PY)
    with open(f"{round_dir}/__init__.py", 'w') as f:
        f.write(SEED_INIT_PY)


def analyze_results(results):
    if not results:
        return {"error": "No results"}
    scores = [r["score"] for r in results if r.get("score") is not None]
    valid_scores = [s for s in scores if s > 0]
    if not valid_scores:
        return {"error": "No valid scores", "all_results": results}
    valid_rounds = [r for r in results if r.get("score") is not None and r["score"] > 0]
    zero_rounds = [r for r in results if r.get("score") is not None and r["score"] == 0]
    return {
        "total_rounds": len(results),
        "valid_rounds": len(valid_rounds),
        "zero_score_rounds": len(zero_rounds),
        "best_score": max(scores),
        "best_round": scores.index(max(scores)) + 1,
        "final_score": scores[-1],
        "all_scores": [r.get("score", 0) for r in results],
    }


def main():
    target_dir = f"{RESULTS_BASE}/{EXPERIMENT_NAME}"
    if os.path.exists(target_dir) and not ALLOW_OVERWRITE:
        raise FileExistsError(
            f"Refusing to overwrite existing experiment: {target_dir}. "
            "Set a new EXPERIMENT_NAME or ALLOW_OVERWRITE=true."
        )
    print("=" * 60)
    print("  MATH 实验 B v5: 宽松触发 + Prompt引导的算子自动进化")
    print("  [Custom, ScEnsemble, Programmer, AnswerFormatter,")
    print("   MultiStrategySolver, SymbolicVerifier]")
    print("  核心: 2轮无提升 → 进化介入, anti-pattern引导生成策略型算子")
    print("=" * 60)
    print(f"时间: {datetime.now().isoformat()}\n")

    print("[1] 设置扩展6算子模板...")
    set_extended_template()

    print("[2] 清理工作区...")
    clean_workspace()
    ensure_seed()

    with open(f"{TEMPLATE_DIR}/operator.json", 'r') as f:
        ops = json.load(f)
    print(f"[3] 初始算子: {list(ops.keys())}")

    print(f"\n[4] 开始{MAX_ROUNDS}轮优化 (model={MODEL_NAME})...")
    print(f"    AST验证: ✅")
    print(f"    冒烟测试: ✅")
    print(f"    最优保留: ❌ (退火有害已关闭)")
    print(f"    瓶颈检测: ✅ (2轮窗口，宽松触发)")
    print(f"    算子进化: ✅ (瓶颈触发，冷却3轮)")
    print(f"    Prompt引导: ✅ (anti-pattern + positive example)\n")

    multiprocessing.set_start_method("fork", force=True)
    from scripts.optimizer import Optimizer
    from scripts.async_llm import LLMsConfig

    mc = LLMsConfig.default()
    cfg = mc.get(MODEL_NAME)

    optimizer = Optimizer(
        dataset="MATH",
        question_type="math",
        opt_llm_config=cfg,
        exec_llm_config=cfg,
        check_convergence=False,
        operators=EXTENDED_OPERATORS.copy(),
        optimized_path="workspace",
        sample=SAMPLE,
        initial_round=1,
        max_rounds=MAX_ROUNDS,
        validation_rounds=VALIDATION_ROUNDS,
        # ===== 验证机制（安全网，不干预搜索方向）=====
        enable_code_validation=True,
        enable_bottleneck_detection=True,
        enable_smoke_test=True,
        max_validation_retries=3,
        smoke_test_timeout=30.0,
        # ===== 算子自动进化 =====
        enable_operator_evolution=True,
        enable_prompt_guidance=True,     # anti-pattern + positive example 引导
        bottleneck_window=2,              # 宽松: 2轮无提升 → 进化介入
        bottleneck_threshold=0.005,
    )

    start_time = time.time()
    try:
        optimizer.optimize("Graph")
        success = True
    except Exception as e:
        print(f"实验出错: {e}")
        import traceback
        traceback.print_exc()
        success = False

    elapsed = time.time() - start_time
    print(f"\n实验耗时: {elapsed:.1f}s")

    print(f"\n验证机制统计:")
    print(f"  AST 拒绝次数: {optimizer._total_validation_rejections}")
    print(f"  冒烟测试拒绝: {optimizer._smoke_test_rejections}")
    print(f"  算子进化触发: {optimizer._evolution_triggered_count}")

    if optimizer.operator_evolver:
        print(f"\n算子进化历史:")
        for record in optimizer.operator_evolver.evolution_history:
            print(f"  🧬 {record['name']}: {record['description']}")
            print(f"     原因: {record['reasoning'][:100]}")

    print(f"\n最终算子列表: {optimizer.operators}")

    results_path = f"{WORKFLOWS_DIR}/results.json"
    if os.path.exists(results_path):
        with open(results_path, 'r') as f:
            results = json.load(f)
    else:
        results = []

    analysis = analyze_results(results)
    analysis["time_seconds"] = elapsed
    analysis["success"] = success
    analysis["validation_stats"] = {
        "ast_rejections": optimizer._total_validation_rejections,
        "smoke_rejections": optimizer._smoke_test_rejections,
        "evolution_triggered": optimizer._evolution_triggered_count,
    }
    analysis["bottleneck_history"] = [
        {"round": h["round"], "score": h["score"]}
        for h in optimizer.bottleneck_detector.history
    ]
    if optimizer.operator_evolver:
        analysis["evolution_history"] = optimizer.operator_evolver.evolution_history
    analysis["final_operators"] = optimizer.operators

    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    shutil.copytree(WORKFLOWS_DIR, f"{target_dir}/workflows")
    if os.path.exists(results_path):
        shutil.copy2(results_path, f"{target_dir}/results.json")
    template_snapshot = f"{target_dir}/template_snapshot"
    if os.path.exists(template_snapshot):
        shutil.rmtree(template_snapshot)
    shutil.copytree(TEMPLATE_DIR, template_snapshot)
    library_source = "workspace/MATH/operator_library"
    if os.path.exists(library_source):
        shutil.copytree(
            library_source, f"{target_dir}/operator_library_snapshot"
        )
    with open(f"{target_dir}/analysis.json", 'w', encoding='utf-8') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    print(f"\n实验B v5结果:")
    print(f"  有效轮次: {analysis.get('valid_rounds', 'N/A')}/{analysis.get('total_rounds', 'N/A')}")
    print(f"  零分轮次: {analysis.get('zero_score_rounds', 'N/A')}")
    print(f"  最佳分数: {analysis.get('best_score', 'N/A'):.4f} (Round {analysis.get('best_round', 'N/A')})")
    print(f"  各轮分数: {analysis.get('all_scores', [])}")
    print(f"\n结果已保存到: {target_dir}")

    v3_path = f"{RESULTS_BASE}/expB_extended_v3/analysis.json"
    if os.path.exists(v3_path):
        with open(v3_path, 'r') as f:
            analysis_v3 = json.load(f)
        score_v3 = analysis_v3.get("best_score", 0)
        score_v5 = analysis.get("best_score", 0)
        delta = score_v5 - score_v3
        print(f"\n对比 v3 (无进化): {score_v3:.4f} → {score_v5:.4f} (Δ={delta:+.4f})")


if __name__ == "__main__":
    main()
