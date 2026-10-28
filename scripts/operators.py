# -*- coding: utf-8 -*-
# @Date    : 2025-03-31
# @Author  : didi & zhaoyang
# @Desc    : operator demo of aflow

import asyncio
import concurrent.futures
import random
import re
import sys
import traceback
from collections import Counter
from typing import Dict, List, Literal, Optional, Tuple, TypedDict

from tenacity import retry, stop_after_attempt, wait_fixed

from scripts.async_llm import AsyncLLM
from scripts.logs import logger
from scripts.formatter import BaseFormatter, FormatError, XmlFormatter, TextFormatter, CodeFormatter


class OperatorResult(TypedDict, total=False):
    """标准化算子输出契约"""
    status: Literal["success", "error"]  # 执行状态
    response: str                         # 主要输出内容（保持向后兼容）
    observation: str                      # 给下游/优化器的结构化反馈
from scripts.operator_an import (
    AnswerGenerateOp,
    AnswerExtractOp,
    AnswerFormatOp,
    CodeGenerateOp,
    FormatOp,
    GenerateOp,
    MdEnsembleOp,
    ReflectionTestOp,
    ReviewOp,
    ReviewReviseOp,
    ReviseOp,
    ScEnsembleOp,
    StrategySelectOp,
    SymbolicVerifyOp,
) # All BaseModel

from scripts.prompts.prompt import (
    ANSWER_GENERATION_PROMPT,
    ANSWER_EXTRACT_PROMPT,
    ANSWER_FORMAT_PROMPT,
    FORMAT_PROMPT,
    MD_ENSEMBLE_PROMPT,
    PYTHON_CODE_VERIFIER_PROMPT,
    REFLECTION_ON_PUBLIC_TEST_PROMPT,
    REVIEW_PROMPT,
    REVIEW_REVISE_PROMPT,
    REVISE_PROMPT,
    SC_ENSEMBLE_PROMPT,
    STRATEGY_SELECT_PROMPT,
    SYMBOLIC_VERIFY_PROMPT,
)
from scripts.utils.code import (
    extract_test_cases_from_jsonl,
    test_case_2_test_function,
)

class Operator:
    def __init__(self, llm: AsyncLLM, name: str):
        self.name = name
        self.llm = llm

    def __call__(self, *args, **kwargs):
        raise NotImplementedError

    async def _safe_call(self, coro):
        """统一异常屏障：算子内部异常不会崩溃整轮，而是返回 status=error"""
        try:
            result = await coro
            # 确保有 status 字段
            if isinstance(result, dict):
                result.setdefault("status", "success")
                result.setdefault("observation", "")
                return result
            return {"status": "success", "response": str(result), "observation": ""}
        except Exception as e:
            exc_type, exc_value, exc_tb = sys.exc_info()
            tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
            tb_str = "".join(tb_lines[-3:])  # 只保留最后3行
            err_msg = f"{type(e).__name__}: {e}"
            logger.warning(f"🛡️ 算子 {self.name} 异常被捕获: {err_msg}")
            return {
                "status": "error",
                "response": "",
                "observation": f"Operator {self.name} failed: {err_msg}\n{tb_str}",
            }

    async def _fill_node(self, op_class, prompt, mode=None, **extra_kwargs):
        # Create appropriate formatter based on mode
        formatter = self._create_formatter(op_class, mode, **extra_kwargs)
        
        try:
            # Use the formatter with AsyncLLM
            if formatter:
                response = await self.llm.call_with_format(prompt, formatter)
            else:
                # Fallback to direct call if no formatter is needed
                response = await self.llm(prompt)
                
            # Convert to expected format based on the original implementation
            if isinstance(response, dict):
                response.setdefault("status", "success")
                response.setdefault("observation", "")
                return response
            else:
                return {"status": "success", "response": response, "observation": ""}
        except FormatError as e:
            logger.warning(f"Format error in {self.name}: {str(e)}")
            return {"status": "error", "response": "", "observation": f"FormatError in {self.name}: {e}"}
    
    def _create_formatter(self, op_class, mode=None, **extra_kwargs) -> Optional[BaseFormatter]:
        """Create appropriate formatter based on operation class and mode"""
        if mode == "xml_fill":
            return XmlFormatter.from_model(op_class)
        elif mode == "code_fill":
            function_name = extra_kwargs.get("function_name")
            return CodeFormatter(function_name=function_name)
        elif mode == "single_fill":
            return TextFormatter()
        else:
            # Return None if no specific formatter is needed
            return None


