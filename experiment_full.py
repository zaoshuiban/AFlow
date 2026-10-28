# -*- coding: utf-8 -*-
"""
最小可行实验 - 完整版
跑全部 GSM8K 验证集 (264题)，对比:
1. Baseline (Custom only, original extract_number)
2. + AnswerExtractor (智能答案提取)
3. + AnswerExtractor + ReviewRevise (智能提取 + 自审查)
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
    """增强的数字提取"""
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
    def __init__(self, name, llm_config, dataset):
        self.name = name; self.dataset = dataset; self.llm = create_llm_instance(llm_config); self.custom = None
    async def __call__(self, problem):
        if self.custom is None:
            from workspace.GSM8K.workflows.template.operator import Custom
            self.custom = Custom(self.llm)
        solution = await self.custom(input=problem, instruction="")
        return solution['response'], self.llm.get_usage_summary()["total_cost"]


class AnswerExtractorWorkflow:
    def __init__(self, name, llm_config, dataset):
        self.name = name; self.dataset = dataset; self.llm = create_llm_instance(llm_config)
        self.custom = None; self.extractor = None
    async def __call__(self, problem):
        if self.custom is None:
            from workspace.GSM8K.workflows.template.operator import Custom, AnswerExtractor
            self.custom = Custom(self.llm); self.extractor = AnswerExtractor(self.llm)
        solution = await self.custom(input=problem, instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}.")
        extracted = await self.extractor(output=solution['response'])
        return extracted['answer'], self.llm.get_usage_summary()["total_cost"]


class CombinedWorkflow:
    def __init__(self, name, llm_config, dataset):
        self.name = name; self.dataset = dataset; self.llm = create_llm_instance(llm_config)
        self.custom = None; self.extractor = None; self.review_revise = None
    async def __call__(self, problem):
        if self.custom is None:
            from workspace.GSM8K.workflows.template.operator import Custom, AnswerExtractor, ReviewRevise
            self.custom = Custom(self.llm); self.extractor = AnswerExtractor(self.llm)
            self.review_revise = ReviewRevise(self.llm)
        solution = await self.custom(input=problem, instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}.")
        revised = await self.review_revise(problem=problem, solution=solution['response'])
        extracted = await self.extractor(output=revised['response'])
        return extracted['answer'], self.llm.get_usage_summary()["total_cost"]


async def evaluate_workflow(workflow, data, benchmark, extract_fn):
    correct = 0; total = len(data); details = []
    for i, problem in enumerate(data):
        expected_output = benchmark.extract_number(problem["answer"])
        try:
            output, cost = await workflow(problem["question"])
            predicted_number = extract_fn(output)
            if predicted_number is None:
                predicted_number = benchmark.extract_number(output)
            score = 1.0 if abs(expected_output - predicted_number) <= 1e-6 else 0.0
            if score == 1.0: correct += 1
            else: details.append({"index": i, "expected": expected_output, "predicted": predicted_number, "output": str(output)[:300]})
            if (i + 1) % 50 == 0: print(f"  [{i+1}/{total}] Acc: {correct/(i+1):.4f}")
        except Exception as e:
            print(f"  Error {i}: {e}")
            details.append({"index": i, "error": str(e)})
    return correct / total, correct, total, details


async def main():
    models_config = LLMsConfig.default()
    model_name = "deepseek-chat"
    exec_llm_config = models_config.get(model_name)

    benchmark = GSM8KBenchmark(name="GSM8K", file_path="data/datasets/gsm8k_validate.jsonl", log_path="workspace/GSM8K/experiment")
    data = await benchmark.load_data()
    print(f"=== Full Experiment: {len(data)} problems ===")
    print(f"Model: {model_name}, Time: {datetime.now().isoformat()}\n")

    results = {}

    for name, wf_class, extract_fn in [
        ("Baseline", BaselineWorkflow, benchmark.extract_number),
        ("+ AnswerExtractor", AnswerExtractorWorkflow, extract_number_enhanced),
        ("+ AE + ReviewRevise", CombinedWorkflow, extract_number_enhanced),
    ]:
        print(f"\n--- {name} ---")
        start = time.time()
        wf = wf_class(name=name, llm_config=exec_llm_config, dataset="GSM8K")
        acc, correct, total, details = await evaluate_workflow(wf, data, benchmark, extract_fn)
        elapsed = time.time() - start
        results[name] = {"accuracy": acc, "correct": correct, "total": total, "time": round(elapsed, 1), "failures": details}
        print(f"  {name}: {correct}/{total} = {acc:.4f} ({elapsed:.1f}s)")

    output_dir = "workspace/GSM8K/experiment"
    with open(f"{output_dir}/full_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    baseline_acc = results["Baseline"]["accuracy"]
    print(f"\n{'='*55}")
    print(f"  {'Experiment':<25} {'Acc':<10} {'Delta':<10} {'Time':<8}")
    print(f"{'-'*55}")
    for name, r in results.items():
        delta = r['accuracy'] - baseline_acc
        delta_str = f"{delta:+.4f}" if name != "Baseline" else "---"
        print(f"  {name:<25} {r['accuracy']:.4f}   {delta_str:<10} {r['time']}s")
    print(f"{'='*55}")
    print(f"Saved to {output_dir}/full_results.json")


if __name__ == "__main__":
    asyncio.run(main())
