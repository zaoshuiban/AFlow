# -*- coding: utf-8 -*-
"""
消融实验：隔离进化算子的净贡献

核心思路：固定 graph 结构为 5 路投票，只替换其中 1 路的算子类型，衡量分数差异。

实验组：
  A. 5×Custom → ScEnsemble              （纯投票基线，无进化算子、无 Programmer）
  B. 3×Custom + Programmer + Custom → ScEnsemble  （随机多路基线，Programmer 占 1 路）
  C. 3×Custom + TrigSimplifier + Programmer → ScEnsemble  （进化算子组，与 v4 R5 结构一致）

每组跑 3 次取均值±标准差，配对 t-test 检验差异显著性。
"""

import asyncio
import json
import os
import sys
import time
import statistics
from datetime import datetime
from typing import List

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.evaluator import Evaluator, DatasetType
from benchmarks.math import MATHBenchmark
from scripts.logs import logger


# ============ 固定的 Graph 结构定义 ============

GRAPH_CONFIGS = {
    "A_5xcustom": {
        "name": "A: 5×Custom → ScEnsemble (纯投票基线)",
        "operators": ["Custom"] * 5 + ["ScEnsemble"],
        "description": "5 个 Custom 解题 + ScEnsemble 投票，无进化算子、无 Programmer",
    },
    "B_3xcustom_programmer_custom": {
        "name": "B: 3×Custom + Programmer + Custom → ScEnsemble (随机多路基线)",
        "operators": ["Custom", "Custom", "Custom", "Programmer", "Custom", "ScEnsemble"],
        "description": "3 Custom + 1 Programmer + 1 Custom + ScEnsemble，Programmer 占 1 路",
    },
    "C_3xcustom_trig_programmer": {
        "name": "C: 3×Custom + TrigSimplifier + Programmer → ScEnsemble (进化算子组)",
        "operators": ["Custom", "Custom", "Custom", "TrigonometricSumSimplifier", "Programmer", "ScEnsemble"],
        "description": "与 v4 R5 结构完全一致，进化算子占 1 路",
    },
}

SOLVE_PROMPT = """
Solve the problem step by step. Show all reasoning clearly. Output your final answer within \\boxed{}.
"""

FORMAT_PROMPT = """
Extract the final answer from the solution below and format it within \\boxed{}.
Solution:
{solution}
"""


def generate_graph_code(config_key: str) -> str:
    """根据配置生成固定的 graph.py 代码"""
    cfg = GRAPH_CONFIGS[config_key]

    lines = []
    lines.append("from typing import Literal")
    lines.append("import workspace.MATH.workflows.template.operator as operator")
    lines.append("import workspace.MATH.workflows.template.prompt as prompt_custom  # 共享 prompt 文件")
    lines.append("from scripts.async_llm import create_llm_instance")
    lines.append("")
    lines.append("from scripts.evaluator import DatasetType")
    lines.append("")
    lines.append("SOLVE_PROMPT = \"\"\"")
    lines.append("Solve the problem step by step. Show all reasoning clearly. Output your final answer within \\\\boxed{}.")
    lines.append("\"\"\"")
    lines.append("")
    lines.append("class Workflow:")
    lines.append("    def __init__(self, name: str, llm_config, dataset: DatasetType) -> None:")
    lines.append("        self.name = name")
    lines.append("        self.dataset = dataset")
    lines.append("        self.llm = create_llm_instance(llm_config)")
    lines.append("        self.custom = operator.Custom(self.llm)")
    lines.append("        self.sc_ensemble = operator.ScEnsemble(self.llm)")

    # 按需加载额外算子
    if "Programmer" in cfg["operators"]:
        lines.append("        self.programmer = operator.Programmer(self.llm)")
    if "TrigonometricSumSimplifier" in cfg["operators"]:
        lines.append("        self.trig_simplifier = operator.TrigonometricSumSimplifier(self.llm)")

    lines.append("")
    lines.append("    async def __call__(self, problem: str):")
    lines.append("        solutions = []")

    # 生成解题逻辑
    custom_count = 0
    for i, op in enumerate(cfg["operators"]):
        if op == "Custom":
            custom_count += 1
            lines.append(f"        sol_{i} = await self.custom(input=problem, instruction=SOLVE_PROMPT)")
            lines.append(f"        solutions.append(sol_{i}['response'])")
        elif op == "Programmer":
            lines.append(f"        prog_sol = await self.programmer(problem=problem)")
            lines.append(f"        solutions.append(prog_sol['output'])")
        elif op == "TrigonometricSumSimplifier":
            lines.append(f"        trig_sol = await self.trig_simplifier(problem=problem)")
            lines.append(f"        solutions.append(trig_sol['response'])")
        elif op == "ScEnsemble":
            pass  # 最后处理

    lines.append("        result = await self.sc_ensemble(solutions=solutions, problem=problem)")
    lines.append("        return result['response'], self.llm.get_usage_summary()['total_cost']")

    return "\n".join(lines)


