# -*- coding: utf-8 -*-
"""
最小可行实验 - Step 5: 对比基线 vs 扩展算子库的性能差异

实验设计：
- 基线: Custom-only 工作流 (原始固定算子)
- 实验 A: Custom + AnswerExtractor 工作流 (验证答案提取算子)
- 实验 B: Custom + ReviewRevise 工作流 (验证自审查算子)
- 实验 C: MultiPathReasoner 工作流 (验证多路径推理算子)
- 实验 D: Custom + CodeVerifyRescue 工作流 (验证代码兜底算子)
- 实验 E: Custom + AnswerExtractor + ReviewRevise (组合)

关键指标: 在同样的 GSM8K 验证集上的准确率
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime
from typing import Optional, Tuple, List

from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.evaluator import DatasetType


# ===== 复用 GSM8K 的评测逻辑 =====
from benchmarks.gsm8k import GSM8KBenchmark


def extract_number_enhanced(text: str) -> Optional[float]:
    """增强的数字提取 - 优先从 \\boxed{} 提取"""
    # 1. Try \boxed{...}
    boxed = re.findall(r'\\boxed\{([^}]+)\}', str(text))
    if boxed:
        val = boxed[-1].strip().replace(",", "")
        try:
            return float(val)
        except ValueError:
            pass
    # 2. Try "The answer is X" patterns
    patterns = [
        r'(?:the answer is|final answer[:\s]*)\s*([+-]?\d+(?:,\d{3})*(?:\.\d+)?)',
        r'(?:answer[:\s]*)\s*([+-]?\d+(?:,\d{3})*(?:\.\d+)?)',
    ]
    for p in patterns:
        m = re.findall(p, str(text), re.IGNORECASE)
        if m:
            try:
                return float(m[-1].replace(",", ""))
            except ValueError:
                pass
    # 3. Fallback: last number
    all_matches = re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|\d+\.\d+", str(text))
    if all_matches:
        try:
            return float(all_matches[-1].replace(",", ""))
        except ValueError:
            pass
    return None


# ===== 工作流定义 =====

class BaselineCustomWorkflow:
    """基线工作流: 只用 Custom 算子"""
    def __init__(self, name: str, llm_config, dataset: DatasetType):
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = None

    async def __call__(self, problem: str):
        if self.custom is None:
            from workspace.GSM8K.workflows.template.operator import Custom
            self.custom = Custom(self.llm)
        solution = await self.custom(input=problem, instruction="")
        return solution['response'], self.llm.get_usage_summary()["total_cost"]


class AnswerExtractorWorkflow:
    """实验 A: Custom + AnswerExtractor"""
    def __init__(self, name: str, llm_config, dataset: DatasetType):
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = None
        self.extractor = None

    async def __call__(self, problem: str):
        if self.custom is None:
            from workspace.GSM8K.workflows.template.operator import Custom, AnswerExtractor
            self.custom = Custom(self.llm)
            self.extractor = AnswerExtractor(self.llm)
        # Step 1: Generate solution
        solution = await self.custom(
            input=problem,
            instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}."
        )
        # Step 2: Extract answer intelligently
        extracted = await self.extractor(output=solution['response'])
        return extracted['answer'], self.llm.get_usage_summary()["total_cost"]


class ReviewReviseWorkflow:
    """实验 B: Custom + ReviewRevise"""
    def __init__(self, name: str, llm_config, dataset: DatasetType):
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = None
        self.review_revise = None

    async def __call__(self, problem: str):
        if self.custom is None:
            from workspace.GSM8K.workflows.template.operator import Custom, ReviewRevise
            self.custom = Custom(self.llm)
            self.review_revise = ReviewRevise(self.llm)
        # Step 1: Generate initial solution
        solution = await self.custom(
            input=problem,
            instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}."
        )
        # Step 2: Review and revise
        revised = await self.review_revise(problem=problem, solution=solution['response'])
        return revised['response'], self.llm.get_usage_summary()["total_cost"]


class MultiPathReasonerWorkflow:
    """实验 C: MultiPathReasoner"""
    def __init__(self, name: str, llm_config, dataset: DatasetType):
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.reasoner = None

    async def __call__(self, problem: str):
        if self.reasoner is None:
            from workspace.GSM8K.workflows.template.operator import MultiPathReasoner
            self.reasoner = MultiPathReasoner(self.llm)
        result = await self.reasoner(problem=problem)
        return result['response'], self.llm.get_usage_summary()["total_cost"]


class CodeVerifyRescueWorkflow:
    """实验 D: Custom + CodeVerifyRescue"""
    def __init__(self, name: str, llm_config, dataset: DatasetType):
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = None
        self.code_verify = None

    async def __call__(self, problem: str):
        if self.custom is None:
            from workspace.GSM8K.workflows.template.operator import Custom, CodeVerifyRescue
            self.custom = Custom(self.llm)
            self.code_verify = CodeVerifyRescue(self.llm)
        # Step 1: Generate text solution
        solution = await self.custom(
            input=problem,
            instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}."
        )
        # Step 2: Verify with code
        code_result = await self.code_verify(problem=problem, solution=solution['response'])
        if code_result.get('output') and code_result['output'] not in ['No code generated', '']:
            return code_result['output'], self.llm.get_usage_summary()["total_cost"]
        return solution['response'], self.llm.get_usage_summary()["total_cost"]


class CombinedWorkflow:
    """实验 E: Custom + AnswerExtractor + ReviewRevise"""
    def __init__(self, name: str, llm_config, dataset: DatasetType):
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = None
        self.extractor = None
        self.review_revise = None

    async def __call__(self, problem: str):
        if self.custom is None:
            from workspace.GSM8K.workflows.template.operator import Custom, AnswerExtractor, ReviewRevise
            self.custom = Custom(self.llm)
            self.extractor = AnswerExtractor(self.llm)
            self.review_revise = ReviewRevise(self.llm)
        # Step 1: Generate solution
        solution = await self.custom(
            input=problem,
            instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}."
        )
        # Step 2: Review and revise
        revised = await self.review_revise(problem=problem, solution=solution['response'])
        # Step 3: Extract answer
        extracted = await self.extractor(output=revised['response'])
        return extracted['answer'], self.llm.get_usage_summary()["total_cost"]


# ===== 评测逻辑 =====

async def evaluate_workflow(workflow, data, benchmark, sample_size=None):
    """评测单个工作流"""
    if sample_size:
        data = data[:sample_size]

    results = []
    correct = 0
    total = len(data)

    for i, problem in enumerate(data):
        input_text = problem["question"]
        expected_output = benchmark.extract_number(problem["answer"])

        try:
            output, cost = await workflow(input_text)

            # 尝试增强提取
            predicted_number = extract_number_enhanced(output)
            if predicted_number is None:
                predicted_number = benchmark.extract_number(output)

            score = 1.0 if abs(expected_output - predicted_number) <= 1e-6 else 0.0

            results.append({
                "index": i,
                "score": score,
                "expected": expected_output,
                "predicted": predicted_number,
                "cost": cost,
            })

            if score == 1.0:
                correct += 1

            if (i + 1) % 50 == 0:
                print(f"  Progress: {i+1}/{total}, Current Acc: {correct/(i+1):.4f}")

        except Exception as e:
            print(f"  Error on problem {i}: {e}")
            results.append({"index": i, "score": 0, "cost": 0})

    accuracy = correct / total if total > 0 else 0
    return accuracy, correct, total, results


async def run_experiment():
    """运行对比实验"""
    models_config = LLMsConfig.default()
    model_name = "deepseek-chat"
    exec_llm_config = models_config.get(model_name)

    # 加载数据
    data_path = "data/datasets/gsm8k_validate.jsonl"
    benchmark = GSM8KBenchmark(name="GSM8K", file_path=data_path, log_path="workspace/GSM8K/experiment")
    data = await benchmark.load_data()

    # 采样以控制成本（可调整）
    sample_size = 100  # 先用 100 题快速验证，设为 None 跑全集
    test_data = data[:sample_size] if sample_size else data

    print(f"=== GSM8K Experiment: Baseline vs Extended Operators ===")
    print(f"Model: {model_name}")
    print(f"Sample size: {len(test_data)}")
    print(f"Time: {datetime.now().isoformat()}")
    print()

    experiments = [
        ("Baseline (Custom only)", BaselineCustomWorkflow),
        ("+ AnswerExtractor", AnswerExtractorWorkflow),
        ("+ ReviewRevise", ReviewReviseWorkflow),
        ("+ MultiPathReasoner", MultiPathReasonerWorkflow),
        ("+ CodeVerifyRescue", CodeVerifyRescueWorkflow),
        ("+ AnswerExtractor + ReviewRevise", CombinedWorkflow),
    ]

    all_results = {}

    for exp_name, workflow_class in experiments:
        print(f"\n--- Running: {exp_name} ---")
        start_time = time.time()

        workflow = workflow_class(
            name=f"GSM8K_{exp_name}",
            llm_config=exec_llm_config,
            dataset="GSM8K"
        )

        try:
            accuracy, correct, total, detail_results = await evaluate_workflow(
                workflow, test_data, benchmark
            )
            elapsed = time.time() - start_time

            all_results[exp_name] = {
                "accuracy": accuracy,
                "correct": correct,
                "total": total,
                "time_seconds": round(elapsed, 1),
            }

            print(f"  Result: {correct}/{total} = {accuracy:.4f} ({elapsed:.1f}s)")

        except Exception as e:
            print(f"  FAILED: {e}")
            all_results[exp_name] = {"accuracy": 0, "correct": 0, "total": len(test_data), "error": str(e)}

    # 保存结果
    output_dir = "workspace/GSM8K/experiment"
    os.makedirs(output_dir, exist_ok=True)

    with open(f"{output_dir}/comparison_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    # 打印汇总
    print(f"\n\n{'='*60}")
    print(f"{'Experiment Results Summary':^60}")
    print(f"{'='*60}")
    print(f"{'Experiment':<40} {'Accuracy':<10} {'Time':<10}")
    print(f"{'-'*60}")

    baseline_acc = all_results.get("Baseline (Custom only)", {}).get("accuracy", 0)
    for exp_name, result in all_results.items():
        acc = result.get("accuracy", 0)
        delta = acc - baseline_acc
        delta_str = f"({delta:+.4f})" if exp_name != "Baseline (Custom only)" else ""
        t = result.get("time_seconds", 0)
        print(f"{exp_name:<40} {acc:.4f} {delta_str:<10} {t:.1f}s")

    print(f"{'='*60}")
    print(f"\nResults saved to {output_dir}/comparison_results.json")

    return all_results


if __name__ == "__main__":
    asyncio.run(run_experiment())
