# -*- coding: utf-8 -*-
"""
最小可行实验 - Step 1&2: 分析已有的失败案例，提取缺失算子需求
"""
import json
import re
from typing import Optional, Tuple


def extract_number_detailed(text: str) -> Tuple[Optional[float], str]:
    """增强的数字提取"""
    # 尝试提取 \boxed{} 中的数字
    boxed_matches = re.findall(r'\\boxed\{([^}]+)\}', str(text))
    if boxed_matches:
        last_boxed = boxed_matches[-1].strip()
        try:
            return float(last_boxed.replace(",", "")), "boxed"
        except ValueError:
            pass
    all_matches = re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|\d+\.\d+", str(text))
    if all_matches:
        last_number = all_matches[-1].replace(",", "")
        try:
            return float(last_number), "last_number"
        except ValueError:
            pass
    return None, "none"


def analyze_failures():
    """分析已有失败案例"""
    with open("workspace/GSM8K/workflows/round_1/log.json", "r", encoding="utf-8") as f:
        failure_cases = json.load(f)

    extraction_failures = []
    reasoning_failures = []
    all_numbers_analysis = []

    for case in failure_cases:
        right_answer = case["right_answer"]
        model_output = case["model_output"]
        extracted_output = case["extracted_output"]

        # 增强提取
        enhanced_number, extract_mode = extract_number_detailed(model_output)

        # 提取所有数字
        all_numbers = re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|\d+\.\d+", model_output)
        all_numbers_float = []
        for n in all_numbers:
            try:
                all_numbers_float.append(float(n.replace(",", "")))
            except:
                pass

        # 判断是否答案在输出中但被错误提取
        answer_in_output = right_answer in all_numbers_float

        analysis = {
            "right_answer": right_answer,
            "extracted_output": extracted_output,
            "enhanced_extracted": enhanced_number,
            "extract_mode": extract_mode,
            "answer_in_output": answer_in_output,
            "all_numbers": all_numbers_float,
            "output_snippet": model_output[:500],
        }
        all_numbers_analysis.append(analysis)

        if answer_in_output:
            extraction_failures.append(analysis)
        else:
            reasoning_failures.append(analysis)

    # 打印分析结果
    print(f"=== Failure Analysis ===")
    print(f"Total failures: {len(failure_cases)}")
    print(f"Extraction failures (correct answer IN output but wrongly extracted): {len(extraction_failures)}")
    print(f"Reasoning failures (correct answer NOT in output): {len(reasoning_failures)}")

    print(f"\n--- Extraction Failures Detail ---")
    for i, f in enumerate(extraction_failures):
        print(f"\n  Case {i+1}:")
        print(f"    Right answer: {f['right_answer']}")
        print(f"    Extracted: {f['extracted_output']} (mode: {f['extract_mode']})")
        print(f"    Enhanced: {f['enhanced_extracted']}")
        print(f"    All numbers in output: {f['all_numbers'][-5:]}")  # 最后5个

    print(f"\n--- Reasoning Failures Detail ---")
    for i, f in enumerate(reasoning_failures):
        print(f"\n  Case {i+1}:")
        print(f"    Right answer: {f['right_answer']}")
        print(f"    Extracted: {f['extracted_output']}")
        print(f"    Answer in output: {f['answer_in_output']}")

    # 统计失败模式
    print(f"\n\n=== Failure Pattern Summary ===")
    print(f"1. Answer Extraction Failure: {len(extraction_failures)}/{len(failure_cases)} ({len(extraction_failures)/len(failure_cases)*100:.1f}%)")
    print(f"   - The correct answer exists in the model output but extract_number() picks the wrong number")
    print(f"   - Root cause: extract_number takes the LAST number, but the answer is often in \\boxed{{}} or earlier in the text")
    print(f"2. Reasoning Failure: {len(reasoning_failures)}/{len(failure_cases)} ({len(reasoning_failures)/len(failure_cases)*100:.1f}%)")
    print(f"   - The model's reasoning leads to a wrong answer")

    return extraction_failures, reasoning_failures


def generate_missing_operator_list(extraction_failures, reasoning_failures):
    """根据失败模式生成缺失算子列表"""

    missing_operators = []

    # 缺失算子 1: AnswerExtractor - 智能答案提取
    missing_operators.append({
        "name": "AnswerExtractor",
        "description": "Intelligently extracts the final numerical answer from a model's reasoning output. "
                       "Prioritizes \\boxed{{}} notation, then uses heuristics to find the final answer "
                       "rather than blindly taking the last number in the text. "
                       "This solves the critical problem where the correct answer exists in the output "
                       f"but is not properly extracted ({len(extraction_failures)} cases in baseline).",
        "interface": "answer_extractor(output: str) -> dict with key 'answer' of type str",
        "failure_cases_addressed": len(extraction_failures),
        "category": "post_processing"
    })

    # 缺失算子 2: ReviewRevise - 审查修正
    missing_operators.append({
        "name": "ReviewRevise",
        "description": "Reviews a solution for correctness and revises it if errors are found. "
                       "First generates a solution, then a separate LLM call reviews it, "
                       "and if the review finds issues, another call revises the solution. "
                       "This addresses reasoning errors where the model makes calculation mistakes "
                       f"that could be caught by self-review ({len(reasoning_failures)} cases in baseline).",
        "interface": "review_revise(problem: str, solution: str) -> dict with key 'response' of type str",
        "failure_cases_addressed": len(reasoning_failures),
        "category": "self_correction"
    })

    # 缺失算子 3: MultiPathReasoner - 多路径推理 + 答案验证
    missing_operators.append({
        "name": "MultiPathReasoner",
        "description": "Generates multiple independent solutions for the same problem using different "
                       "reasoning strategies (e.g., step-by-step, code-based, backward-chaining), "
                       "then uses majority voting to select the most consistent answer. "
                       "Combines the benefits of ScEnsemble with diverse reasoning paths. "
                       "Addresses both extraction and reasoning failures through redundancy.",
        "interface": "multi_path_reasoner(problem: str, num_paths: int = 3) -> dict with key 'response' of type str",
        "failure_cases_addressed": len(extraction_failures) + len(reasoning_failures),
        "category": "ensemble"
    })

    # 缺失算子 4: CodeVerifyRescue - 代码验证兜底
    missing_operators.append({
        "name": "CodeVerifyRescue",
        "description": "Takes a text-based reasoning solution and uses it as analysis input to generate "
                       "Python code that computes the answer. If code execution succeeds, uses the code output. "
                       "This serves as a 'rescue' operator when text-based reasoning might have calculation errors. "
                       "Unlike the existing Programmer operator, this is specifically designed to REFINE and VERIFY "
                       "an existing solution rather than generate from scratch.",
        "interface": "code_verify_rescue(problem: str, solution: str) -> dict with keys 'code', 'output' of type str",
        "failure_cases_addressed": len(reasoning_failures),
        "category": "verification"
    })

    print(f"\n\n=== Missing Operator List ===")
    for op in missing_operators:
        print(f"\n  {op['name']} [{op['category']}]:")
        print(f"    Description: {op['description']}")
        print(f"    Interface: {op['interface']}")
        print(f"    Addresses: {op['failure_cases_addressed']} failure cases")

    # 保存到文件
    with open("workspace/GSM8K/experiment/missing_operators.json", "w", encoding="utf-8") as f:
        json.dump(missing_operators, f, indent=2, ensure_ascii=False)

    return missing_operators


if __name__ == "__main__":
    extraction_failures, reasoning_failures = analyze_failures()
    missing_operators = generate_missing_operator_list(extraction_failures, reasoning_failures)