def generate_prompt_file() -> str:
    """生成共享的 prompt.py"""
    return 'SOLVE_PROMPT = """\nSolve the problem step by step. Show all reasoning clearly. Output your final answer within \\\\boxed{}.\n"""\n'


class DeterministicWorkflow:
    """动态构建的固定工作流，用于消融实验评分"""

    def __init__(self, config_key: str, llm_config, dataset: str):
        from scripts.async_llm import create_llm_instance as _create
        self.llm = _create(llm_config)
        self.dataset = dataset
        self.config_key = config_key
        self.cfg = GRAPH_CONFIGS[config_key]

        # 实例化算子
        from workspace.MATH.workflows.template.operator import (
            Custom, ScEnsemble, Programmer, TrigonometricSumSimplifier
        )
        self.custom = Custom(self.llm)
        self.sc_ensemble = ScEnsemble(self.llm)
        self.programmer = Programmer(self.llm) if "Programmer" in self.cfg["operators"] else None
        self.trig_simplifier = (
            TrigonometricSumSimplifier(self.llm)
            if "TrigonometricSumSimplifier" in self.cfg["operators"]
            else None
        )

    async def __call__(self, problem: str):
        solutions = []
        for op in self.cfg["operators"]:
            if op == "Custom":
                sol = await self.custom(input=problem, instruction=SOLVE_PROMPT)
                solutions.append(sol.get("response", ""))
            elif op == "Programmer":
                prog_sol = await self.programmer(problem=problem)
                solutions.append(prog_sol.get("output", ""))
            elif op == "TrigonometricSumSimplifier":
                trig_sol = await self.trig_simplifier(problem=problem)
                solutions.append(trig_sol.get("response", ""))
            elif op == "ScEnsemble":
                continue

        result = await self.sc_ensemble(solutions=solutions, problem=problem)
        return result.get("response", ""), self.llm.get_usage_summary()["total_cost"]


async def run_single_evaluation(config_key: str, run_idx: int, llm_config) -> dict:
    """对一组固定 graph 执行一次完整的 MATH 评测"""
    cfg = GRAPH_CONFIGS[config_key]
    logger.info(f"{'='*60}")
    logger.info(f"开始评测: {cfg['name']} (第 {run_idx+1} 次运行)")
    logger.info(f"{'='*60}")

    # 直接用 DeterministicWorkflow 避免写文件
    data_path = "data/datasets/math_validate.jsonl"
    log_dir = f"workspace/MATH/ablation/{config_key}/run_{run_idx}"
    os.makedirs(log_dir, exist_ok=True)

    benchmark = MATHBenchmark(name="MATH", file_path=data_path, log_path=log_dir)
    data = await benchmark.load_data()

    results = []
    semaphore = asyncio.Semaphore(20)

    async def eval_problem(problem):
        async with semaphore:
            workflow = DeterministicWorkflow(config_key, llm_config, "MATH")
            try:
                output, cost = await asyncio.wait_for(
                    workflow(problem["problem"]),
                    timeout=180.0
                )
            except asyncio.TimeoutError:
                output, cost = "TIMEOUT", 0.0
            except Exception as e:
                output, cost = f"ERROR: {e}", 0.0

            score, extracted = benchmark.calculate_score(problem["solution"], output)
            if score == 0:
                benchmark.log_mismatch(
                    problem["problem"], problem["solution"], output, extracted,
                    extract_answer_code=benchmark.get_function_code(benchmark.extract_model_answer)
                )
            return problem["problem"], output, problem["solution"], score, cost

    tasks = [eval_problem(p) for p in data]
    results = await asyncio.gather(*tasks)

    # 计算分数
    import pandas as pd
    df = pd.DataFrame(results, columns=["question", "prediction", "expected_output", "score", "cost"])
    avg_score = df["score"].mean()
    total_cost = df["cost"].max()

    # 保存详细结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(log_dir, f"{avg_score:.5f}_{timestamp}.csv")
    df.to_csv(csv_path, index=False)

    logger.info(f"[{cfg['name']} run={run_idx+1}] score={avg_score:.5f}, cost={total_cost:.4f}")

    return {
        "config": config_key,
        "run": run_idx,
        "score": avg_score,
        "cost": total_cost,
        "csv": csv_path,
    }


