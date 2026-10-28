# -*- coding: utf-8 -*-
# @Date    : 6/26/2024 17:07 PM
# @Author  : didi
# @Desc    : prompts of operators

ANSWER_GENERATION_PROMPT = """
Think step by step and solve the problem.
1. In the "thought" field, explain your thinking process in detail.
2. In the "answer" field, provide the final answer concisely and clearly. The answer should be a direct response to the question, without including explanations or reasoning.
Your task: {input}
"""

FORMAT_PROMPT = """
For the question described as {problem_description},
please extract a short and concise answer contains only one word/few words from the following solution: {solution}.
Make sure there are no additional comments or explanations in your response.
"""

SC_ENSEMBLE_PROMPT = """
Given the question described as follows: {question}
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

MD_ENSEMBLE_PROMPT = """
Given the question described as follows: {question}
Several solutions have been generated to address the given question. They are as follows:
{solutions}

Carefully evaluate these solutions and identify the solution that is more capable of solving the problem compared to other solutions, as this is crucial for problem-solving.

In the "thought" field, provide a detailed explanation of your thought process. In the "solution_letter" field, output only the single letter ID (A, B, C, etc.) corresponding to the solution. Do not include any additional text or explanation in the "solution_letter" field.
"""

REVIEW_PROMPT = """
Given a problem and a thoughtful solution, your task is to using critical thinking (questioning) to review the solution's correctness and provide a review result in boolean format.

problem: {problem}
solution: {solution}

If you are more than 95 percent confident that the final answer is incorrect, please return False and give a feedback for the error. Otherwise, please return True and give a explanation for the correctness.
"""

REVISE_PROMPT = """
Given a problem and a thoughtful solution which is just reviewed as incorrect, your task is to revise the solution to solve the question and ensure the final code solution is wrapped with ```python```.

problem: {problem}
solution: {solution}
feedback: {feedback}

Ensure the output code is self-contained, and without any additional text or test cases.
"""

# === Auto-generated prompts for GSM8K experiment ===

ANSWER_EXTRACT_PROMPT = """
You are an answer extraction assistant. Given a mathematical solution text, your task is to extract the FINAL numerical answer.

Rules:
1. If the solution contains \\boxed{{answer}}, extract the number inside \\boxed{{}}.
2. If there is no \\boxed{{}} notation, look for phrases like "The answer is", "Final answer:", or similar.
3. The answer should be a single number (integer, decimal, or fraction).
4. Do NOT extract intermediate calculation results.
5. If the answer is a mixed number like "21 1/3", convert it: 21 1/3 = 64/3 ≈ 21.333...

Solution text to extract from:
{output}

Extract the final numerical answer.
"""

REVIEW_REVISE_PROMPT = """
You are a mathematics reviewer. Your task is to:
1. Review the given solution for a math problem
2. Check if the reasoning and final answer are correct
3. If there is an error, provide a corrected solution

Problem: {problem}
Solution: {solution}

Review the solution step by step. Check:
- Are all calculations correct?
- Is the interpretation of the problem correct?
- Is the final answer correct?

If the solution is correct, set review_result to True.
If there is an error, set review_result to False, explain the error in feedback, and provide the corrected solution.

IMPORTANT: Assign a confidence score (0.0-1.0) to your review. Only set review_result to False if you are highly confident (>= 0.8) that the solution is wrong. If you are unsure, keep review_result as True to avoid over-correction.
"""

# === MATH-specific prompts ===

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
