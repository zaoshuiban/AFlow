# -*- coding: utf-8 -*-
"""
修复验证实验 - 针对已知失败案例的精准测试
只测试基线中失败的 6 题，验证 AnswerExtractor 修复后的效果
"""
import asyncio
import json
import re
from typing import Optional

from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.evaluator import DatasetType
from benchmarks.gsm8k import GSM8KBenchmark


def extract_number_enhanced(text: str) -> Optional[float]:
    """增强的数字提取 - 支持 \\boxed{}, \\frac, 混合数等"""
    # 1. Try \boxed{...}
    boxed = re.findall(r'\\boxed\{([^}]+)\}', str(text))
    if boxed:
        val = boxed[-1].strip()
        # Handle LaTeX fractions: \frac{a}{b}
        frac_match = re.match(r'\\frac\{(-?\d+)\}\{(-?\d+)\}', val)
        if frac_match:
            num, den = int(frac_match.group(1)), int(frac_match.group(2))
            if den != 0:
                result = num / den
                return result
        # Handle mixed numbers: "21\frac{1}{3}"
        mixed_match = re.match(r'(-?\d+)\\frac\{(-?\d+)\}\{(-?\d+)\}', val)
        if mixed_match:
            whole = int(mixed_match.group(1))
            num = int(mixed_match.group(2))
            den = int(mixed_match.group(3))
            if den != 0:
                result = whole + num / den
                return result
        val = val.replace('\\%', '').replace('%', '').replace(',', '').strip()
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


async def main():
    models_config = LLMsConfig.default()
    model_name = "deepseek-chat"
    exec_llm_config = models_config.get(model_name)

    benchmark = GSM8KBenchmark(name="GSM8K", file_path="data/datasets/gsm8k_validate.jsonl", log_path="workspace/GSM8K/experiment")
    data = await benchmark.load_data()

    # 加载完整实验结果，找到基线失败的索引
    with open("workspace/GSM8K/experiment/full_results.json") as f:
        full_results = json.load(f)

    baseline_failure_indices = [f['index'] for f in full_results['Baseline']['failures'] if 'error' not in f]
    print(f"Baseline failure indices: {baseline_failure_indices}")

    # 只测试这些失败案例
    test_cases = [data[i] for i in baseline_failure_indices]

    from workspace.GSM8K.workflows.template.operator import Custom, AnswerExtractor

    llm = create_llm_instance(exec_llm_config)
    custom = Custom(llm)
    extractor = AnswerExtractor(llm)

    print(f"\n=== Targeted Test on {len(test_cases)} Baseline Failures ===\n")

    for idx, case in zip(baseline_failure_indices, test_cases):
        question = case["question"]
        expected = benchmark.extract_number(case["answer"])

        # 原始方法
        custom_result = await custom(input=question, instruction="")
        original_extract = benchmark.extract_number(custom_result['response'])
        enhanced_extract = extract_number_enhanced(custom_result['response'])

        # AnswerExtractor 方法
        ae_result = await extractor(output=custom_result['response'])
        ae_answer = ae_result['answer']
        try:
            ae_number = float(str(ae_answer).replace(',', ''))
        except:
            ae_number = None

        print(f"Index {idx}: Expected = {expected}")
        print(f"  Original extract: {original_extract} ({'✓' if abs(original_extract - expected) < 1e-6 else '✗'})")
        print(f"  Enhanced extract: {enhanced_extract} ({'✓' if enhanced_extract and abs(enhanced_extract - expected) < 1e-6 else '✗'})")
        print(f"  AE result: {ae_answer} → {ae_number} ({'✓' if ae_number and abs(ae_number - expected) < 1e-6 else '✗'})")
        print()

    # 同时测试之前分数格式出错的案例
    frac_test_indices = [68, 106, 118]
    print(f"\n=== Testing Fraction/Format Edge Cases ===\n")

    for idx in frac_test_indices:
        if idx >= len(data):
            continue
        case = data[idx]
        question = case["question"]
        expected = benchmark.extract_number(case["answer"])

        custom_result = await custom(input=question, instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}.")

        ae_result = await extractor(output=custom_result['response'])
        ae_answer = ae_result['answer']

        print(f"Index {idx}: Expected = {expected}")
        print(f"  AE result: '{ae_answer}'")
        # Show the boxed value found
        boxed = re.findall(r'\\boxed\{([^}]+)\}', custom_result['response'])
        print(f"  \\boxed{{}} content: {boxed[-1] if boxed else 'NOT FOUND'}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
