# -*- coding: utf-8 -*-
"""
HumanEval 实验: 代码生成 workflow 优化
算子: [Custom, CustomCodeGenerate, ScEnsemble, Test]
验证: AST验证 + 冒烟测试 + 瓶颈检测
"""

import json
import os
import shutil
import glob
import time
import multiprocessing
from datetime import datetime

MODEL_NAME = "deepseek-chat"
MAX_ROUNDS = 10
VALIDATION_ROUNDS = 1
SAMPLE = 4

WORKFLOWS_DIR = "workspace/HumanEval/workflows"
TEMPLATE_DIR = f"{WORKFLOWS_DIR}/template"
RESULTS_BASE = "workspace/HumanEval/experiment"

OPERATORS = ["Custom", "CustomCodeGenerate", "ScEnsemble", "Test"]

SEED_GRAPH_PY = '''from typing import Literal
import workspace.HumanEval.workflows.template.operator as operator
import workspace.HumanEval.workflows.round_1.prompt as prompt_custom
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
        self.custom_code_generate = operator.CustomCodeGenerate(self.llm)

    async def __call__(self, problem: str, entry_point: str):
        solution = await self.custom_code_generate(problem=problem, entry_point=entry_point, instruction="")
        return solution['response'], self.llm.get_usage_summary()["total_cost"]
'''

SEED_PROMPT_PY = '''# XXX_PROMPT = """
#
# Solve it.
#
# """
'''
SEED_INIT_PY = ''

OPERATOR_JSON = {
    "Custom": {
        "description": "Generates anything based on customized input and instruction.",
        "interface": "custom(input: str, instruction: str) -> dict with key 'response' of type str"
    },
    "CustomCodeGenerate": {
        "description": "Generates code based on customized input and instruction. When you want to get standard code, you should use custom_code_generate operator.",
        "interface": "custom_code_generate(problem: str, entry_point: str, instruction: str) -> dict with key 'response' of type str"
    },
    "ScEnsemble": {
        "description": "Uses self-consistency to select the solution that appears most frequently in the solution list, improve the selection to enhance the choice of the best solution.",
        "interface": "sc_ensemble(solutions: List[str], problem: str) -> dict with key 'response' of type str"
    },
    "Test": {
        "description": "Tests the solution using public test cases. If the solution fails, it reflects on the errors and attempts to modify the solution. Returns True and the solution if all tests pass after modifications. Returns False and the current solution if it still fails after modifications.",
        "interface": "test(problem: str, solution: str, entry_point: str) -> dict with key 'result' of type bool and key 'solution' of type str"
    }
}

OPERATOR_AN_PY = '''# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field


class GenerateOp(BaseModel):
    response: str = Field(default="", description="Your solution for this problem")


class ScEnsembleOp(BaseModel):
    thought: str = Field(default="", description="The thought of the most consistent solution.")
    solution_letter: str = Field(default="", description="The letter of most consistent solution.")


class ReflectionTestOp(BaseModel):
    reflection_and_solution: str = Field(
        default="", description="Corrective solution for code execution errors or test case failures"
    )
'''

OP_PROMPT_PY = r'''SC_ENSEMBLE_PROMPT = """
Given the question described as follows: {problem}
Several solutions have been generated to address the given question. They are as follows:
{solutions}

Carefully evaluate these solutions and identify the answer that appears most frequently across them. This consistency in answers is crucial for determining the most reliable solution.

In the "thought" field, provide a detailed explanation of your thought process. In the "solution_letter" field, output only the single letter ID (A, B, C, etc.) corresponding to the most consistent solution. Do not include any additional text or explanation in the "solution_letter" field.
"""


REFLECTION_ON_PUBLIC_TEST_PROMPT = """
Given a code problem and a python code solution which failed to pass test or execute, you need to analyze the reason for the failure and propose a better code solution.: 
### problem
{problem}

### Code Solution
{solution}

### Execution Result
{exec_pass}

#### Failed Test Case
{test_fail}

Please provide a reflection on the failed test cases and code solution, followed by a better code solution without any additional text or test cases.
"""
'''