class Custom(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Custom"):
        super().__init__(llm, name)

    async def __call__(self, input, instruction):
        prompt = instruction + input
        response = await self._fill_node(GenerateOp, prompt, mode="single_fill")
        return response


class AnswerGenerate(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "AnswerGenerate"):
        super().__init__(llm, name)

    async def __call__(self, input: str) -> Tuple[str, str]:
        prompt = ANSWER_GENERATION_PROMPT.format(input=input)
        response = await self._fill_node(AnswerGenerateOp, prompt, mode="xml_fill")
        return response


class CustomCodeGenerate(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "CustomCodeGenerate"):
        super().__init__(llm, name)

    async def __call__(self, problem, entry_point, instruction):
        prompt = instruction + problem
        response = await self._fill_node(GenerateOp, prompt, mode="code_fill", function_name=entry_point)
        return response


class ScEnsemble(Operator):
    """
    Paper: Self-Consistency Improves Chain of Thought Reasoning in Language Models
    Link: https://arxiv.org/abs/2203.11171
    Paper: Universal Self-Consistency for Large Language Model Generation
    Link: https://arxiv.org/abs/2311.17311
    """

    def __init__(self, llm: AsyncLLM, name: str = "ScEnsemble"):
        super().__init__(llm, name)

    async def __call__(self, solutions: List[str], problem: str):
        # 过滤掉空/错误解法
        valid_solutions = [s for s in solutions if s and s.strip()]
        if not valid_solutions:
            return {"status": "error", "response": "", "observation": "ScEnsemble: all solutions are empty or errored"}
        if len(valid_solutions) == 1:
            return {"status": "success", "response": valid_solutions[0], "observation": ""}

        answer_mapping = {}
        solution_text = ""
        for index, solution in enumerate(valid_solutions):
            answer_mapping[chr(65 + index)] = index
            solution_text += f"{chr(65 + index)}: \n{str(solution)}\n\n\n"

        prompt = SC_ENSEMBLE_PROMPT.format(question=problem, solutions=solution_text)
        response = await self._fill_node(ScEnsembleOp, prompt, mode="xml_fill")

        answer = response.get("solution_letter", "")
        answer = answer.strip().upper()

        # 安全防护：LLM 可能返回无效字母
        if answer not in answer_mapping:
            logger.warning(f"ScEnsemble: LLM returned invalid letter '{answer}', falling back to first solution")
            return {"status": "success", "response": valid_solutions[0], "observation": f"Invalid ensemble letter: {answer}"}

        return {"status": "success", "response": valid_solutions[answer_mapping[answer]], "observation": ""}


def run_code(code):
    try:
        # Create a new global namespace
        global_namespace = {}

        disallowed_imports = [
            "os",
            "sys",
            "subprocess",
            "multiprocessing",
            "matplotlib",
            "seaborn",
            "plotly",
            "bokeh",
            "ggplot",
            "pylab",
            "tkinter",
            "PyQt5",
            "wx",
            "pyglet",
        ]

        # Check for prohibited imports
        for lib in disallowed_imports:
            if f"import {lib}" in code or f"from {lib}" in code:
                logger.info("Detected prohibited import: %s", lib)
                return "Error", f"Prohibited import: {lib} and graphing functionalities"

        # Use exec to execute the code
        exec(code, global_namespace)
        # Assume the code defines a function named 'solve'
        if "solve" in global_namespace and callable(global_namespace["solve"]):
            result = global_namespace["solve"]()
            return "Success", str(result)
        else:
            return "Error", "Function 'solve' not found"
    except Exception as e:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        tb_str = traceback.format_exception(exc_type, exc_value, exc_traceback)
        return "Error", f"Execution error: {str(e)}\n{''.join(tb_str)}"


class Programmer(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Programmer"):
        super().__init__(llm, name)
        # Create a class-level process pool, instead of creating a new one for each execution
        self.process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=1)

    def __del__(self):
        """Ensure the process pool is closed when the object is destroyed"""
        if hasattr(self, 'process_pool'):
            self.process_pool.shutdown(wait=True)

    async def exec_code(self, code, timeout=30):
        """
        Asynchronously execute code and return an error if timeout occurs.
        """
        loop = asyncio.get_running_loop()

        try:
            # Use the class-level process pool
            future = loop.run_in_executor(self.process_pool, run_code, code)
            # Wait for the task to complete or timeout
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            # Only cancel this specific future, not the entire process pool
            future.cancel()
            # Force garbage collection
            import gc
            gc.collect()
            return "Error", "Code execution timed out"
        except concurrent.futures.process.BrokenProcessPool:
            # If the process pool is broken, recreate it
            self.process_pool.shutdown(wait=False)
            self.process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=1)
            return "Error", "Process pool broken, try again"
        except Exception as e:
            return "Error", f"Unknown error: {str(e)}"

    async def code_generate(self, problem, analysis, feedback, mode):
        """
        Asynchronous method to generate code.
        """
        prompt = PYTHON_CODE_VERIFIER_PROMPT.format(
            problem=problem,
            analysis=analysis,
            feedback=feedback
        )
        response = await self._fill_node(CodeGenerateOp, prompt, mode, function_name="solve")
        return response

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    async def __call__(self, problem: str, analysis: str = "None"):
        """
        Call method, generate code and execute, retry up to 3 times.
        """
        code = None
        output = None
        feedback = ""
        for i in range(3):
            code_response = await self.code_generate(problem, analysis, feedback, mode="code_fill")
            code = code_response.get("code")
            if not code:
                return {"status": "error", "code": code, "output": "No code generated", "observation": "Programmer: LLM did not generate code"}
            status, output = await self.exec_code(code)
            if status == "Success":
                return {"status": "success", "code": code, "output": output, "observation": ""}
            else:
                print(f"Execution error on attempt {i + 1}, error message: {output}")
                feedback = (
                    f"\nThe result of the error from the code you wrote in the previous round:\n"
                    f"Code: {code}\n\nStatus: {status}, {output}"
                )

            # Force garbage collection after each iteration
            import gc
            gc.collect()

        return {"status": "error", "code": code, "output": output, "observation": f"Programmer: code execution failed after 3 attempts: {output}"}

