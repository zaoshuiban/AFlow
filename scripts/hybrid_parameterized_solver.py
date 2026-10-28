"""Known-mechanism reusable tools with per-problem Programmer fallback."""

from __future__ import annotations

import importlib
from typing import Any, Dict, Optional

from scripts.parameterized_tools import ParameterizedToolRegistry


UNKNOWN_MECHANISM_PROGRAMMER_PROMPT = (
    "No validated reusable parameterized tool matched this problem. Generate a "
    "self-contained Python solution for this one problem. Define zero-argument "
    "solve() and return only the final answer. Do not read files, access a network, "
    "or start processes."
)


class HybridParameterizedSolver:
    def __init__(
        self,
        llm: Any,
        operator_module: str,
        *,
        registry: Optional[ParameterizedToolRegistry] = None,
        programmer: Any = None,
    ):
        self.registry = registry or ParameterizedToolRegistry()
        if programmer is None:
            module = importlib.import_module(operator_module)
            programmer = getattr(module, "Programmer")(llm, "UnknownMechanismProgrammer")
        self.programmer = programmer

    async def __call__(self, problem: str) -> Dict[str, Any]:
        reusable = self.registry.solve(problem)
        if reusable.status == "success":
            return {
                "status": "success",
                "response": reusable.response,
                "observation": "",
                "tool_trace": {
                    "route": "reusable_parameterized_tool",
                    **reusable.to_dict(),
                },
            }
        if reusable.status not in {"no_match", "refused"}:
            return {
                "status": "error",
                "response": "",
                "observation": reusable.observation,
                "tool_trace": {
                    "route": "known_tool_error_no_runtime_regeneration",
                    **reusable.to_dict(),
                },
            }

        generated = await self.programmer(
            problem=problem,
            analysis=UNKNOWN_MECHANISM_PROGRAMMER_PROMPT,
        )
        code = str(generated.get("code", "") or "")
        output = str(generated.get("output", "") or "").strip()
        trace = {
            "route": "single_problem_programmer_fallback",
            "parameterized_attempt": reusable.to_dict(),
            "code": code,
            "output": output,
        }
        if (
            not output
            or output == "No code generated"
            or output.startswith(
                (
                    "Execution error",
                    "Code execution timed out",
                    "Unknown error",
                    "Prohibited import",
                )
            )
        ):
            return {
                "status": "error",
                "response": "",
                "observation": output or "programmer_empty_output",
                "tool_trace": trace,
            }
        return {
            "status": "success",
            "response": f"Single-problem Programmer result: {output}\n\\boxed{{{output}}}",
            "observation": "",
            "tool_trace": trace,
        }