OPERATOR_PY = r'''# -*- coding: utf-8 -*-
import ast
import random
import sys
import traceback
from collections import Counter
from typing import Dict, List, Tuple, Optional

from scripts.formatter import BaseFormatter, FormatError, XmlFormatter, CodeFormatter, TextFormatter
from workspace.HumanEval.workflows.template.operator_an import *
from workspace.HumanEval.workflows.template.op_prompt import *
from scripts.async_llm import AsyncLLM
from scripts.logs import logger
import asyncio

from scripts.utils.code import extract_test_cases_from_jsonl, test_case_2_test_function


from scripts.operators import Operator


class Custom(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Custom"):
        super().__init__(llm, name)

    async def __call__(self, input, instruction):
        prompt = instruction + input
        response = await self._fill_node(GenerateOp, prompt, mode="single_fill")
        return response
    
class CustomCodeGenerate(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "CustomCodeGenerate"):
        super().__init__(llm, name)

    async def __call__(self, problem, entry_point, instruction):
        prompt = instruction + problem
        response = await self._fill_node(GenerateOp, prompt, mode="code_fill", function_name=entry_point)
        return response


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

        answer = response.get("solution_letter", "")
        answer = answer.strip().upper()

        return {"response": solutions[answer_mapping[answer]]}

class Test(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Test"):
        super().__init__(llm, name)

    def exec_code(self, solution, entry_point):

        test_cases = extract_test_cases_from_jsonl(entry_point, dataset="HumanEval")
                
        fail_cases = []
        for test_case in test_cases:
            test_code = test_case_2_test_function(solution, test_case, entry_point)
            try:
                exec(test_code, globals())
            except AssertionError as e:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                tb_str = traceback.format_exception(exc_type, exc_value, exc_traceback)
                error_infomation = {
                    "test_fail_case": {
                        "test_case": test_case,
                        "error_type": "AssertionError",
                        "error_message": str(e),
                        "traceback": tb_str,
                    }
                }
                fail_cases.append(error_infomation)
            except Exception as e:
                return {"exec_fail_case": str(e)}
        if fail_cases != []:
            return fail_cases
        else:
            return "no error"

    async def __call__(
        self, problem, solution, entry_point, test_loop: int = 3
    ):
        for _ in range(test_loop):
            result = self.exec_code(solution, entry_point)
            if result == "no error":
                return {"result": True, "solution": solution}
            elif "exec_fail_case" in result:
                result = result["exec_fail_case"]
                prompt = REFLECTION_ON_PUBLIC_TEST_PROMPT.format(
                    problem=problem,
                    solution=solution,
                    exec_pass=f"executed unsuccessfully, error: \n {result}",
                    test_fail="executed unsucessfully",
                )
                response = await self._fill_node(ReflectionTestOp, prompt, mode="code_fill")
                solution = response["response"]
            else:
                prompt = REFLECTION_ON_PUBLIC_TEST_PROMPT.format(
                    problem=problem,
                    solution=solution,
                    exec_pass="executed successfully",
                    test_fail=result,
                )
                response = await self._fill_node(ReflectionTestOp, prompt, mode="code_fill")
                solution = response["response"]
        
        result = self.exec_code(solution, entry_point)
        if result == "no error":
            return {"result": True, "solution": solution}
        else:
            return {"result": False, "solution": solution}
'''