class Test(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Test"):
        super().__init__(llm, name)

    def exec_code(self, solution, entry_point):
        test_cases = extract_test_cases_from_jsonl(entry_point)

        fail_cases = []
        for test_case in test_cases:
            test_code = test_case_2_test_function(solution, test_case, entry_point)
            try:
                exec(test_code, globals())
            except AssertionError as e:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                tb_str = traceback.format_exception(exc_type, exc_value, exc_traceback)
                with open("tester.txt", "a") as f:
                    f.write("test_error of " + entry_point + "\n")
                error_infomation = {
                    "test_fail_case": {
                        "test_case": test_case,
                        "error_type": "AssertionError",
                        "error_message": str(e),
                        "traceback": tb_str,
                    }
                }
                fail_cases.append(error_infomation)
            except Exception as e:
                with open("tester.txt", "a") as f:
                    f.write(entry_point + " " + str(e) + "\n")
                return {"exec_fail_case": str(e)}
        if fail_cases != []:
            return fail_cases
        else:
            return "no error"

    async def __call__(self, problem, solution, entry_point, test_loop: int = 3):
        """
        "Test": {
        "description": "Test the solution with test cases, if the solution is correct, return 'no error'; if incorrect, reflect on the solution and the error information",
        "interface": "test(problem: str, solution: str, entry_point: str) -> str"
        }
        """
        for _ in range(test_loop):
            result = self.exec_code(solution, entry_point)
            if result == "no error":
                return {"result": True, "solution": solution}
            elif "exec_fail_case" in result:
                result = result["exec_fail_case"]
                prompt = REFLECTION_ON_PUBLIC_TEST_PROMPT.format(
                    problem=problem,
                    solution=solution,
                    exec_pass=f"executed unsuccessfully, error: \n {result}",
                    test_fail="executed unsucessfully",
                )
                response = await self._fill_node(ReflectionTestOp, prompt, mode="code_fill")
                solution = response["response"]
            else:
                prompt = REFLECTION_ON_PUBLIC_TEST_PROMPT.format(
                    problem=problem,
                    solution=solution,
                    exec_pass="executed successfully",
                    test_fail=result,
                )
                response = await self._fill_node(ReflectionTestOp, prompt, mode="code_fill")
                solution = response["response"]

        result = self.exec_code(solution, entry_point)
        if result == "no error":
            return {"result": True, "solution": solution}
        else:
            return {"result": False, "solution": solution}


class Format(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Format"):
        super().__init__(llm, name)

    async def __call__(self, problem, solution, mode: str = None):
        prompt = FORMAT_PROMPT.format(problem_description=problem, solution=solution)
        response = await self._fill_node(FormatOp, prompt, mode)
        return response


class Review(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Review"):
        super().__init__(llm, name)

    async def __call__(self, problem, solution, mode: str = None):
        prompt = REVIEW_PROMPT.format(problem=problem, solution=solution)
        response = await self._fill_node(ReviewOp, prompt, mode="xml_fill")
        return response


class Revise(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Revise"):
        super().__init__(llm, name)

    async def __call__(self, problem, solution, feedback, mode: str = None):
        prompt = REVISE_PROMPT.format(problem=problem, solution=solution, feedback=feedback)
        response = await self._fill_node(ReviseOp, prompt, mode="xml_fill")
        return response


class MdEnsemble(Operator):
    """
    Paper: Can Generalist Foundation Models Outcompete Special-Purpose Tuning? Case Study in Medicine
    Link: https://arxiv.org/abs/2311.16452
    """

    def __init__(self, llm: AsyncLLM, name: str = "MdEnsemble", vote_count: int = 5):
        super().__init__(llm, name)
        self.vote_count = vote_count

    @staticmethod
    def shuffle_answers(solutions: List[str]) -> Tuple[List[str], Dict[str, str]]:
        shuffled_solutions = solutions.copy()
        random.shuffle(shuffled_solutions)
        answer_mapping = {chr(65 + i): solutions.index(solution) for i, solution in enumerate(shuffled_solutions)}
        return shuffled_solutions, answer_mapping

    async def __call__(self, solutions: List[str], problem: str, mode: str = None):
        logger.info(f"solution count: {len(solutions)}")
        all_responses = []

        for _ in range(self.vote_count):
            shuffled_solutions, answer_mapping = self.shuffle_answers(solutions)

            solution_text = ""
            for index, solution in enumerate(shuffled_solutions):
                solution_text += f"{chr(65 + index)}: \n{str(solution)}\n\n\n"

            prompt = MD_ENSEMBLE_PROMPT.format(solutions=solution_text, question=problem)
            response = await self._fill_node(MdEnsembleOp, prompt, mode="xml_fill")

            answer = response.get("solution_letter", "A")
            answer = answer.strip().upper()

            if answer in answer_mapping:
                original_index = answer_mapping[answer]
                all_responses.append(original_index)

        most_frequent_index = Counter(all_responses).most_common(1)[0][0]
        final_answer = solutions[most_frequent_index]
        return {"solution": final_answer}


# ========== Auto-generated operators for GSM8K experiment ==========


class AnswerExtractor(Operator):
    """
    Intelligently extracts the final numerical answer from a model's reasoning output.
    Prioritizes \\boxed{} notation, then uses LLM-based extraction as fallback.
    Addresses the critical answer extraction failure pattern (18/26 baseline failures).
    """

    def __init__(self, llm: AsyncLLM, name: str = "AnswerExtractor"):
        super().__init__(llm, name)

    @staticmethod
    def _regex_extract(text: str):
        """Fast regex-based extraction: prioritize \\boxed{}, then last standalone number."""
        # 1. Try \boxed{...} — supports one level of nested braces (e.g., \boxed{\frac{3}{5}})
        boxed = re.findall(r'\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}', str(text))
        if boxed:
            val = boxed[-1].strip()
            # Handle LaTeX fractions: \frac{a}{b} -> a/b -> evaluate
            frac_match = re.match(r'\\frac\{(-?\d+)\}\{(-?\d+)\}', val)
            if frac_match:
                num, den = int(frac_match.group(1)), int(frac_match.group(2))
                if den != 0:
                    result = num / den
                    return str(int(result)) if result == int(result) else str(result)
            # Handle mixed numbers: "21\frac{1}{3}"
            mixed_match = re.match(r'(-?\d+)\\frac\{(-?\d+)\}\{(-?\d+)\}', val)
            if mixed_match:
                whole = int(mixed_match.group(1))
                num = int(mixed_match.group(2))
                den = int(mixed_match.group(3))
                if den != 0:
                    result = whole + num / den
                    return str(int(result)) if result == int(result) else str(result)
            # Handle \sqrt{...}
            sqrt_match = re.match(r'\\sqrt\{(\d+)\}', val)
            if sqrt_match:
                import math
                result = math.sqrt(int(sqrt_match.group(1)))
                return str(int(result)) if result == int(result) else str(result)
            val = val.replace('\\%', '').replace('%', '').strip()
            return val
        # 2. Try "The answer is X" / "Final answer: X" patterns
        patterns = [
            r'(?:the answer is|final answer[:\s]*)\s*([+-]?\d+(?:,\d{3})*(?:\.\d+)?)',
            r'(?:answer[:\s]*)\s*([+-]?\d+(?:,\d{3})*(?:\.\d+)?)',
        ]
        for p in patterns:
            m = re.findall(p, str(text), re.IGNORECASE)
            if m:
                return m[-1].replace(",", "")
        return None

    async def __call__(self, output: str):
        # First try regex extraction
        regex_result = self._regex_extract(output)
        if regex_result is not None:
            return {"answer": regex_result}

        # Fallback to LLM-based extraction
        prompt = ANSWER_EXTRACT_PROMPT.format(output=output)
        response = await self._fill_node(AnswerExtractOp, prompt, mode="xml_fill")
        answer = response.get("answer", "")
        return {"answer": answer.strip()}


class ReviewRevise(Operator):
    """
    Reviews a solution for correctness and revises it if errors are found.
    Addresses reasoning errors where the model makes calculation mistakes (8/26 baseline failures).
    Uses confidence threshold to avoid over-correction (revising correct solutions).
    """

    CONFIDENCE_THRESHOLD = 0.8

    def __init__(self, llm: AsyncLLM, name: str = "ReviewRevise"):
        super().__init__(llm, name)

    async def __call__(self, problem: str, solution: str):
        prompt = REVIEW_REVISE_PROMPT.format(problem=problem, solution=solution)
        response = await self._fill_node(ReviewReviseOp, prompt, mode="xml_fill")

        if response.get("review_result", True):
            # Solution is correct, return as-is
            return {"response": solution}
        else:
            # Solution has errors, but only revise if confidence is high enough
            confidence = response.get("confidence", 1.0)
            if confidence < self.CONFIDENCE_THRESHOLD:
                # Low confidence — don't revise, keep original to avoid over-correction
                return {"response": solution}
            revised = response.get("revised_solution", "")
            if revised:
                return {"response": revised}
            return {"response": solution}


class MultiPathReasoner(Operator):
    """
    Generates multiple independent solutions using different reasoning strategies,
    then uses majority voting to select the most consistent answer.
    Addresses both extraction and reasoning failures through redundancy.
    """

    def __init__(self, llm: AsyncLLM, name: str = "MultiPathReasoner", num_paths: int = 3):
        super().__init__(llm, name)
        self.num_paths = num_paths
        self.custom = Custom(llm, "Custom")
        self.programmer = Programmer(llm, "Programmer")
        self.sc_ensemble = ScEnsemble(llm, "ScEnsemble")

    async def __call__(self, problem: str, num_paths: int = None):
        n = num_paths or self.num_paths
        solutions = []

        # Path 1: Step-by-step reasoning
        sol1 = await self.custom(
            input=problem,
            instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}."
        )
        solutions.append(sol1['response'])

        # Path 2: Code-based reasoning
        code_result = await self.programmer(problem=problem, analysis="Solve using Python code")
        if code_result.get('output') and code_result['output'] != 'No code generated':
            solutions.append(f"The answer computed by code is {code_result['output']}")
        else:
            # Fallback: another text path
            sol2 = await self.custom(
                input=problem,
                instruction="Solve this problem. Think carefully and put the final numerical answer in \\boxed{}."
            )
            solutions.append(sol2['response'])

        # Path 3: Another reasoning approach
        if n >= 3:
            sol3 = await self.custom(
                input=problem,
                instruction="Solve this math problem. Double-check your work. Put the final answer in \\boxed{}."
            )
            solutions.append(sol3['response'])

        # Use ScEnsemble to vote
        if len(solutions) > 1:
            result = await self.sc_ensemble(solutions=solutions, problem=problem)
            return result
        else:
            return {"response": solutions[0]}


class CodeVerifyRescue(Operator):
    """
    Takes a text-based reasoning solution and uses it as analysis to generate
    Python code that computes the answer. If code execution succeeds, uses the code output.
    This serves as a 'rescue' operator when text-based reasoning might have calculation errors.
    """

    def __init__(self, llm: AsyncLLM, name: str = "CodeVerifyRescue"):
        super().__init__(llm, name)
        self.programmer = Programmer(llm, "Programmer")

    async def __call__(self, problem: str, solution: str):
        # Use the existing solution as analysis for code generation
        code_result = await self.programmer(
            problem=problem,
            analysis=f"Here is a previous attempt at solving this problem (may contain errors): {solution}"
        )
        return code_result


# ========== MATH-specific operators ==========


class AnswerFormatter(Operator):
    """
    Extracts the final answer from a solution and reformats it with proper
    LaTeX notation, ensuring correct \\boxed{} wrapping. Addresses the format
    mismatch failure pattern in MATH (missing units, degree symbols, LaTeX issues).
    """

    def __init__(self, llm: AsyncLLM, name: str = "AnswerFormatter"):
        super().__init__(llm, name)

    async def __call__(self, problem: str, solution: str):
        prompt = ANSWER_FORMAT_PROMPT.format(problem=problem, solution=solution)
        response = await self._fill_node(AnswerFormatOp, prompt, mode="xml_fill")

        formatted = response.get("formatted_answer", "")
        if formatted:
            # Ensure the formatted answer is wrapped in \boxed{} if not already
            if "\\boxed" not in formatted:
                formatted = f"\\boxed{{{formatted}}}"
            return {"status": "success", "response": formatted, "observation": ""}
        # Fallback: return original solution
        return {"status": "success", "response": solution, "observation": "AnswerFormatter: LLM returned empty formatted_answer"}


class MultiStrategySolver(Operator):
    """
    Generates solutions using multiple distinct strategies (algebraic, code-based,
    step-by-step verification), then selects the most reliable one.
    Addresses reasoning errors by combining diverse solution approaches.
    """

    def __init__(self, llm: AsyncLLM, name: str = "MultiStrategySolver"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")
        self.programmer = Programmer(llm, "Programmer")

    async def __call__(self, problem: str):
        solutions = []

        # Strategy 1: Algebraic/step-by-step reasoning
        sol1 = await self.custom(
            input=problem,
            instruction=(
                "Solve this problem step by step using algebraic reasoning. "
                "Show all work clearly. Put the final answer in \\boxed{}."
            ),
        )
        if sol1.get("status") != "error" and sol1.get("response"):
            solutions.append(sol1["response"])

        # Strategy 2: Code-based computation
        code_result = await self.programmer(
            problem=problem, analysis="Solve using Python code for precise computation"
        )
        if code_result.get("status") == "success" and code_result.get("output"):
            code_sol = f"The answer computed by Python code is {code_result['output']}. \\boxed{{{code_result['output']}}}"
            solutions.append(code_sol)
        else:
            # Fallback: another reasoning path with different framing
            sol2 = await self.custom(
                input=problem,
                instruction=(
                    "Solve this problem using a different approach than direct algebra. "
                    "Try working backwards from the answer or using substitution. "
                    "Put the final answer in \\boxed{}."
                ),
            )
            if sol2.get("status") != "error" and sol2.get("response"):
                solutions.append(sol2["response"])

        # Strategy 3: Verification-oriented reasoning
        sol3 = await self.custom(
            input=problem,
            instruction=(
                "Solve this math problem carefully. After finding your answer, "
                "verify it by substituting back into the original conditions. "
                "Put the verified final answer in \\boxed{}."
            ),
        )
        if sol3.get("status") != "error" and sol3.get("response"):
            solutions.append(sol3["response"])

        if not solutions:
            return {"status": "error", "response": "", "observation": "MultiStrategySolver: all strategies failed"}

        # Select the best solution using LLM-based strategy comparison
        solution_text = ""
        for index, sol in enumerate(solutions):
            solution_text += f"Strategy {chr(65 + index)}:\n{sol}\n\n"

        prompt = STRATEGY_SELECT_PROMPT.format(problem=problem, solutions=solution_text)
        response = await self._fill_node(StrategySelectOp, prompt, mode="xml_fill")

        best = response.get("best_solution", "")
        if best:
            return {"status": "success", "response": best, "observation": ""}

        # Fallback: use ScEnsemble voting
        if len(solutions) > 1:
            result = await ScEnsemble(self.llm)(solutions=solutions, problem=problem)
            return result
        return {"status": "success", "response": solutions[0], "observation": ""}


class SymbolicVerifier(Operator):
    """
    Verifies a proposed answer against the problem's constraints using
    symbolic reasoning. If the answer is inconsistent, provides a corrected answer.
    Addresses complex problem failures where the model's reasoning direction is wrong.
    """

    def __init__(self, llm: AsyncLLM, name: str = "SymbolicVerifier"):
        super().__init__(llm, name)

    @staticmethod
    def _extract_boxed_answer(text: str) -> str:
        """Extract the answer from \\boxed{...} notation."""
        matches = re.findall(r"\\boxed\{((?:[^{}]|{[^{}]*})*)\}", str(text))
        if matches:
            return matches[-1].strip()
        return text.strip()

    async def __call__(self, problem: str, solution: str):
        # Extract the proposed answer for verification
        proposed_answer = self._extract_boxed_answer(solution)

        prompt = SYMBOLIC_VERIFY_PROMPT.format(
            problem=problem, answer=proposed_answer
        )
        response = await self._fill_node(SymbolicVerifyOp, prompt, mode="xml_fill")

        if response.get("is_consistent", True):
            # Answer is consistent, return original solution
            return {"status": "success", "response": solution, "observation": ""}
        else:
            # Answer is inconsistent, use corrected answer
            corrected = response.get("corrected_answer", "")
            if corrected:
                if "\\boxed" not in corrected:
                    corrected = f"\\boxed{{{corrected}}}"
                return {
                    "status": "success",
                    "response": f"{solution}\n\nVerification found inconsistency. Corrected answer: {corrected}",
                    "observation": f"SymbolicVerifier: inconsistency detected, corrected answer provided",
                }
            # No correction provided, return original
            return {"status": "success", "response": solution, "observation": "SymbolicVerifier: inconsistency detected but no correction provided"}
