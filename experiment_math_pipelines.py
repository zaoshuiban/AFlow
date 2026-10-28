# -*- coding: utf-8 -*-
"""
MATH 对照实验 v2：手动构造工作流 + AFlow 评测

策略变更：由于 DeepSeek 优化器无法正确生成引用新算子的代码，
改为手动构造使用新算子的工作流管线，然后用 AFlow 的评测器评测。
"""

import json
import os
import shutil
import glob
import time
import asyncio
import multiprocessing
from datetime import datetime

multiprocessing.set_start_method("fork", force=True)

MODEL_NAME = "deepseek-chat"
WORKFLOWS_DIR = "workspace/MATH/workflows"
TEMPLATE_DIR = f"{WORKFLOWS_DIR}/template"
RESULTS_BASE = "workspace/MATH/experiment_ablation"

# 手动构造的管线
PIPELINES = {
    "P0_Custom_only": {
        "description": "Custom only 基线",
        "graph_py": '''from typing import Literal
import workspace.MATH.workflows.template.operator as operator
import workspace.MATH.workflows.round_1.prompt as prompt_custom
from scripts.async_llm import create_llm_instance
from scripts.evaluator import DatasetType

class Workflow:
    def __init__(self, name: str, llm_config, dataset: DatasetType) -> None:
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = operator.Custom(self.llm)

    async def __call__(self, problem: str):
        solution = await self.custom(input=problem, instruction="")
        return solution['response'], self.llm.get_usage_summary()["total_cost"]
''',
        "prompt_py": '# placeholder\n',
    },
    "P1_Custom_AnswerFormatter": {
        "description": "Custom生成 → AnswerFormatter格式修正",
        "graph_py": '''from typing import Literal
import workspace.MATH.workflows.template.operator as operator
import workspace.MATH.workflows.round_1.prompt as prompt_custom
from scripts.async_llm import create_llm_instance
from scripts.evaluator import DatasetType

class Workflow:
    def __init__(self, name: str, llm_config, dataset: DatasetType) -> None:
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = operator.Custom(self.llm)
        self.answer_formatter = operator.AnswerFormatter(self.llm)

    async def __call__(self, problem: str):
        solution = await self.custom(input=problem, instruction="Solve this math problem step by step. Put your final answer in \\\\boxed{}.")
        formatted = await self.answer_formatter(problem=problem, solution=solution['response'])
        return formatted['response'], self.llm.get_usage_summary()["total_cost"]
''',
        "prompt_py": 'MATH_PROMPT = """Solve this math problem step by step."""\n',
    },
    "P2_Custom_SymbolicVerifier": {
        "description": "Custom生成 → SymbolicVerifier验证修正",
        "graph_py": '''from typing import Literal
import workspace.MATH.workflows.template.operator as operator
import workspace.MATH.workflows.round_1.prompt as prompt_custom
from scripts.async_llm import create_llm_instance
from scripts.evaluator import DatasetType

class Workflow:
    def __init__(self, name: str, llm_config, dataset: DatasetType) -> None:
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = operator.Custom(self.llm)
        self.symbolic_verifier = operator.SymbolicVerifier(self.llm)

    async def __call__(self, problem: str):
        solution = await self.custom(input=problem, instruction="Solve this math problem step by step. Put your final answer in \\\\boxed{}.")
        verified = await self.symbolic_verifier(problem=problem, solution=solution['response'])
        return verified['response'], self.llm.get_usage_summary()["total_cost"]
''',
        "prompt_py": 'MATH_PROMPT = """Solve this math problem step by step."""\n',
    },
    "P3_MultiStrategySolver": {
        "description": "MultiStrategySolver 多策略求解",
        "graph_py": '''from typing import Literal
import workspace.MATH.workflows.template.operator as operator
import workspace.MATH.workflows.round_1.prompt as prompt_custom
from scripts.async_llm import create_llm_instance
from scripts.evaluator import DatasetType

class Workflow:
    def __init__(self, name: str, llm_config, dataset: DatasetType) -> None:
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.multi_strategy = operator.MultiStrategySolver(self.llm)

    async def __call__(self, problem: str):
        result = await self.multi_strategy(problem=problem)
        return result['response'], self.llm.get_usage_summary()["total_cost"]
''',
        "prompt_py": 'MATH_PROMPT = """Solve this math problem."""\n',
    },
    "P4_Custom_ReviewRevise_AnswerFormatter": {
        "description": "Custom生成 → ReviewRevise推理修正 → AnswerFormatter格式修正",
        "graph_py": '''from typing import Literal
import workspace.MATH.workflows.template.operator as operator
import workspace.MATH.workflows.round_1.prompt as prompt_custom
from scripts.async_llm import create_llm_instance
from scripts.evaluator import DatasetType

class Workflow:
    def __init__(self, name: str, llm_config, dataset: DatasetType) -> None:
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = operator.Custom(self.llm)
        self.review_revise = operator.ReviewRevise(self.llm)
        self.answer_formatter = operator.AnswerFormatter(self.llm)

    async def __call__(self, problem: str):
        solution = await self.custom(input=problem, instruction="Solve this math problem step by step. Put your final answer in \\\\boxed{}.")
        reviewed = await self.review_revise(problem=problem, solution=solution['response'])
        formatted = await self.answer_formatter(problem=problem, solution=reviewed['response'])
        return formatted['response'], self.llm.get_usage_summary()["total_cost"]
''',
        "prompt_py": 'MATH_PROMPT = """Solve this math problem step by step."""\n',
    },
    "P5_MultiStrategy_SymbolicVerifier": {
        "description": "MultiStrategySolver多策略 → SymbolicVerifier验证",
        "graph_py": '''from typing import Literal
import workspace.MATH.workflows.template.operator as operator
import workspace.MATH.workflows.round_1.prompt as prompt_custom
from scripts.async_llm import create_llm_instance
from scripts.evaluator import DatasetType

class Workflow:
    def __init__(self, name: str, llm_config, dataset: DatasetType) -> None:
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.multi_strategy = operator.MultiStrategySolver(self.llm)
        self.symbolic_verifier = operator.SymbolicVerifier(self.llm)

    async def __call__(self, problem: str):
        result = await self.multi_strategy(problem=problem)
        verified = await self.symbolic_verifier(problem=problem, solution=result['response'])
        return verified['response'], self.llm.get_usage_summary()["total_cost"]
''',
        "prompt_py": 'MATH_PROMPT = """Solve this math problem."""\n',
    },
}


