# -*- coding: utf-8 -*-
"""
最小可行实验 - Step 1: 用固定算子集合跑 GSM8K 基线并收集详细失败日志
"""
import asyncio
import json
import os
import re
from typing import Optional, Tuple, List

from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.operators import Custom, ScEnsemble, Programmer
from scripts.evaluator import DatasetType
from benchmarks.gsm8k import GSM8KBenchmark


class BaselineWorkflow:
    """基线工作流：只用 Custom 算子（直接让 LLM 解题）"""
    def __init__(self, name: str, llm_config, dataset: DatasetType):
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = Custom(self.llm)

    async def __call__(self, problem: str):
        solution = await self.custom(input=problem, instruction="")
        return solution['response'], self.llm.get_usage_summary()["total_cost"]


class BaselineWithProgrammer:
    """基线工作流2：Custom + Programmer（代码验证）"""
    def __init__(self, name: str, llm_config, dataset: DatasetType):
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = Custom(self.llm)
        self.programmer = Programmer(self.llm)

    async def __call__(self, problem: str):
        # 先让 LLM 分析问题
        analysis_result = await self.custom(input=problem, instruction="Analyze this math problem step by step:")
        # 再用 Programmer 写代码求解
        code_result = await self.programmer(problem=problem, analysis=analysis_result['response'])
        if code_result.get('output') and code_result['output'] != 'No code generated':
            return code_result['output'], self.llm.get_usage_summary()["total_cost"]
        # fallback to custom
        return analysis_result['response'], self.llm.get_usage_summary()["total_cost"]


def extract_number_detailed(text: str) -> Tuple[Optional[float], str]:
    """增强的数字提取，返回提取到的数字和提取模式描述"""
    # 尝试提取 \boxed{} 中的数字
    boxed_matches = re.findall(r'\\boxed\{([^}]+)\}', str(text))
    if boxed_matches:
        last_boxed = boxed_matches[-1].strip()
        try:
            return float(last_boxed.replace(",", "")), "boxed"
        except ValueError:
            pass

    # 提取所有数字
    all_matches = re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|\d+\.\d+", str(text))
    if all_matches:
        last_number = all_matches[-1].replace(",", "")
        try:
            return float(last_number), "last_number"
        except ValueError:
            pass

    return None, "none"


async def run_baseline_with_detailed_log():
    """运行基线并收集详细的失败案例分析"""
    models_config = LLMsConfig.default()

    # 使用配置中的模型
    model_name = "deepseek-chat"
    exec_llm_config = models_config.get(model_name)

    # 创建基线工作流
    workflow = BaselineWorkflow(name="GSM8K_Baseline", llm_config=exec_llm_config, dataset="GSM8K")

    # 加载数据
    data_path = "data/datasets/gsm8k_validate.jsonl"
    benchmark = GSM8KBenchmark(name="GSM8K", file_path=data_path, log_path="workspace/GSM8K/baseline_log")

    # 确保日志目录存在
    os.makedirs("workspace/GSM8K/baseline_log", exist_ok=True)

    # 运行评测
    data = await benchmark.load_data()

    results = []
    failure_cases = []

    for i, problem in enumerate(data):
        input_text = problem["question"]
        expected_output = benchmark.extract_number(problem["answer"])

        try:
            output, cost = await workflow(input_text)
            predicted_number = benchmark.extract_number(output)
            score, extracted_output = benchmark.calculate_score(expected_output, predicted_number)

            # 增强的数字提取分析
            enhanced_number, extract_mode = extract_number_detailed(output)

            result = {
                "index": i,
                "question": input_text,
                "right_answer": expected_output,
                "model_output": output[:2000],  # 截断过长输出
                "extracted_output": extracted_output,
                "enhanced_extracted": enhanced_number,
                "extract_mode": extract_mode,
                "score": score,
                "cost": cost,
            }

            results.append(result)

            if score == 0:
                # 详细记录失败案例
                failure_case = {
                    "index": i,
                    "question": input_text,
                    "right_answer": expected_output,
                    "model_output": output[:3000],
                    "extracted_output": extracted_output,
                    "enhanced_extracted": enhanced_number,
                    "extract_mode": extract_mode,
                    "failure_type": "extraction" if enhanced_number == expected_output else "reasoning",
                    "all_numbers_in_output": re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|\d+\.\d+", output),
                }
                failure_cases.append(failure_case)

            if (i + 1) % 50 == 0:
                current_score = sum(r["score"] for r in results) / len(results)
                print(f"Progress: {i+1}/{len(data)}, Current Score: {current_score:.4f}")

        except Exception as e:
            print(f"Error on problem {i}: {e}")
            results.append({"index": i, "score": 0, "cost": 0})
            failure_cases.append({
                "index": i,
                "question": input_text,
                "right_answer": expected_output,
                "error": str(e),
                "failure_type": "exception",
            })

    # 计算总分
    total_score = sum(r["score"] for r in results)
    avg_score = total_score / len(results) if results else 0

    # 保存结果
    summary = {
        "model": model_name,
        "workflow": "Custom_only",
        "total_problems": len(results),
        "correct": int(total_score),
        "accuracy": avg_score,
        "failure_count": len(failure_cases),
        "extraction_failures": len([f for f in failure_cases if f.get("failure_type") == "extraction"]),
        "reasoning_failures": len([f for f in failure_cases if f.get("failure_type") == "reasoning"]),
        "exception_failures": len([f for f in failure_cases if f.get("failure_type") == "exception"]),
    }

    # 保存到文件
    output_dir = "workspace/GSM8K/experiment"
    os.makedirs(output_dir, exist_ok=True)

    with open(f"{output_dir}/baseline_results.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    with open(f"{output_dir}/baseline_failures.json", "w", encoding="utf-8") as f:
        json.dump(failure_cases, f, indent=2, ensure_ascii=False)

    with open(f"{output_dir}/baseline_all_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n=== Baseline Results ===")
    print(f"Model: {model_name}")
    print(f"Total: {len(results)}, Correct: {int(total_score)}, Accuracy: {avg_score:.4f}")
    print(f"Failures: {len(failure_cases)}")
    print(f"  - Extraction failures (correct answer in output but wrongly extracted): {summary['extraction_failures']}")
    print(f"  - Reasoning failures (wrong answer): {summary['reasoning_failures']}")
    print(f"  - Exception failures: {summary['exception_failures']}")
    print(f"\nResults saved to {output_dir}/")

    return summary, failure_cases


if __name__ == "__main__":
    asyncio.run(run_baseline_with_detailed_log())
