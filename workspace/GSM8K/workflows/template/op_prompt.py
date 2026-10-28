SC_ENSEMBLE_PROMPT = """
Given the question described as follows: {problem}
Several solutions have been generated to address the given question. They are as follows:
{solutions}

Carefully evaluate these solutions and identify the answer that appears most frequently across them. This consistency in answers for determining the most reliable solution.

In the "thought" field, provide a detailed explanation of your thought process. In the "solution_letter" field, output only the single letter ID (A, B, C, etc.) corresponding to the most consistent solution.
"""

PYTHON_CODE_VERIFIER_PROMPT = """
You are a professional Python programmer. Your task is to write complete, self-contained code based on a given mathematical problem and output the answer.

Problem description: {problem}
Other analysis: {analysis}
{feedback}

Your code should:
1. Implement the calculation steps described in the problem.
2. Define a function named `solve` that performs the calculation and returns the result.
3. `solve` function return the final calculation result.
"""

ANSWER_EXTRACT_PROMPT = """
You are an answer extraction assistant. Given a mathematical solution text, extract the FINAL numerical answer.

Rules:
1. If the solution contains \\boxed{{answer}}, extract the number inside \\boxed{{}}.
2. If there is no \\boxed{{}} notation, look for phrases like "The answer is", "Final answer:", or similar.
3. The answer should be a single number (integer, decimal, or fraction).
4. Do NOT extract intermediate calculation results.

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

Review the solution step by step. If the solution is correct, set review_result to True.
If there is an error, set review_result to False, explain the error in feedback, and provide the corrected solution.
"""