def set_extended_template():
    """设置扩展6算子模板"""
    from run_math_expB import (
        EXTENDED_OPERATOR_AN_PY, EXTENDED_OP_PROMPT_PY, EXTENDED_OPERATOR_PY
    )
    EXTENDED_OPERATOR_JSON = {
        "Custom": {"description": "Generates anything based on customized input and instruction.", "interface": "custom(input: str, instruction: str) -> dict with key 'response' of type str"},
        "ScEnsemble": {"description": "Uses self-consistency to select the solution that appears most frequently.", "interface": "sc_ensemble(solutions: List[str], problem: str) -> dict with key 'response' of type str"},
        "Programmer": {"description": "Automatically writes, executes Python code, and returns the solution.", "interface": "programmer(problem: str, analysis: str = 'None') -> dict with keys 'code' and 'output' of type str"},
        "AnswerFormatter": {"description": "Extracts the final answer and reformats it with proper LaTeX notation.", "interface": "answer_formatter(problem: str, solution: str) -> dict with key 'response' of type str"},
        "MultiStrategySolver": {"description": "Generates solutions using three distinct strategies then selects the most reliable one.", "interface": "multi_strategy_solver(problem: str) -> dict with key 'response' of type str"},
        "SymbolicVerifier": {"description": "Verifies a proposed answer against the problem's constraints using symbolic reasoning.", "interface": "symbolic_verifier(problem: str, solution: str) -> dict with key 'response' of type str"},
    }
    with open(f"{TEMPLATE_DIR}/operator.json", 'w', encoding='utf-8') as f:
        json.dump(EXTENDED_OPERATOR_JSON, f, indent=4, ensure_ascii=False)
    with open(f"{TEMPLATE_DIR}/operator_an.py", 'w', encoding='utf-8') as f:
        f.write(EXTENDED_OPERATOR_AN_PY)
    with open(f"{TEMPLATE_DIR}/op_prompt.py", 'w', encoding='utf-8') as f:
        f.write(EXTENDED_OP_PROMPT_PY)
    with open(f"{TEMPLATE_DIR}/operator.py", 'w', encoding='utf-8') as f:
        f.write(EXTENDED_OPERATOR_PY)
    for d in glob.glob(f"{WORKFLOWS_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)


async def evaluate_pipeline(pipeline_name: str, pipeline_config: dict) -> dict:
    """评测单个管线"""
    from scripts.async_llm import LLMsConfig
    from scripts.evaluator import Evaluator
    import importlib

    # 清理工作区
    for d in glob.glob(f"{WORKFLOWS_DIR}/round_*"):
        shutil.rmtree(d)
    for f in ['results.json', 'processed_experience.json']:
        p = f"{WORKFLOWS_DIR}/{f}"
        if os.path.exists(p):
            os.remove(p)
    for d in glob.glob(f"{WORKFLOWS_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)

    # 设置扩展模板
    set_extended_template()

    # 创建 round_1 目录并写入管线代码
    round_dir = f"{WORKFLOWS_DIR}/round_1"
    os.makedirs(round_dir, exist_ok=True)

    with open(f"{round_dir}/graph.py", 'w', encoding='utf-8') as f:
        f.write(pipeline_config["graph_py"])
    with open(f"{round_dir}/prompt.py", 'w', encoding='utf-8') as f:
        f.write(pipeline_config["prompt_py"])
    with open(f"{round_dir}/__init__.py", 'w', encoding='utf-8') as f:
        f.write('')

    # 配置
    mc = LLMsConfig.default()
    cfg = mc.get(MODEL_NAME)

    # 动态导入 graph 模块（不实例化，Evaluator 会自己实例化）
    spec = importlib.util.spec_from_file_location("graph_module", f"{round_dir}/graph.py")
    graph_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graph_module)

    # 使用 Evaluator 评测（传入 Workflow 类，不是实例）
    evaluator = Evaluator(eval_path=WORKFLOWS_DIR)

    print(f"  评测 {pipeline_name}: {pipeline_config['description']}...")
    start_time = time.time()

    try:
        score, avg_cost, total_cost = await evaluator.graph_evaluate(
            "MATH",
            graph_module.Workflow,  # 传入类，不是实例
            {"dataset": "MATH", "llm_config": cfg},
            WORKFLOWS_DIR,
            is_test=False,
        )
        elapsed = time.time() - start_time
        total = 119  # MATH validate set size
        correct = int(score * total)
        return {
            "pipeline": pipeline_name,
            "description": pipeline_config["description"],
            "score": score,
            "total": total,
            "correct": correct,
            "cost": total_cost,
            "time_seconds": elapsed,
        }
    except Exception as e:
        import traceback
        elapsed = time.time() - start_time
        traceback.print_exc()
        return {
            "pipeline": pipeline_name,
            "description": pipeline_config["description"],
            "error": str(e),
            "time_seconds": elapsed,
        }


async def main():
    print("=" * 60)
    print("  MATH 实验 B v2: 手动构造管线 + AFlow 评测")
    print("=" * 60)
    print(f"时间: {datetime.now().isoformat()}\n")

    os.makedirs(f"{RESULTS_BASE}/pipeline_logs", exist_ok=True)

    results = {}
    for name, config in PIPELINES.items():
        try:
            result = await evaluate_pipeline(name, config)
            results[name] = result
            if "score" in result:
                print(f"  → {name}: {result['score']:.4f} ({result['correct']}/{result['total']})\n")
            else:
                print(f"  → {name}: ERROR - {result.get('error', 'unknown')}\n")
        except Exception as e:
            import traceback
            print(f"  → {name}: ERROR - {e}")
            traceback.print_exc()
            results[name] = {"pipeline": name, "error": str(e)}
            print()

    # 保存结果
    with open(f"{RESULTS_BASE}/pipeline_results.json", 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # 对比实验 A
    expa_path = f"{RESULTS_BASE}/expA_original/analysis.json"
    score_a = 0
    if os.path.exists(expa_path):
        with open(expa_path, 'r') as f:
            analysis_a = json.load(f)
        score_a = analysis_a.get("best_score", 0)

    print("\n" + "=" * 60)
    print("  对比报告")
    print("=" * 60)
    print(f"\n实验A（优化器搜索，原始3算子）最佳: {score_a:.4f}")
    print("\n实验B（手动构造管线，扩展6算子）:")
    for name, result in sorted(results.items(), key=lambda x: x[1].get("score", 0), reverse=True):
        score = result.get("score", 0)
        delta = score - score_a
        marker = " ★" if delta > 0 else ""
        print(f"  {name}: {score:.4f} (Δ={delta:+.4f}){marker} - {result.get('description', '')}")

    valid_results = {k: v for k, v in results.items() if "score" in v}
    if valid_results:
        best_b = max(valid_results.values(), key=lambda x: x.get("score", 0))
        best_delta = best_b.get("score", 0) - score_a
        print(f"\n最佳扩展管线: {best_b['pipeline']} = {best_b.get('score', 0):.4f} (Δ={best_delta:+.4f})")
        if best_delta > 0:
            print("结论: MATH特定算子扩展了搜索空间上限!")
        else:
            print("结论: 扩展算子未超过优化器搜索的最佳结果")


if __name__ == "__main__":
    asyncio.run(main())