async def run_ablation(num_runs: int = 3):
    """执行完整的消融实验"""
    models_config = LLMsConfig.default()
    exec_model = "deepseek-chat"
    llm_config = models_config.get(exec_model)
    if llm_config is None:
        raise ValueError(f"Model '{exec_model}' not found in config")

    all_results = []

    # 依次运行每个配置 × 每次重复（串行，避免 API 过载）
    for config_key in GRAPH_CONFIGS:
        for run_idx in range(num_runs):
            result = await run_single_evaluation(config_key, run_idx, llm_config)
            all_results.append(result)
            logger.info(f"完成 {config_key} run {run_idx+1}/{num_runs}")

    # 汇总统计
    logger.info("\n" + "=" * 80)
    logger.info("消融实验汇总")
    logger.info("=" * 80)

    summary = {}
    for config_key in GRAPH_CONFIGS:
        scores = [r["score"] for r in all_results if r["config"] == config_key]
        if scores:
            mean_score = statistics.mean(scores)
            std_score = statistics.stdev(scores) if len(scores) > 1 else 0.0
            summary[config_key] = {"mean": mean_score, "std": std_score, "scores": scores}
            logger.info(
                f"{GRAPH_CONFIGS[config_key]['name']}: "
                f"{mean_score:.5f} ± {std_score:.5f}  (raw: {scores})"
            )

    # 配对 t-test（A vs C）
    if "A_5xcustom" in summary and "C_3xcustom_trig_programmer" in summary and num_runs >= 3:
        from scipy import stats as scipy_stats
        scores_a = summary["A_5xcustom"]["scores"]
        scores_c = summary["C_3xcustom_trig_programmer"]["scores"]
        if len(scores_a) == len(scores_c) and len(scores_a) >= 3:
            t_stat, p_value = scipy_stats.ttest_rel(scores_c, scores_a)
            delta = statistics.mean(scores_c) - statistics.mean(scores_a)
            logger.info(f"\n配对 t-test (C vs A): Δ={delta:.5f}, t={t_stat:.3f}, p={p_value:.4f}")
            if p_value < 0.05:
                logger.info("→ 差异显著 (p < 0.05)，进化算子有统计显著的净贡献")
            else:
                logger.info("→ 差异不显著 (p >= 0.05)，无法确认进化算子的净贡献")

    # B vs C：排除 Programmer 混杂
    if "B_3xcustom_programmer_custom" in summary and "C_3xcustom_trig_programmer" in summary and num_runs >= 3:
        from scipy import stats as scipy_stats
        scores_b = summary["B_3xcustom_programmer_custom"]["scores"]
        scores_c = summary["C_3xcustom_trig_programmer"]["scores"]
        if len(scores_b) == len(scores_c) and len(scores_b) >= 3:
            t_stat, p_value = scipy_stats.ttest_rel(scores_c, scores_b)
            delta = statistics.mean(scores_c) - statistics.mean(scores_b)
            logger.info(f"\n配对 t-test (C vs B): Δ={delta:.5f}, t={t_stat:.3f}, p={p_value:.4f}")
            if p_value < 0.05:
                logger.info("→ 差异显著 (p < 0.05)，进化算子相比 Programmer 有显著优势")
            else:
                logger.info("→ 差异不显著 (p >= 0.05)，进化算子与 Programmer 效果无显著差异")

    # 保存汇总结果
    summary_path = "workspace/MATH/ablation/summary.json"
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "num_runs": num_runs,
            "configs": {k: {"name": GRAPH_CONFIGS[k]["name"], "description": GRAPH_CONFIGS[k]["description"]} for k in GRAPH_CONFIGS},
            "results": all_results,
            "summary": {k: {kk: vv for kk, vv in v.items() if kk != "scores"} for k, v in summary.items()},
        }, f, indent=2, ensure_ascii=False)
    logger.info(f"\n汇总结果已保存至: {summary_path}")

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MATH 消融实验：隔离进化算子净贡献")
    parser.add_argument("--num_runs", type=int, default=3, help="每组重复次数（建议≥3）")
    args = parser.parse_args()

    asyncio.run(run_ablation(num_runs=args.num_runs))
