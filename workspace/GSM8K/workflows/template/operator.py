import concurrent
import re
import sys
import traceback
from typing import List, Optional

from tenacity import retry, stop_after_attempt, wait_fixed

from scripts.formatter import BaseFormatter, FormatError, XmlFormatter, CodeFormatter, TextFormatter
from workspace.GSM8K.workflows.template.operator_an import *
from workspace.GSM8K.workflows.template.op_prompt import *
from scripts.async_llm import AsyncLLM
from scripts.logs import logger
import asyncio

from scripts.operators import Operator


class Custom(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "Custom"):
        super().__init__(llm, name)

    async def __call__(self, input, instruction):
        prompt = instruction + input
        response = await self._fill_node(GenerateOp, prompt, mode="single_fill")
        return response

def run_code(code):
    try:
        global_namespace = {}
        disallowed_imports = [
            "os", "sys", "subprocess", "multiprocessing",
            "matplotlib", "seaborn", "plotly", "bokeh", "ggplot",
            "pylab", "tkinter", "PyQt5", "wx", "pyglet"
        ]
        for lib in disallowed_imports:
            if f"import {lib}" in code or f"from {lib}" in code:
                logger.info("Detected prohibited import: %s", lib)
                return "Error", f"Prohibited import: {lib} and graphing functionalities"
        exec(code, global_namespace)
        if 'solve' in global_namespace and callable(global_namespace['solve']):
            result = global_namespace['solve']()
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

    async def exec_code(self, code, timeout=30):
        loop = asyncio.get_running_loop()
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
            try:
                future = loop.run_in_executor(executor, run_code, code)
                result = await asyncio.wait_for(future, timeout=timeout)
                return result
            except asyncio.TimeoutError:
                executor.shutdown(wait=False, cancel_futures=True)
                return "Error", "Code execution timed out"
            except Exception as e:
                return "Error", f"Unknown error: {str(e)}"

    async def code_generate(self, problem, analysis, feedback, mode):
        prompt = PYTHON_CODE_VERIFIER_PROMPT.format(
            problem=problem, analysis=analysis, feedback=feedback
        )
        response = await self._fill_node(CodeGenerateOp, prompt, mode, function_name="solve")
        return response

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    async def __call__(self, problem: str, analysis: str = "None"):
        code = None
        output = None
        feedback = ""
        for i in range(3):
            code_response = await self.code_generate(problem, analysis, feedback, mode="code_fill")
            code = code_response.get("code")
            if not code:
                return {"code": code, "output": "No code generated"}
            status, output = await self.exec_code(code)
            if status == "Success":
                return {"code": code, "output": output}
            else:
                print(f"Execution error on attempt {i + 1}, error message: {output}")
                feedback = (
                    f"\nThe result of the error from the code you wrote in the previous round:\n"
                    f"Code: {code}\n\nStatus: {status}, {output}"
                )
        return {"code": code, "output": output}


class ScEnsemble(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "ScEnsemble"):
        super().__init__(llm, name)

    async def __call__(self, solutions: List[str], problem: str):
        answer_mapping = {}
        solution_text = ""
        for index, solution in enumerate(solutions):
            answer_mapping[chr(65 + index)] = index
            solution_text += f"{chr(65 + index)}: \n{str(solution)}\n\n\n"
        prompt = SC_ENSEMBLE_PROMPT.format(problem=problem, solutions=solution_text)
        response = await self._fill_node(ScEnsembleOp, prompt, mode="xml_fill")
        answer = response.get("solution_letter", "").strip().upper()
        return {"response": solutions[answer_mapping[answer]]}


class AnswerExtractor(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "AnswerExtractor"):
        super().__init__(llm, name)

    @staticmethod
    def _regex_extract(text: str):
        boxed = re.findall(r'\\boxed\{([^}]+)\}', str(text))
        if boxed:
            val = boxed[-1].strip()
            frac_match = re.match(r'\\frac\{(-?\d+)\}\{(-?\d+)\}', val)
            if frac_match:
                num, den = int(frac_match.group(1)), int(frac_match.group(2))
                if den != 0:
                    result = num / den
                    return str(int(result)) if result == int(result) else str(result)
            mixed_match = re.match(r'(-?\d+)\\frac\{(-?\d+)\}\{(-?\d+)\}', val)
            if mixed_match:
                whole = int(mixed_match.group(1))
                num = int(mixed_match.group(2))
                den = int(mixed_match.group(3))
                if den != 0:
                    result = whole + num / den
                    return str(int(result)) if result == int(result) else str(result)
            val = val.replace('\\%', '').replace('%', '').strip()
            return val
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
        regex_result = self._regex_extract(output)
        if regex_result is not None:
            return {"answer": regex_result}
        prompt = ANSWER_EXTRACT_PROMPT.format(output=output)
        response = await self._fill_node(AnswerExtractOp, prompt, mode="xml_fill")
        answer = response.get("answer", "")
        return {"answer": answer.strip()}


class ReviewRevise(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "ReviewRevise"):
        super().__init__(llm, name)

    async def __call__(self, problem: str, solution: str):
        prompt = REVIEW_REVISE_PROMPT.format(problem=problem, solution=solution)
        response = await self._fill_node(ReviewReviseOp, prompt, mode="xml_fill")
        if response.get("review_result", True):
            return {"response": solution}
        else:
            revised = response.get("revised_solution", "")
            if revised:
                return {"response": revised}
            return {"response": solution}


class MultiPathReasoner(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "MultiPathReasoner", num_paths: int = 3):
        super().__init__(llm, name)
        self.num_paths = num_paths
        self.custom = Custom(llm, "Custom")
        self.programmer = Programmer(llm, "Programmer")
        self.sc_ensemble = ScEnsemble(llm, "ScEnsemble")

    async def __call__(self, problem: str, num_paths: int = None):
        n = num_paths or self.num_paths
        solutions = []
        sol1 = await self.custom(input=problem,
            instruction="Solve this step by step. Show your work clearly and put the final answer in \\boxed{}.")
        solutions.append(sol1['response'])
        code_result = await self.programmer(problem=problem, analysis="Solve using Python code")
        if code_result.get('output') and code_result['output'] != 'No code generated':
            solutions.append(f"The answer computed by code is {code_result['output']}")
        else:
            sol2 = await self.custom(input=problem,
                instruction="Solve this problem. Think carefully and put the final numerical answer in \\boxed{}.")
            solutions.append(sol2['response'])
        if n >= 3:
            sol3 = await self.custom(input=problem,
                instruction="Solve this math problem. Double-check your work. Put the final answer in \\boxed{}.")
            solutions.append(sol3['response'])
        if len(solutions) > 1:
            result = await self.sc_ensemble(solutions=solutions, problem=problem)
            return result
        else:
            return {"response": solutions[0]}


class CodeVerifyRescue(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "CodeVerifyRescue"):
        super().__init__(llm, name)
        self.programmer = Programmer(llm, "Programmer")

    async def __call__(self, problem: str, solution: str):
        code_result = await self.programmer(problem=problem,
            analysis=f"Here is a previous attempt at solving this problem (may contain errors): {solution}")
        return code_result
