# -*- coding: utf-8 -*-
"""
最小可行实验 - 快速验证版
只跑 20 题，只对比 Baseline vs AnswerExtractor（最大收益算子）
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime
from typing import Optional

from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.evaluator import DatasetType
from benchmarks.gsm8k import GSM8KBenchmark


def extract_number_enhanced(text: str) -> Optional[float]:
    """增强的数字提取 - 优先从 \\boxed{} 提取"""
    boxed = re.findall(r'\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}', str(text))
    if boxed:
        val = boxed[-1].strip().replace(",", "")
        try:
            return float(val)
        except ValueError:
            pass
    all_matches = re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|\d+\.\d+", str(text))
    if all_matches:
        try:
            return float(all_matches[-1].replace(",", ""))
        except ValueError:
            pass
    return None


class BaselineWorkflow:
    """基线: Custom only, 用原始 extract_number"""
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
    """实验: Custom + AnswerExtractor"""
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
        solution = await self.custom(
            input=problem,
            instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}."
        )
        extracted = await self.extractor(output=solution['response'])
        return extracted['answer'], self.llm.get_usage_summary()["total_cost"]


async def evaluate_workflow(workflow, data, benchmark, extract_fn):
    """评测单个工作流"""
    correct = 0
    total = len(data)
    details = []

    for i, problem in enumerate(data):
        input_text = problem["question"]
        expected_output = benchmark.extract_number(problem["answer"])

        try:
            output, cost = await workflow(input_text)
            predicted_number = extract_fn(output)
            if predicted_number is None:
                predicted_number = benchmark.extract_number(output)
            score = 1.0 if abs(expected_output - predicted_number) <= 1e-6 else 0.0

            if score == 1.0:
                correct += 1
            else:
                details.append({
                    "index": i,
                    "expected": expected_output,
                    "predicted": predicted_number,
                    "output_snippet": str(output)[:200],
                })

            if (i + 1) % 10 == 0:
                print(f"  [{i+1}/{total}] Acc: {correct/(i+1):.4f}")

        except Exception as e:
            print(f"  Error on {i}: {e}")
            details.append({"index": i, "error": str(e)})

    return correct / total, correct, total, details


async def main():
    models_config = LLMsConfig.default()
    model_name = "deepseek-chat"
    exec_llm_config = models_config.get(model_name)

    data_path = "data/datasets/gsm8k_validate.jsonl"
    benchmark = GSM8KBenchmark(name="GSM8K", file_path=data_path, log_path="workspace/GSM8K/experiment")
    data = await benchmark.load_data()

    # 使用 30 题做快速验证
    sample_size = 30
    test_data = data[:sample_size]

    print(f"=== Quick Validation Experiment ===")
    print(f"Model: {model_name}, Sample: {sample_size}, Time: {datetime.now().isoformat()}")
    print()

    # === Baseline ===
    print("--- Baseline (Custom only, original extract) ---")
    start = time.time()
    baseline_wf = BaselineWorkflow(name="baseline", llm_config=exec_llm_config, dataset="GSM8K")
    baseline_acc, baseline_correct, baseline_total, baseline_details = await evaluate_workflow(
        baseline_wf, test_data, benchmark, benchmark.extract_number  # original extract
    )
    baseline_time = time.time() - start
    print(f"  Baseline: {baseline_correct}/{baseline_total} = {baseline_acc:.4f} ({baseline_time:.1f}s)")

    # === AnswerExtractor ===
    print("\n--- + AnswerExtractor (enhanced extract) ---")
    start = time.time()
    ae_wf = AnswerExtractorWorkflow(name="ae", llm_config=exec_llm_config, dataset="GSM8K")
    ae_acc, ae_correct, ae_total, ae_details = await evaluate_workflow(
        ae_wf, test_data, benchmark, extract_number_enhanced  # enhanced extract
    )
    ae_time = time.time() - start
    print(f"  + AnswerExtractor: {ae_correct}/{ae_total} = {ae_acc:.4f} ({ae_time:.1f}s)")

    # === 结果汇总 ===
    output_dir = "workspace/GSM8K/experiment"
    os.makedirs(output_dir, exist_ok=True)

    results = {
        "model": model_name,
        "sample_size": sample_size,
        "timestamp": datetime.now().isoformat(),
        "baseline": {"accuracy": baseline_acc, "correct": baseline_correct, "total": baseline_total, "time": baseline_time},
        "answer_extractor": {"accuracy": ae_acc, "correct": ae_correct, "total": ae_total, "time": ae_time},
        "improvement": {"delta": ae_acc - baseline_acc, "percentage_change": ((ae_acc - baseline_acc) / baseline_acc * 100) if baseline_acc > 0 else 0},
        "baseline_failures": baseline_details,
        "ae_failures": ae_details,
    }

    with open(f"{output_dir}/quick_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Baseline:    {baseline_acc:.4f} ({baseline_correct}/{baseline_total})")
    print(f"  + AE:        {ae_acc:.4f} ({ae_correct}/{ae_total})")
    print(f"  Improvement: {ae_acc - baseline_acc:+.4f} ({(ae_acc - baseline_acc)/baseline_acc*100:+.1f}%)")
    print(f"{'='*50}")
    print(f"Results saved to {output_dir}/quick_results.json")


if __name__ == "__main__":
    asyncio.run(main())