def set_template():
    """设置 HumanEval 算子模板"""
    with open(f"{TEMPLATE_DIR}/operator.json", 'w', encoding='utf-8') as f:
        json.dump(OPERATOR_JSON, f, indent=4, ensure_ascii=False)
    with open(f"{TEMPLATE_DIR}/operator_an.py", 'w', encoding='utf-8') as f:
        f.write(OPERATOR_AN_PY)
    with open(f"{TEMPLATE_DIR}/op_prompt.py", 'w', encoding='utf-8') as f:
        f.write(OP_PROMPT_PY)
    with open(f"{TEMPLATE_DIR}/operator.py", 'w', encoding='utf-8') as f:
        f.write(OPERATOR_PY)

    # 清理 __pycache__
    for d in glob.glob(f"{TEMPLATE_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)
    for d in glob.glob(f"{WORKFLOWS_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)
    print("  已设置 HumanEval 4算子模板")


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
    print("=" * 60)
    print("  HumanEval 实验: 代码生成 workflow 优化")
    print("  算子: [Custom, CustomCodeGenerate, ScEnsemble, Test]")
    print("  验证: AST + 冒烟测试 + 瓶颈检测")
    print("=" * 60)
    print(f"时间: {datetime.now().isoformat()}\n")

    # 设置模板
    print("[1] 设置 HumanEval 算子模板...")
    set_template()

    # 清理工作区
    print("[2] 清理工作区...")
    clean_workspace()
    ensure_seed()

    # 验证模板
    with open(f"{TEMPLATE_DIR}/operator.json", 'r') as f:
        ops = json.load(f)
    print(f"[3] 模板算子: {list(ops.keys())}")

    # 运行实验
    print(f"[4] 开始10轮优化 (model={MODEL_NAME})...")
    print(f"    AST验证: ✅ 启用 (最多3次重试)")
    print(f"    冒烟测试: ✅ 启用 (30秒超时)")
    print(f"    瓶颈检测: ✅ 启用 (3轮窗口)\n")

    multiprocessing.set_start_method("fork", force=True)
    from scripts.optimizer import Optimizer
    from scripts.async_llm import LLMsConfig

    mc = LLMsConfig.default()
    cfg = mc.get(MODEL_NAME)

    optimizer = Optimizer(
        dataset="HumanEval",
        question_type="code",
        opt_llm_config=cfg,
        exec_llm_config=cfg,
        check_convergence=False,
        operators=OPERATORS,
        optimized_path="workspace",
        sample=SAMPLE,
        initial_round=1,
        max_rounds=MAX_ROUNDS,
        validation_rounds=VALIDATION_ROUNDS,
        # ===== 验证机制 =====
        enable_code_validation=True,
        enable_bottleneck_detection=True,
        enable_smoke_test=True,
        max_validation_retries=3,
        smoke_test_timeout=30.0,
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

    # 验证统计
    print(f"\n验证机制统计:")
    print(f"  AST 拒绝次数: {optimizer._total_validation_rejections}")
    print(f"  冒烟测试拒绝: {optimizer._smoke_test_rejections}")

    # 读取结果
    results_path = f"{WORKFLOWS_DIR}/results.json"
    if os.path.exists(results_path):
        with open(results_path, 'r') as f:
            results = json.load(f)
    else:
        results = []

    analysis = analyze_results(results)
    analysis["time_seconds"] = elapsed
    analysis["success"] = success
    analysis["dataset"] = "HumanEval"
    analysis["model"] = MODEL_NAME
    analysis["operators"] = OPERATORS
    analysis["validation_stats"] = {
        "ast_rejections": optimizer._total_validation_rejections,
        "smoke_rejections": optimizer._smoke_test_rejections,
    }
    if hasattr(optimizer, 'bottleneck_detector') and optimizer.bottleneck_detector:
        analysis["bottleneck_history"] = [
            {"round": h["round"], "score": h["score"]}
            for h in optimizer.bottleneck_detector.history
        ]

    # 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = f"{RESULTS_BASE}/humaneval_{timestamp}"
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    shutil.copytree(WORKFLOWS_DIR, f"{target_dir}/workflows")
    if os.path.exists(results_path):
        shutil.copy2(results_path, f"{target_dir}/results.json")
    template_snapshot = f"{target_dir}/template_snapshot"
    if os.path.exists(template_snapshot):
        shutil.rmtree(template_snapshot)
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    shutil.copytree(TEMPLATE_DIR, template_snapshot)
    with open(f"{target_dir}/analysis.json", 'w', encoding='utf-8') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    print(f"\nHumanEval 实验结果:")
    print(f"  有效轮次: {analysis.get('valid_rounds', 'N/A')}/{analysis.get('total_rounds', 'N/A')}")
    print(f"  零分轮次: {analysis.get('zero_score_rounds', 'N/A')}")
    print(f"  最佳分数: {analysis.get('best_score', 'N/A'):.4f} (Round {analysis.get('best_round', 'N/A')})")
    print(f"  各轮分数: {analysis.get('all_scores', [])}")
    print(f"\n结果已保存到: {target_dir}")


if __name__ == "__main__":
    main()
