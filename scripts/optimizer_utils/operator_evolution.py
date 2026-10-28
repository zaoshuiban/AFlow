# -*- coding: utf-8 -*-
"""
算子自动进化模块（骨架+填空模式）

核心理念：
- LLM 只需要填空（prompt 文本 + 可选路由条件），不需要写代码
- 系统从 5 种骨架模板自动生成健壮的算子类代码
- 把复杂的代码生成任务降维成"填空题"，大幅降低冒烟拒绝率

骨架类型：
1. single_step    — 单步特化，一个 prompt 搞定
2. conditional_route — 条件路由，根据题型走不同 prompt
3. two_stage      — 两阶段，先分析再求解
4. multi_strategy — 多策略求解 + 可选代码执行 + ensemble 投票
5. verify_fix     — 先求解再验证修正
6. programmer_step — 用专项指令生成并执行 Python，保存完整工具轨迹
"""

import ast
import asyncio
import importlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, Field

from scripts.async_llm import AsyncLLM, create_llm_instance
from scripts.formatter import XmlFormatter, FormatError
from scripts.logs import logger
from scripts.operator_library import KNOWN_FEATURES, OperatorLibrary, parse_feature_list


class FailureDiagnosis(BaseModel):
    """Failure analysis produced before any operator is designed."""

    dominant_error_type: str = Field(default="", description="Dominant stable error type")
    root_cause: str = Field(default="", description="Mechanistic root cause, not a symptom")
    missing_capability: str = Field(default="", description="Capability absent from the baseline")
    evidence: str = Field(default="", description="Which supplied cases support this diagnosis")
    applicable_when: str = Field(default="", description="Narrow conditions where a specialist should run")
    not_applicable_when: str = Field(default="", description="Conditions where it should refuse")
    success_criteria: str = Field(default="", description="Observable behavior that would demonstrate improvement")


class OperatorDesignSpec(BaseModel):
    """Code-free operator architecture consumed by the deterministic compiler."""

    operator_name: str = Field(
        default="",
        description="Name of the new operator class in PascalCase"
    )
    description: str = Field(
        default="",
        description="One-sentence description of what this operator does"
    )
    reasoning: str = Field(
        default="",
        description="Why this skeleton type and prompt strategy addresses the bottleneck"
    )
    skeleton_type: str = Field(
        default="single_step",
        description=(
            "Skeleton pattern: single_step, conditional_route, two_stage, "
            "multi_strategy, verify_fix, programmer_step"
        )
    )
    prompt_1: str = Field(
        default="",
        description="Main prompt instruction text. The problem will be appended automatically after this instruction. "
                    "Do NOT include {problem} placeholders."
    )
    prompt_1_name: str = Field(
        default="",
        description="UPPER_SNAKE_CASE constant name for prompt_1, must end with _PROMPT"
    )
    prompt_2: str = Field(
        default="",
        description="Second prompt instruction text (required for conditional_route, two_stage, verify_fix). "
                    "The input/context will be appended automatically."
    )
    prompt_2_name: str = Field(
        default="",
        description="UPPER_SNAKE_CASE constant name for prompt_2, ending with _PROMPT. Required when prompt_2 is provided."
    )
    routing_condition: str = Field(
        default="",
        description="Python boolean expression using `problem` variable (required for conditional_route, multi_strategy). "
                    "E.g.: 'any(kw in problem.lower() for kw in [\"triangle\", \"circle\", \"angle\"])'"
    )
    applicability_features: str = Field(
        default="",
        description="Comma-separated declarative features required before this operator may be routed",
    )
    failure_boundary_features: str = Field(
        default="",
        description="Comma-separated declarative features that must block this operator",
    )
    mechanism: str = Field(
        default="",
        description="The causal intervention that differs from the generic baseline",
    )
    expected_advantage: str = Field(
        default="",
        description="What failures this design should uniquely fix",
    )
    refusal_policy: str = Field(
        default="",
        description="When the generated specialist must decline and use fallback",
    )


class OperatorEvolveResponse(OperatorDesignSpec):
    """Backward-compatible name for one validated design specification."""


# ============================================================
# 算子进化 prompt
# ============================================================

OPERATOR_EVOLUTION_BASE = """You are designing a new workflow operator for an LLM-based math problem-solving system.

An operator combines a **specialized prompt strategy** with a **runtime skeleton**. You choose the skeleton type and fill in prompts — the system generates all code automatically.

## Available Skeleton Types

### 1. single_step (default, most common)
Sends one specialized prompt to the LLM via Custom. Best when a single reasoning strategy suffices.
**When to use**: You have ONE distinct solving approach that works for the target problem type.
**You provide**: prompt_1

### 2. conditional_route
Routes to different prompts based on a condition on the problem text. Best when different categories need fundamentally different strategies.
**When to use**: The bottleneck affects multiple problem types, and each needs its own approach.
**You provide**: prompt_1, prompt_2, routing_condition

### 3. two_stage
First analyzes/decomposes, then solves with analysis as context. Best when upfront planning improves solution quality.
**When to use**: Problems fail because the model jumps into calculation without understanding structure.
**You provide**: prompt_1 (analysis), prompt_2 (solve)

### 4. multi_strategy
Text reasoning with a specialized prompt, then optionally adds a Programmer result as a second candidate for ScEnsemble voting. Unlike the built-in MultiStrategySolver (which has hardcoded strategies), this skeleton lets you design the strategy via prompt_1 and choose when to trigger code via routing_condition. Best where precise computation helps.
**When to use**: Problems involve heavy computation where code execution can cross-check text answers, and you want to customize the reasoning strategy.
**You provide**: prompt_1, routing_condition (when to trigger code)

### 5. verify_fix
Solves first, then verifies and fixes if needed. Best when first-pass answers are close but contain fixable errors.
**When to use**: The model gets the approach right but makes arithmetic or sign errors.
**You provide**: prompt_1 (solve), prompt_2 (verify and fix)

### 6. programmer_step
Uses prompt_1 as a specialized algorithm specification for Programmer, then runs
the generated Python and returns its exact output. The generated code and output
are preserved in the evaluation trace. Do not include Python source in prompt_1;
describe the algorithm, invariants, expected return value, and refusal boundary.
**When to use**: Exact finite computation is the missing capability and the batch
policy explicitly permits Programmer.
**You provide**: prompt_1 (algorithm specification for code generation)

## Rules for Prompt Design

1. **Change HOW the problem is solved**, not just check the answer
2. Every prompt must end with: "Put your final answer in \\boxed{{}}."
3. **Do NOT use placeholders** like {problem} or {analysis} — the system appends inputs automatically
4. Each prompt should represent a DISTINCT reasoning strategy
5. Do NOT design verification-only operators (SymbolicVerifier already exists)

## Rules for Routing Conditions (conditional_route / multi_strategy only)

- Must be a valid Python boolean expression using the variable `problem`
- Must be self-contained (no imports, no function calls beyond builtins)
- Keep it simple: string checks like `any(kw in problem.lower() for kw in [...])`

## Required Routing Metadata

Return two additional comma-separated fields. `applicability_features` lists
features that must all be present; `failure_boundary_features` lists features
where the operator must never be used. Choose only from:

counting_problem, probability_problem, arrangement_problem,
finite_object_arrangement, distinct_objects,
explicit_rotation_equivalence, explicit_reflection_equivalence,
explicit_symmetry_equivalence, finite_geometric_layout,
group_action_candidate, burnside_safe, people_seating, circular_seating,
rotation_already_quotiented, continuous_probability, geometry_problem,
algebra_problem, number_theory_problem, parametric_curve, trajectory_trace,
path_retracing_candidate, distance_traveled_request.

Prefer narrow applicability and explicit failure boundaries. These fields are
declarative metadata; do not put Python expressions in them.
"""

OPERATOR_EVOLUTION_GUIDANCE = """
---

## ❌ ANTI-PATTERNS: Do NOT generate operators like these

These were auto-generated in previous experiments and FAILED:

1. **Verification/checking operators** — SymbolicVerifier already exists. Don't create AlgebraicSanityCheck, NumericalConstraintChecker, etc.
2. **Generic retry operators** — "Try harder" is not a new strategy
3. **Format-only operators** — Reformatting answers adds ~0% value (AnswerFormatter handles this)

### ✅ What works: Change ONE of these dimensions

1. **Problem representation**: Reformulate the problem (coordinates, matrices, tree diagrams, symmetry groups)
2. **Solving method**: Use a fundamentally different approach (enumeration, working backwards, invariants, parity)
3. **Information flow**: Provide context that vanilla prompting lacks (formula lookup, similar examples, constraints)
4. **Problem decomposition**: Break into independent subproblems solved separately
"""

OPERATOR_EVOLUTION_INPUT = """
## Current Workflow Graph
```python
{graph_code}
```

## Existing Operators
{operator_descriptions}

## Bottleneck Analysis
{bottleneck_info}

## Failed Cases (sampled from recent rounds)
{failure_cases}

## Your Task
Analyze the failure patterns and design a new operator that addresses the root cause.

1. Identify the dominant error type
2. Choose the skeleton type that best fits your strategy
3. Design prompt(s) that apply a DISTINCT reasoning strategy
4. If using conditional_route or multi_strategy, design the routing condition
"""

FAILURE_DIAGNOSIS_PROMPT = """You are the diagnostic stage of an operator-evolution system.
You are NOT allowed to design an operator, write a prompt, or write code yet.

Given the workflow, bottleneck report, and sampled optimization-set failures below,
identify one persistent capability gap. Separate root causes from symptoms and state
narrow applicability and refusal boundaries. Do not memorize answers or case-specific
numbers. Every claim must be supported by at least two supplied cases when possible.

## Workflow
{graph_code}

## Bottleneck
{bottleneck_info}

## Failures
{failure_cases}
"""

FAILURE_DIAGNOSIS_SYSTEM = """Perform only causal failure diagnosis. Do not propose
an operator, prompt, workflow, skeleton, implementation, or code. Return the
requested structured diagnostic fields and nothing else.\n"""

OPERATOR_ARCHITECT_PROMPT = """You are the architecture stage of an operator-evolution system.
You receive a frozen failure diagnosis. Produce a CODE-FREE operator design using
one of the fixed skeletons described in the system prompt. You may provide prompt
text and a small declarative routing expression, but never Python class code,
imports, helper functions, or executable statements.

The implementation will be compiled deterministically from a reviewed skeleton.
The design must introduce a causal solving mechanism, not a generic request to
think harder, verify, retry, or reformat. It must declare when it refuses.

## Frozen diagnosis
{diagnosis}

## Existing operators
{operator_descriptions}

## Previously accepted designs in this batch
{prior_designs}

## Batch-scoped generation policy
{design_policy}

These constraints are mandatory. A design outside the allowed skeletons or
tool/call policy will be rejected before it can enter a tournament.

## Candidate slot
Design candidate {candidate_number} of {candidate_count}. It must use a materially
different representation, decomposition, or solving mechanism from prior designs.
"""


# ============================================================
# 骨架模板定义
# ============================================================

_SINGLE_STEP_TEMPLATE = r'''class __CLASS_NAME__(Operator):
    _required_features = __REQUIRED_FEATURES__
    _blocked_features = __BLOCKED_FEATURES__

    def __init__(self, llm: AsyncLLM, name: str = "__CLASS_NAME__"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")

__RUNTIME_GUARD__

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        result = await self.custom(input=problem, instruction=__PROMPT_1__)
        if result.get("status") == "error":
            return {"status": "error", "response": "", "observation": result.get("observation", "")}
        return self._finalize(result.get("response", ""))'''


_CONDITIONAL_ROUTE_TEMPLATE = r'''class __CLASS_NAME__(Operator):
    _required_features = __REQUIRED_FEATURES__
    _blocked_features = __BLOCKED_FEATURES__

    def __init__(self, llm: AsyncLLM, name: str = "__CLASS_NAME__"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")

__RUNTIME_GUARD__

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        if __CONDITION__:
            result = await self.custom(input=problem, instruction=__PROMPT_1__)
        else:
            result = await self.custom(input=problem, instruction=__PROMPT_2__)
        if result.get("status") == "error":
            return {"status": "error", "response": "", "observation": result.get("observation", "")}
        return self._finalize(result.get("response", ""))'''


_TWO_STAGE_TEMPLATE = r'''class __CLASS_NAME__(Operator):
    _required_features = __REQUIRED_FEATURES__
    _blocked_features = __BLOCKED_FEATURES__

    def __init__(self, llm: AsyncLLM, name: str = "__CLASS_NAME__"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")

__RUNTIME_GUARD__

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        # Stage 1: Analyze
        analysis = await self.custom(input=problem, instruction=__PROMPT_1__)
        if analysis.get("status") == "error":
            return {"status": "error", "response": "", "observation": f"Stage 1 failed: {analysis.get('observation', '')}"}
        # Stage 2: Solve with analysis context
        combined_input = f"Problem: {problem}\n\nAnalysis:\n{analysis['response']}"
        result = await self.custom(input=combined_input, instruction=__PROMPT_2__)
        if result.get("status") == "error":
            return {"status": "error", "response": "", "observation": f"Stage 2 failed: {result.get('observation', '')}"}
        return self._finalize(result.get("response", ""))'''


_MULTI_STRATEGY_TEMPLATE = r'''class __CLASS_NAME__(Operator):
    _required_features = __REQUIRED_FEATURES__
    _blocked_features = __BLOCKED_FEATURES__

    def __init__(self, llm: AsyncLLM, name: str = "__CLASS_NAME__"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")
        self.programmer = Programmer(llm, "Programmer")

__RUNTIME_GUARD__

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        # Always try text reasoning with specialized strategy
        text_result = await self.custom(input=problem, instruction=__PROMPT_1__)
        solutions = []
        observations = []
        if text_result.get("status") != "error" and text_result.get("response"):
            solutions.append(text_result['response'])
        else:
            observations.append(f"Text strategy failed: {text_result.get('observation', '')}")
        # Optionally try code execution
        if __CONDITION__:
            code_result = await self.programmer(problem=problem, analysis="Solve using Python code for precise computation")
            if code_result.get("status") == "success" and code_result.get("output"):
                code_sol = f"The answer computed by Python code is {code_result['output']}. \\boxed{{{code_result['output']}}}"
                solutions.append(code_sol)
            else:
                observations.append(f"Code strategy failed: {code_result.get('observation', '')}")
        if not solutions:
            return {"status": "error", "response": "", "observation": "; ".join(observations)}
        if len(solutions) == 1:
            return self._finalize(solutions[0])
        ensemble = ScEnsemble(self.llm)
        result = await ensemble(solutions=solutions, problem=problem)
        return self._finalize(result.get("response", ""))'''


_VERIFY_FIX_TEMPLATE = r'''class __CLASS_NAME__(Operator):
    _required_features = __REQUIRED_FEATURES__
    _blocked_features = __BLOCKED_FEATURES__

    def __init__(self, llm: AsyncLLM, name: str = "__CLASS_NAME__"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")

__RUNTIME_GUARD__

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        # Step 1: Solve
        solve_result = await self.custom(input=problem, instruction=__PROMPT_1__)
        if solve_result.get("status") == "error":
            return {"status": "error", "response": "", "observation": f"Solve step failed: {solve_result.get('observation', '')}"}
        solution = solve_result['response']
        # Step 2: Verify and fix if needed
        verify_input = f"Problem: {problem}\n\nProposed solution:\n{solution}"
        verify_result = await self.custom(input=verify_input, instruction=__PROMPT_2__)
        if verify_result.get("status") == "error":
            # Verification failed, return original solution as fallback
            return self._finalize(solution)
        return self._finalize(verify_result.get("response", ""))'''


_PROGRAMMER_STEP_TEMPLATE = r'''class __CLASS_NAME__(Operator):
    _required_features = __REQUIRED_FEATURES__
    _blocked_features = __BLOCKED_FEATURES__

    def __init__(self, llm: AsyncLLM, name: str = "__CLASS_NAME__"):
        super().__init__(llm, name)
        self.programmer = Programmer(llm, "Programmer")

__RUNTIME_GUARD__

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        code_result = await self.programmer(problem=problem, analysis=__PROMPT_1__)
        code = str(code_result.get("code", "") or "")
        output = str(code_result.get("output", "") or "").strip()
        if not output or output == "No code generated" or output.startswith((
            "Execution error", "Code execution timed out", "Unknown error", "Prohibited import"
        )):
            return {
                "status": "error",
                "response": "",
                "observation": output or "programmer_empty_output",
                "tool_trace": {"code": code, "output": output},
            }
        response = f"Python execution result: {output}\n\\boxed{{{output}}}"
        finalized = self._finalize(response)
        finalized["tool_trace"] = {"code": code, "output": output}
        return finalized'''


_SKELETON_TEMPLATES = {
    "single_step": _SINGLE_STEP_TEMPLATE,
    "conditional_route": _CONDITIONAL_ROUTE_TEMPLATE,
    "two_stage": _TWO_STAGE_TEMPLATE,
    "multi_strategy": _MULTI_STRATEGY_TEMPLATE,
    "verify_fix": _VERIFY_FIX_TEMPLATE,
    "programmer_step": _PROGRAMMER_STEP_TEMPLATE,
}


_RUNTIME_GUARD_METHODS = r'''    def _preflight(self, problem: str):
        from scripts.operator_library import ProblemFeatureExtractor
        context = ProblemFeatureExtractor().extract(problem)
        missing = sorted(set(self._required_features) - set(context.features))
        blocked = sorted(set(self._blocked_features) & set(context.features))
        if missing or blocked:
            reasons = []
            if missing:
                reasons.append("missing=" + ",".join(missing))
            if blocked:
                reasons.append("blocked=" + ",".join(blocked))
            return {
                "status": "declined",
                "response": "",
                "observation": "specialist_not_applicable:" + ";".join(reasons),
            }
        return None

    @staticmethod
    def _finalize(response: str):
        response = str(response or "").strip()
        if not response:
            return {
                "status": "declined",
                "response": "",
                "observation": "specialist_postcondition:empty_response",
            }
        if "\\boxed" not in response:
            return {
                "status": "declined",
                "response": "",
                "observation": "specialist_postcondition:missing_boxed_answer",
            }
        return {"status": "success", "response": response, "observation": ""}'''


# ============================================================
# OperatorEvolver 主类
# ============================================================

class OperatorEvolver:
    """算子自动进化器（骨架+填空模式）"""

    def __init__(
        self,
        root_path: str,
        optimize_llm: AsyncLLM,
        dataset: str,
        max_evolution_attempts: int = 3,
        enable_prompt_guidance: bool = True,
    ):
        self.root_path = root_path
        self.optimize_llm = optimize_llm
        self.dataset = dataset
        self.max_evolution_attempts = max_evolution_attempts
        self.evolve_system_prompt = self._build_system_prompt(enable_prompt_guidance)
        self.evolution_history: List[Dict] = []
        self.operator_library = OperatorLibrary(
            os.path.join(self.root_path, "operator_library", "operators.json")
        )

    @staticmethod
    def _model_dict(model: BaseModel) -> Dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump()
        return model.dict()

    async def diagnose_failures(
        self,
        *,
        bottleneck_info: str,
        failure_cases: str,
        graph_code: str,
    ) -> FailureDiagnosis:
        """Run diagnosis without allowing operator or code generation."""
        prompt = FAILURE_DIAGNOSIS_PROMPT.format(
            graph_code=graph_code,
            bottleneck_info=bottleneck_info,
            failure_cases=failure_cases,
        )
        diagnosis = await self._call_structured(
            prompt,
            FailureDiagnosis,
            system_prompt=FAILURE_DIAGNOSIS_SYSTEM,
        )
        if diagnosis is None:
            raise RuntimeError("失败诊断未返回有效结构")
        if not diagnosis.root_cause.strip() or not diagnosis.missing_capability.strip():
            raise RuntimeError("失败诊断缺少 root_cause 或 missing_capability")
        return diagnosis

    async def design_candidates(
        self,
        *,
        diagnosis: FailureDiagnosis,
        operator_descriptions: str,
        candidate_count: int = 4,
        design_policy: Optional[Dict[str, Any]] = None,
    ) -> List[OperatorEvolveResponse]:
        """Generate diverse code-free specs; no artifact is installed here."""
        if candidate_count < 2:
            raise ValueError("candidate_count 必须至少为 2，才能进行锦标赛")
        designs: List[OperatorEvolveResponse] = []
        for candidate_number in range(1, candidate_count + 1):
            prior_designs = "\n".join(
                f"- {item.operator_name}: {item.mechanism or item.reasoning}"
                for item in designs
            ) or "(none)"
            prompt = OPERATOR_ARCHITECT_PROMPT.format(
                diagnosis=json.dumps(
                    self._model_dict(diagnosis), ensure_ascii=False, indent=2
                ),
                operator_descriptions=operator_descriptions,
                prior_designs=prior_designs,
                candidate_number=candidate_number,
                candidate_count=candidate_count,
                design_policy=json.dumps(
                    design_policy or {}, ensure_ascii=False, indent=2
                ),
            )
            response = await self._call_structured(prompt, OperatorEvolveResponse)
            if response is None:
                logger.warning(f"🧬 候选 {candidate_number} 未返回有效规格")
                continue
            valid, errors = self._validate_response(response)
            policy_valid, policy_errors = self._validate_design_policy(
                response, design_policy or {}
            )
            if not policy_valid:
                valid = False
                errors = "; ".join(filter(None, (errors, policy_errors)))
            if not valid:
                logger.warning(f"🧬 候选 {candidate_number} 规格无效: {errors}")
                continue
            duplicate, reason = self._check_duplicate(
                response.operator_name,
                response.description,
                skeleton_type=response.skeleton_type,
            )
            if not duplicate:
                for prior in designs:
                    if prior.operator_name == response.operator_name:
                        duplicate, reason = True, "批次内候选同名"
                        break
            if duplicate:
                logger.warning(f"🧬 候选 {candidate_number} 重复: {reason}")
                continue
            designs.append(response)
        if len(designs) < 2:
            raise RuntimeError(
                f"只得到 {len(designs)} 个有效且不重复的候选，不足以进行锦标赛"
            )
        return designs

    async def evolve_candidates(
        self,
        *,
        bottleneck_info: str,
        failure_cases: str,
        graph_code: str,
        operator_descriptions: str,
        candidate_count: int = 4,
        batch_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Diagnose, architect, compile, and archive a pending tournament batch.

        Crucially, this method never writes candidates into the mutable workflow
        template. Installation is reserved for a tournament winner.
        """
        diagnosis = await self.diagnose_failures(
            bottleneck_info=bottleneck_info,
            failure_cases=failure_cases,
            graph_code=graph_code,
        )
        designs = await self.design_candidates(
            diagnosis=diagnosis,
            operator_descriptions=operator_descriptions,
            candidate_count=candidate_count,
            design_policy=(batch_context or {}).get("operator_generation_policy", {}),
        )
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        template_parts = Path(self.root_path, "workflows", "template").parts
        try:
            workspace_index = list(template_parts).index("workspace")
            operator_module = ".".join(template_parts[workspace_index:]) + ".operator"
        except ValueError:
            operator_module = ""

        records = []
        for design in designs:
            class_code = self._generate_class_code(design)
            prompt_code = self._generate_prompt_code(design)
            compile(class_code, f"<{design.operator_name}:compiled>", "exec")
            record = self.operator_library.register_evolved_candidate(
                name=design.operator_name,
                description=design.description,
                module=operator_module,
                skeleton_type=design.skeleton_type,
                applicability_features=design.applicability_features,
                failure_features=design.failure_boundary_features,
                provenance={
                    "kind": "staged_auto_evolution",
                    "dataset": self.dataset,
                    "batch_id": batch_id,
                    "diagnosis": self._model_dict(diagnosis),
                    "design": self._model_dict(design),
                    "compiler": "fixed_skeleton_v2",
                    "batch_context": batch_context or {},
                },
                class_code=class_code,
                prompt_code=prompt_code,
            )
            records.append(record)

        batch_dir = Path(self.root_path, "operator_evolution_batches", batch_id)
        batch_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "schema_version": 1,
            "batch_id": batch_id,
            "status": "pending_tournament",
            "dataset": self.dataset,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "diagnosis": self._model_dict(diagnosis),
            "candidate_names": [record["name"] for record in records],
            "designs": [self._model_dict(item) for item in designs],
            "batch_context": batch_context or {},
        }
        manifest_path = batch_dir / "manifest.json"
        with manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return {**manifest, "manifest_path": str(manifest_path)}

    async def evolve(
        self,
        bottleneck_info: str,
        failure_cases: str,
        graph_code: str,
        operator_descriptions: str,
    ) -> Optional[Dict]:
        """
        瓶颈触发算子进化

        Returns:
            成功: {"name": ..., "description": ..., "skeleton_type": ..., "files_written": [...]}
            失败: None
        """
        prompt = OPERATOR_EVOLUTION_INPUT.format(
            graph_code=graph_code,
            operator_descriptions=operator_descriptions,
            bottleneck_info=bottleneck_info,
            failure_cases=failure_cases,
        )

        for attempt in range(self.max_evolution_attempts):
            logger.info(f"🧬 算子进化尝试 {attempt + 1}/{self.max_evolution_attempts}")

            try:
                response = await self._call_llm(prompt)
            except Exception as e:
                logger.error(f"🧬 LLM 调用失败: {e}")
                continue

            if response is None:
                logger.warning("🧬 LLM 未返回有效响应")
                continue

            # 验证响应
            is_valid, errors = self._validate_response(response)
            if not is_valid:
                logger.warning(f"🧬 响应验证失败: {errors}")
                prompt += f"\n\n## Previous attempt failed validation:\n{errors}\nPlease fix and regenerate."
                continue

            # 去重检查
            is_duplicate, dup_reason = self._check_duplicate(
                response.operator_name, response.description
            )
            if is_duplicate:
                logger.warning(f"🧬 算子与已有算子重复: {dup_reason}")
                prompt += f"\n\n## Previous attempt was duplicate: {dup_reason}\nPlease design a fundamentally different operator."
                continue

            # Archive the immutable revision before touching the mutable
            # template.  If installation fails there is still an auditable
            # record, and no template-only orphan can be created.
            template_parts = Path(self.root_path, "workflows", "template").parts
            try:
                workspace_index = list(template_parts).index("workspace")
                operator_module = ".".join(template_parts[workspace_index:]) + ".operator"
            except ValueError:
                operator_module = ""
            try:
                self.operator_library.register_evolved_candidate(
                    name=response.operator_name,
                    description=response.description,
                    module=operator_module,
                    skeleton_type=response.skeleton_type,
                    applicability_features=response.applicability_features,
                    failure_features=response.failure_boundary_features,
                    provenance={
                        "kind": "auto_evolved",
                        "dataset": self.dataset,
                        "reasoning": response.reasoning,
                        "attempt": attempt + 1,
                    },
                    class_code=self._generate_class_code(response),
                    prompt_code=self._generate_prompt_code(response),
                )
            except Exception as exc:
                logger.error(f"🧬 算子归档失败，未修改模板: {exc}")
                prompt += (
                    f"\n\n## Previous attempt FAILED archival:\n{exc}\n"
                    "Please use a new valid operator name and regenerate."
                )
                continue

            # 安装算子
            success, install_detail, actual_class_name = self._install_operator(response)
            if not success:
                self.operator_library.set_status(
                    response.operator_name,
                    "rejected",
                    f"Mutable template installation failed: {install_detail}",
                )
                logger.error(f"🧬 算子安装失败: {install_detail}")
                prompt += (
                    f"\n\n## Previous attempt FAILED installation:\n{install_detail}\n"
                    "Please fix and regenerate."
                )
                continue

            evolution_record = {
                "name": actual_class_name,
                "description": response.description,
                "reasoning": response.reasoning,
                "skeleton_type": response.skeleton_type,
                "attempt": attempt + 1,
                "applicability_features": parse_feature_list(response.applicability_features),
                "failure_boundary_features": parse_feature_list(response.failure_boundary_features),
            }
            self.evolution_history.append(evolution_record)

            logger.info(
                f"🧬 算子进化成功: {response.operator_name} "
                f"(skeleton={response.skeleton_type}) — {response.description}"
            )
            return evolution_record

        logger.warning("🧬 算子进化: 所有尝试均失败")
        return None

    # ----------------------------------------------------------
    # LLM 调用
    # ----------------------------------------------------------

    async def _call_structured(
        self,
        prompt: str,
        response_model: Type[BaseModel],
        *,
        system_prompt: Optional[str] = None,
    ) -> Optional[BaseModel]:
        """Call one pipeline stage with an explicit output schema."""
        prefix = self.evolve_system_prompt if system_prompt is None else system_prompt
        try:
            formatter = XmlFormatter.from_model(response_model)
            response = await self.optimize_llm.call_with_format(
                prefix + prompt,
                formatter,
            )
            return response_model(**response)
        except FormatError as exc:
            logger.warning(f"🧬 {response_model.__name__} 格式错误: {exc}")
            raw = await self.optimize_llm(prefix + prompt)
            return self._parse_raw_model(raw, response_model)

    @staticmethod
    def _parse_raw_model(raw: str, response_model: Type[BaseModel]) -> Optional[BaseModel]:
        """Minimal XML fallback for the scalar fields used by pipeline stages."""
        try:
            model_fields = getattr(response_model, "model_fields", None)
            if model_fields is None:
                model_fields = response_model.__fields__
            values = {}
            for field_name in model_fields:
                match = re.search(
                    rf"<{field_name}>(.*?)</{field_name}>", raw, re.DOTALL
                )
                if match:
                    values[field_name] = match.group(1).strip()
            return response_model(**values) if values else None
        except Exception as exc:
            logger.error(f"🧬 解析 {response_model.__name__} 失败: {exc}")
            return None

    async def _call_llm(self, prompt: str) -> Optional[OperatorEvolveResponse]:
        """调用 LLM 生成算子设计"""
        try:
            formatter = XmlFormatter.from_model(OperatorEvolveResponse)
            response = await self.optimize_llm.call_with_format(
                self.evolve_system_prompt + prompt,
                formatter,
            )
            return OperatorEvolveResponse(**response)
        except FormatError as e:
            logger.warning(f"🧬 格式错误: {e}")
            raw = await self.optimize_llm(self.evolve_system_prompt + prompt)
            return self._parse_raw_response(raw)

    @staticmethod
    def _build_system_prompt(enable_prompt_guidance: bool) -> str:
        """根据是否启用 prompt guidance 构建系统 prompt"""
        base = OPERATOR_EVOLUTION_BASE
        if enable_prompt_guidance:
            return base + OPERATOR_EVOLUTION_GUIDANCE
        return base

    def _parse_raw_response(self, raw: str) -> Optional[OperatorEvolveResponse]:
        """降级解析：从原始文本中提取字段"""
        try:
            fields = {
                "operator_name": r"<operator_name>(.*?)</operator_name>",
                "description": r"<description>(.*?)</description>",
                "reasoning": r"<reasoning>(.*?)</reasoning>",
                "skeleton_type": r"<skeleton_type>(.*?)</skeleton_type>",
                "prompt_1": r"<prompt_1>(.*?)</prompt_1>",
                "prompt_1_name": r"<prompt_1_name>(.*?)</prompt_1_name>",
                "prompt_2": r"<prompt_2>(.*?)</prompt_2>",
                "prompt_2_name": r"<prompt_2_name>(.*?)</prompt_2_name>",
                "routing_condition": r"<routing_condition>(.*?)</routing_condition>",
                "applicability_features": r"<applicability_features>(.*?)</applicability_features>",
                "failure_boundary_features": r"<failure_boundary_features>(.*?)</failure_boundary_features>",
            }
            result = {}
            for field, pattern in fields.items():
                match = re.search(pattern, raw, re.DOTALL)
                result[field] = match.group(1).strip() if match else ""

            if not result.get("operator_name") or not result.get("prompt_1"):
                return None

            if not result.get("skeleton_type"):
                result["skeleton_type"] = "single_step"
            if not result.get("prompt_1_name"):
                result["prompt_1_name"] = f"{result['operator_name'].upper()}_PROMPT"

            return OperatorEvolveResponse(**result)
        except Exception as e:
            logger.error(f"🧬 解析原始响应失败: {e}")
            return None

    # ----------------------------------------------------------
    # 验证
    # ----------------------------------------------------------

    def _validate_response(self, response: OperatorEvolveResponse) -> Tuple[bool, str]:
        """验证 LLM 响应（只检查文本字段，不需要解析代码）"""
        errors = []

        # 1. 算子名
        name = response.operator_name
        if not name:
            errors.append("operator_name 为空")
        elif not re.match(r'^[A-Z][a-zA-Z0-9]*$', name):
            errors.append(f"operator_name '{name}' 不是 PascalCase 格式")

        # 2. skeleton_type
        valid_types = set(_SKELETON_TEMPLATES.keys())
        if response.skeleton_type not in valid_types:
            errors.append(f"skeleton_type '{response.skeleton_type}' 不合法，必须为: {valid_types}")

        # 3. prompt_1
        if not response.prompt_1.strip():
            errors.append("prompt_1 为空")

        # 4. prompt_1_name
        if not response.prompt_1_name.strip():
            errors.append("prompt_1_name 为空")
        elif not re.match(r'^[A-Z][A-Z0-9_]*_PROMPT$', response.prompt_1_name):
            errors.append(f"prompt_1_name '{response.prompt_1_name}' 必须 UPPER_SNAKE_CASE 并以 _PROMPT 结尾")

        # 5. 需要第二个 prompt 的骨架类型
        needs_prompt_2 = response.skeleton_type in ("conditional_route", "two_stage", "verify_fix")
        if needs_prompt_2:
            if not response.prompt_2.strip():
                errors.append(f"skeleton_type '{response.skeleton_type}' 需要 prompt_2")
            if not response.prompt_2_name.strip():
                errors.append(f"skeleton_type '{response.skeleton_type}' 需要 prompt_2_name")
            elif not re.match(r'^[A-Z][A-Z0-9_]*_PROMPT$', response.prompt_2_name):
                errors.append(f"prompt_2_name '{response.prompt_2_name}' 必须 UPPER_SNAKE_CASE 并以 _PROMPT 结尾")

        # 6. 需要路由条件的骨架类型
        needs_condition = response.skeleton_type in ("conditional_route", "multi_strategy")
        if needs_condition and not response.routing_condition.strip():
            errors.append(f"skeleton_type '{response.skeleton_type}' 需要 routing_condition")

        # 7. 验证路由条件可编译
        if response.routing_condition.strip():
            try:
                condition_tree = ast.parse(response.routing_condition, mode="eval")
                self._validate_routing_condition_ast(condition_tree)
                compile(condition_tree, "<routing_condition>", "eval")
            except SyntaxError as e:
                errors.append(f"routing_condition 语法错误: {e}")
            except ValueError as e:
                errors.append(f"routing_condition 越界: {e}")

        # 8. 声明式路由元数据必须来自闭合特征词表
        for field_name, raw_value in (
            ("applicability_features", response.applicability_features),
            ("failure_boundary_features", response.failure_boundary_features),
        ):
            raw_features = {
                item.strip().lower()
                for item in re.split(r"[,;\n]", raw_value or "")
                if item.strip()
            }
            unknown = raw_features - KNOWN_FEATURES
            if unknown:
                errors.append(
                    f"{field_name} 包含未知特征: {', '.join(sorted(unknown))}"
                )

        # 9. prompt 内不应包含 {problem} 占位符
        for p_name, p_val in [("prompt_1", response.prompt_1), ("prompt_2", response.prompt_2)]:
            if "{problem}" in p_val or "{analysis}" in p_val:
                errors.append(
                    f"{p_name} 包含占位符 {{problem}} 或 {{analysis}}，"
                    "系统会自动拼接输入，请不要手动使用占位符"
                )

        if errors:
            return False, "; ".join(errors)
        return True, ""

    @staticmethod
    def _validate_design_policy(
        response: OperatorEvolveResponse, policy: Dict[str, Any]
    ) -> Tuple[bool, str]:
        if not policy:
            return True, ""
        errors = []
        allowed = set(policy.get("allowed_skeleton_types", []))
        if allowed and response.skeleton_type not in allowed:
            errors.append(
                f"skeleton_type '{response.skeleton_type}' violates allowed set "
                f"{sorted(allowed)}"
            )
        programmer_skeletons = {"multi_strategy", "programmer_step"}
        if (
            policy.get("programmer_allowed") is False
            and response.skeleton_type in programmer_skeletons
        ):
            errors.append(
                f"{response.skeleton_type} invokes Programmer but "
                "programmer_allowed=false"
            )
        call_counts = {
            "single_step": 1,
            "conditional_route": 1,
            "two_stage": 2,
            "verify_fix": 2,
            "multi_strategy": 3,
            "programmer_step": 1,
        }
        maximum = policy.get("max_llm_calls_per_problem")
        if maximum is not None and call_counts.get(response.skeleton_type, 99) > int(maximum):
            errors.append(
                f"skeleton requires up to {call_counts.get(response.skeleton_type, 99)} "
                f"LLM calls, exceeding max {maximum}"
            )
        return (not errors), "; ".join(errors)

    @staticmethod
    def _validate_routing_condition_ast(tree: ast.AST) -> None:
        """Allow only small, side-effect-free predicates over ``problem``."""
        allowed_nodes = (
            ast.Expression,
            ast.BoolOp,
            ast.UnaryOp,
            ast.Compare,
            ast.Name,
            ast.Load,
            ast.Store,
            ast.Constant,
            ast.List,
            ast.Tuple,
            ast.GeneratorExp,
            ast.comprehension,
            ast.Call,
            ast.Attribute,
            ast.In,
            ast.NotIn,
            ast.And,
            ast.Or,
            ast.Not,
        )
        for node in ast.walk(tree):
            if not isinstance(node, allowed_nodes):
                raise ValueError(f"不允许的语法 {type(node).__name__}")
            if isinstance(node, ast.Name) and node.id not in {
                "problem", "kw", "any", "all"
            }:
                raise ValueError(f"不允许的变量 {node.id}")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id not in {"any", "all"}:
                        raise ValueError(f"不允许的函数 {node.func.id}")
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr != "lower":
                        raise ValueError(f"不允许的方法 {node.func.attr}")
                else:
                    raise ValueError("不允许动态函数调用")

    # ----------------------------------------------------------
    # 去重
    # ----------------------------------------------------------

    def _check_duplicate(
        self,
        operator_name: str,
        description: str = "",
        skeleton_type: str = "",
    ) -> Tuple[bool, str]:
        """检查是否与已有算子重复（名称精确匹配 + 描述重叠度）"""
        template_dir = os.path.join(self.root_path, "workflows", "template")
        operator_json_path = os.path.join(template_dir, "operator.json")
        existing_descriptions = {}

        library_record = self.operator_library.get(operator_name)
        if library_record:
            return True, f"持久算子库中已存在同名算子 {operator_name}"

        for record in self.operator_library.list_records():
            if skeleton_type and record.get("skeleton_type") != skeleton_type:
                continue
            existing_descriptions[record["name"]] = record.get("description", "")

        if os.path.exists(operator_json_path):
            with open(operator_json_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if operator_name in existing:
                return True, f"算子名 {operator_name} 已存在"
            for name, info in existing.items():
                if isinstance(info, dict):
                    if skeleton_type and info.get("skeleton_type") != skeleton_type:
                        continue
                    existing_descriptions[name] = info.get("description", "")

        for record in self.evolution_history:
            if record["name"] == operator_name:
                return True, f"本轮已进化出同名算子 {operator_name}"
            if skeleton_type and record.get("skeleton_type") != skeleton_type:
                continue
            existing_descriptions[record["name"]] = record.get("description", "")

        if description and existing_descriptions:
            dup_result = self._check_description_overlap(operator_name, description, existing_descriptions)
            if dup_result[0]:
                return dup_result

        return False, ""

    def _check_description_overlap(
        self,
        new_name: str,
        new_desc: str,
        existing_descriptions: Dict[str, str],
        threshold: float = 0.6,
    ) -> Tuple[bool, str]:
        """检查新算子描述与已有算子描述的功能重叠度"""
        stop_words = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "to", "of", "in", "for",
            "on", "with", "at", "by", "from", "as", "into", "through", "during",
            "before", "after", "above", "below", "between", "out", "off", "over",
            "under", "again", "further", "then", "once", "that", "this", "these",
            "those", "it", "its", "and", "but", "or", "nor", "not", "so", "yet",
            "both", "either", "neither", "each", "every", "all", "any", "few",
            "more", "most", "other", "some", "such", "no", "only", "own", "same",
            "than", "too", "very", "just", "because", "if", "when", "where",
            "how", "what", "which", "who", "whom", "while", "up", "about",
            "use", "using", "used", "make", "makes", "made", "get", "gets",
            "take", "takes", "give", "gives", "let", "allows", "provide",
            "provides", "perform", "performs", "call", "calls",
        }

        def extract_keywords(text: str) -> set:
            words = re.findall(r'[a-zA-Z_]{3,}', text.lower())
            return set(w for w in words if w not in stop_words)

        new_keywords = extract_keywords(new_desc)
        if not new_keywords:
            return False, ""

        for existing_name, existing_desc in existing_descriptions.items():
            existing_keywords = extract_keywords(existing_desc)
            if not existing_keywords:
                continue
            overlap = len(new_keywords & existing_keywords) / len(new_keywords)
            if overlap > threshold:
                overlapping = new_keywords & existing_keywords
                return True, (
                    f"算子 {new_name} 与 {existing_name} 功能重叠度 {overlap:.0%} "
                    f"(重叠关键词: {', '.join(sorted(overlapping))}). "
                    f"已有: '{existing_desc[:80]}' "
                    f"新: '{new_desc[:80]}'"
                )

        return False, ""

    # ----------------------------------------------------------
    # 安装
    # ----------------------------------------------------------

    def _install_operator(
        self, response: OperatorEvolveResponse
    ) -> Tuple[bool, str, str]:
        """将算子安装到 template 目录，并进行运行时 import 验证"""
        template_dir = os.path.join(self.root_path, "workflows", "template")
        files_written = []

        try:
            # 0. 清除 __pycache__
            self._clear_template_pycache(template_dir)

            # 1. 生成 prompt 常量代码并追加到 op_prompt.py
            prompt_code = self._generate_prompt_code(response)
            prompt_file = os.path.join(template_dir, "op_prompt.py")
            self._append_code(prompt_file, prompt_code, response.operator_name)
            files_written.append("op_prompt.py")

            # 2. 从骨架模板生成算子类代码并追加到 operator.py
            class_code = self._generate_class_code(response)
            op_file = os.path.join(template_dir, "operator.py")
            self._append_code(op_file, class_code, response.operator_name)
            files_written.append("operator.py")

            # 3. 更新 operator.json
            json_file = os.path.join(template_dir, "operator.json")
            self._update_operator_json(json_file, response)
            files_written.append("operator.json")

            # 4. 运行时 import 验证
            import_ok, import_err = self._runtime_import_test(template_dir)
            if not import_ok:
                logger.error(f"🧬 运行时 import 验证失败: {import_err}")
                self._rollback_operator(template_dir, response.operator_name, files_written)
                return False, f"运行时 import 验证失败: {import_err}", response.operator_name

            logger.info(
                f"🧬 骨架模板生成算子: skeleton_type={response.skeleton_type}, "
                f"prompts=[{response.prompt_1_name}, {response.prompt_2_name or 'N/A'}]"
            )
            return True, f"已写入: {', '.join(files_written)} (import 验证通过)", response.operator_name

        except Exception as e:
            logger.error(f"🧬 安装失败，尝试回滚: {e}")
            self._rollback_operator(template_dir, response.operator_name, files_written)
            return False, str(e), response.operator_name

    def _generate_prompt_code(self, response: OperatorEvolveResponse) -> str:
        """从 LLM 回复生成 prompt 常量代码"""
        lines = []

        # repr() preserves LaTeX backslashes (notably ``\boxed``) and safely
        # handles quotes/newlines without creating invalid source code.
        lines.append(f"{response.prompt_1_name} = {response.prompt_1!r}")

        # Prompt 2 (if applicable)
        if response.prompt_2.strip():
            lines.append("")
            lines.append("")
            lines.append(f"{response.prompt_2_name} = {response.prompt_2!r}")

        return "\n".join(lines)

    def _generate_class_code(self, response: OperatorEvolveResponse) -> str:
        """Compile a code-free design through a fixed, reviewed skeleton."""
        skeleton_type = response.skeleton_type
        if skeleton_type not in _SKELETON_TEMPLATES:
            skeleton_type = "single_step"
            logger.warning(f"🧬 未知 skeleton_type '{response.skeleton_type}'，回退到 single_step")

        template = _SKELETON_TEMPLATES[skeleton_type]

        # 替换占位符
        code = template.replace("__CLASS_NAME__", response.operator_name)
        code = code.replace("__PROMPT_1__", response.prompt_1_name)
        code = code.replace(
            "__REQUIRED_FEATURES__",
            repr(tuple(parse_feature_list(response.applicability_features))),
        )
        code = code.replace(
            "__BLOCKED_FEATURES__",
            repr(tuple(parse_feature_list(response.failure_boundary_features))),
        )
        code = code.replace("__RUNTIME_GUARD__", _RUNTIME_GUARD_METHODS)

        if response.prompt_2_name:
            code = code.replace("__PROMPT_2__", response.prompt_2_name)

        if response.routing_condition:
            code = code.replace("__CONDITION__", response.routing_condition)

        return code

    def _clear_template_pycache(self, template_dir: str):
        """清除 template 目录及其父目录的 __pycache__"""
        for dirpath in [template_dir, os.path.dirname(template_dir)]:
            pycache = os.path.join(dirpath, "__pycache__")
            if os.path.exists(pycache):
                shutil.rmtree(pycache, ignore_errors=True)
                logger.info(f"🧬 已清除 {pycache}")

    def _runtime_import_test(self, template_dir: str) -> Tuple[bool, str]:
        """运行时 import 验证"""
        parts = Path(template_dir).parts
        try:
            ws_idx = list(parts).index("workspace")
        except ValueError:
            return False, f"无法从路径 {template_dir} 推导 import 路径"

        relative_parts = parts[ws_idx:]
        base_import = ".".join(relative_parts)

        errors = []

        for module_name in ["op_prompt", "operator"]:
            full_import_path = f"{base_import}.{module_name}"

            try:
                modules_to_remove = [k for k in sys.modules if k.startswith(base_import)]
                for m in modules_to_remove:
                    del sys.modules[m]

                self._clear_template_pycache(template_dir)

                mod = importlib.import_module(full_import_path)

                for attr_name in dir(mod):
                    try:
                        getattr(mod, attr_name)
                    except (NameError, ImportError, AttributeError) as e:
                        errors.append(f"{module_name}.{attr_name}: {e}")

            except (NameError, ImportError, ModuleNotFoundError) as e:
                errors.append(f"import {full_import_path}: {e}")
            except Exception as e:
                err_str = str(e)
                if "NameError" in err_str or "is not defined" in err_str:
                    errors.append(f"import {full_import_path}: {e}")
                else:
                    logger.warning(f"🧬 import {full_import_path} 时出现非致命异常: {e}")

        if errors:
            return False, "; ".join(errors)
        return True, ""

    def _append_code(self, filepath: str, code: str, operator_name: str):
        """追加代码到文件，带注释分隔"""
        with open(filepath, "r", encoding="utf-8") as f:
            existing = f.read()

        separator = f"\n\n# ========== Auto-evolved operator: {operator_name} ==========\n\n"
        new_content = existing.rstrip() + separator + code.strip() + "\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

        logger.info(f"🧬 已追加 {operator_name} 代码到 {filepath}")

    def _update_operator_json(self, json_file: str, response: OperatorEvolveResponse):
        """更新 operator.json"""
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        func_name = response.operator_name.lower()
        # 所有骨架模板统一返回 {"response": str}
        interface = f"{func_name}(problem: str) -> dict with key 'response' of type str"

        data[response.operator_name] = {
            "description": response.description,
            "interface": interface,
            "auto_evolved": True,
            "skeleton_type": response.skeleton_type,
            "applicability_features": parse_feature_list(response.applicability_features),
            "failure_boundary_features": parse_feature_list(response.failure_boundary_features),
        }

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        logger.info(
            f"🧬 已更新 operator.json: +{response.operator_name} "
            f"(skeleton={response.skeleton_type})"
        )

    def _rollback_operator(self, template_dir: str, operator_name: str, files_written: List[str]):
        """回滚安装失败的算子"""
        separator_pattern = f"\n\n# ========== Auto-evolved operator: {operator_name} ==========\n\n"

        for filename in files_written:
            if filename == "operator.json":
                json_file = os.path.join(template_dir, filename)
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if operator_name in data:
                        del data[operator_name]
                        with open(json_file, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=4, ensure_ascii=False)
                except Exception:
                    pass
            else:
                filepath = os.path.join(template_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    idx = content.find(separator_pattern.strip())
                    if idx > 0:
                        content = content[:idx].rstrip() + "\n"
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(content)
                except Exception:
                    pass

    # ----------------------------------------------------------
    # 辅助方法（保持原有接口不变）
    # ----------------------------------------------------------

    def collect_failure_cases(self, max_cases: int = 5) -> str:
        """从最近的 log.json 中收集失败样例，并自动分类错误类型"""
        workflows_dir = os.path.join(self.root_path, "workflows")
        results_file = os.path.join(workflows_dir, "results.json")
        if not os.path.exists(results_file):
            return "无评估结果"

        try:
            with open(results_file, "r", encoding="utf-8") as f:
                results = json.load(f)
        except Exception:
            return "无法读取结果"

        if not results:
            return "无结果数据"

        sorted_results = sorted(results, key=lambda x: x.get("score", 0))
        failure_cases = []

        for result in sorted_results:
            round_num = result.get("round")
            log_file = os.path.join(workflows_dir, f"round_{round_num}", "log.json")
            if not os.path.exists(log_file):
                continue

            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    log_data = json.load(f)
            except Exception:
                continue

            if isinstance(log_data, dict):
                log_data = [log_data]

            for entry in log_data:
                extracted = entry.get("extracted_output", "")
                right_answer = entry.get("right_answer", "")
                question = entry.get("question", "")
                model_output = entry.get("model_output", "")

                if extracted and right_answer and extracted.strip() not in right_answer and right_answer.strip() not in extracted:
                    if len(model_output) > 500:
                        model_output = model_output[:500] + "...(truncated)"

                    error_type = self._classify_error(question, model_output, extracted, right_answer)

                    failure_cases.append({
                        "round": round_num,
                        "question": question[:200],
                        "model_output": model_output,
                        "extracted_answer": extracted[:100],
                        "correct_answer": right_answer[:100],
                        "error_type": error_type,
                    })

                if len(failure_cases) >= max_cases:
                    break

            if len(failure_cases) >= max_cases:
                break

        if not failure_cases:
            return "未找到明确的失败样例（可能答案格式不匹配而非推理错误）"

        type_counts = {}
        for case in failure_cases:
            t = case["error_type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        lines = [f"**Error Type Distribution**: {dict(type_counts)}\n"]
        for i, case in enumerate(failure_cases):
            lines.append(
                f"### Case {i+1} (R{case['round']}) [{case['error_type']}]\n"
                f"**Question**: {case['question']}\n"
                f"**Model Output** (partial): {case['model_output']}\n"
                f"**Extracted Answer**: {case['extracted_answer']}\n"
                f"**Correct Answer**: {case['correct_answer']}\n"
            )
        return "\n".join(lines)

    @staticmethod
    def _classify_error(question: str, model_output: str, extracted: str, correct: str) -> str:
        """基于规则的错误类型分类（无需 LLM 调用，零成本）"""
        q_lower = question.lower()

        # 格式化错误
        try:
            ext_clean = re.sub(r'[,$\\%\s]', '', extracted)
            cor_clean = re.sub(r'[,$\\%\s]', '', correct)
            if ext_clean == cor_clean or (
                ext_clean.replace('.0', '') == cor_clean.replace('.0', '')
            ):
                return "formatting"
        except Exception:
            pass

        # 提取错误
        if correct.strip() in model_output and correct.strip() != extracted.strip():
            return "extraction"

        # 计算错误
        try:
            ext_num = float(re.sub(r'[^\d.\-]', '', extracted))
            cor_num = float(re.sub(r'[^\d.\-]', '', correct))
            if ext_num != 0 and cor_num != 0:
                ratio = ext_num / cor_num
                if abs(ratio - round(ratio)) < 0.01 and round(ratio) in (2, 10, 100, 0.5, 0.1):
                    return "arithmetic"
        except (ValueError, ZeroDivisionError):
            pass

        # 几何/三角错误
        geo_keywords = ["triangle", "circle", "angle", "area", "perimeter", "diagonal",
                        "polygon", "rectangle", "square", "rhombus", "trapezoid",
                        "三角", "圆", "角", "面积", "周长"]
        if any(kw in q_lower for kw in geo_keywords):
            return "geometric"

        # 概率/组合错误
        prob_keywords = ["probability", "combin", "permut", "ways", "choose",
                         "概率", "组合", "排列"]
        if any(kw in q_lower for kw in prob_keywords):
            return "combinatorial"

        return "reasoning"

    def get_current_graph_code(self) -> str:
        """获取当前最优 round 的 graph.py 代码"""
        workflows_dir = os.path.join(self.root_path, "workflows")
        results_file = os.path.join(workflows_dir, "results.json")

        if not os.path.exists(results_file):
            return ""

        try:
            with open(results_file, "r", encoding="utf-8") as f:
                results = json.load(f)
        except Exception:
            return ""

        if not results:
            return ""

        best = max(results, key=lambda x: x.get("score", 0))
        best_round = best.get("round", 1)

        graph_file = os.path.join(workflows_dir, f"round_{best_round}", "graph.py")
        if not os.path.exists(graph_file):
            return ""

        with open(graph_file, "r", encoding="utf-8") as f:
            return f.read()

    def get_operator_descriptions(self, operators: List[str]) -> str:
        """获取算子描述列表"""
        json_file = os.path.join(self.root_path, "workflows", "template", "operator.json")
        if not os.path.exists(json_file):
            return "无算子描述"

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        lines = []
        for i, name in enumerate(operators):
            if name in data:
                desc = data[name].get("description", "")
                interface = data[name].get("interface", "")
                skeleton = data[name].get("skeleton_type", "")
                auto = " (auto-evolved)" if data[name].get("auto_evolved") else ""
                skel_info = f", skeleton={skeleton}" if skeleton else ""
                lines.append(f"{i+1}. **{name}**{auto}: {desc}\n   Interface: {interface}{skel_info}")
            else:
                lines.append(f"{i+1}. **{name}**: (no description)")

        return "\n".join(lines)

    def get_evolution_summary(self) -> str:
        """获取进化历史摘要"""
        if not self.evolution_history:
            return "无算子进化记录"

        lines = ["### 算子进化历史"]
        for i, record in enumerate(self.evolution_history):
            skel = record.get("skeleton_type", "unknown")
            lines.append(
                f"{i+1}. **{record['name']}** ({skel}, 尝试 {record['attempt']}): "
                f"{record['description']}\n   原因: {record['reasoning'][:100]}"
            )
        return "\n".join(lines)

    # ----------------------------------------------------------
    # 准入门槛：小规模评测过滤低质量进化算子
    # ----------------------------------------------------------

    async def quality_gate(
        self,
        operator_name: str,
        exec_llm_config,
        dataset: str,
        sample_size: int = 25,
        baseline_threshold: float = 0.0,
    ) -> Tuple[bool, Dict]:
        """
        进化算子准入门槛：在小样本集上对比新算子 vs Custom 的得分。

        Args:
            operator_name: 进化出的算子类名
            exec_llm_config: 执行 LLM 配置
            dataset: 数据集名 (e.g. "MATH")
            sample_size: 评测样本数，默认 25 题
            baseline_threshold: 最低通过门槛（相对于 Custom 基线的差值）。
                0.0 表示新算子必须 ≥ Custom 得分；
                -0.02 表示允许低 2%。

        Returns:
            (passed, details) — details 包含两个算子的得分和题级对比。
        """
        from benchmarks.math import MATHBenchmark
        from benchmarks.gsm8k import GSM8KBenchmark

        # 1. 动态实例化新算子
        template_dir = os.path.join(self.root_path, "workflows", "template")
        parts = Path(template_dir).parts
        try:
            ws_idx = list(parts).index("workspace")
        except ValueError:
            return False, {"error": f"无法从路径推导 import 路径"}

        base_import = ".".join(parts[ws_idx:])
        op_module_path = f"{base_import}.operator"

        # 清缓存
        for m_key in [k for k in sys.modules if k.startswith(base_import)]:
            del sys.modules[m_key]
        self._clear_template_pycache(template_dir)

        try:
            op_module = importlib.import_module(op_module_path)
        except Exception as e:
            return False, {"error": f"import 进化算子失败: {e}"}

        OpClass = getattr(op_module, operator_name, None)
        if OpClass is None:
            return False, {"error": f"未找到算子类 {operator_name}"}

        # 2. 加载评测数据（取前 sample_size 题）
        data_path = f"data/datasets/{dataset.lower()}_validate.jsonl"

        benchmark_map = {"MATH": MATHBenchmark, "GSM8K": GSM8KBenchmark}
        benchmark_cls = benchmark_map.get(dataset)
        if benchmark_cls is None:
            return False, {"error": f"不支持的数据集: {dataset}"}

        temp_log_dir = os.path.join(self.root_path, "ablation", f"gate_{operator_name}")
        os.makedirs(temp_log_dir, exist_ok=True)
        benchmark = benchmark_cls(name=dataset, file_path=data_path, log_path=temp_log_dir)

        all_data = await benchmark.load_data()
        eval_data = all_data[:sample_size]

        # 3. 并行跑 Custom 基线 vs 新算子
        llm = create_llm_instance(exec_llm_config)
        CustomClass = getattr(op_module, "Custom", None)
        if CustomClass is None:
            return False, {"error": "未找到 Custom 基线算子"}

        custom_op = CustomClass(llm)
        evolved_op = OpClass(llm)

        prompts_module = importlib.import_module(f"{base_import}.op_prompt")

        async def eval_single(problem, is_evolved: bool):
            try:
                problem_text = problem["problem"] if isinstance(problem, dict) else problem
                solution_text = problem["solution"] if isinstance(problem, dict) else ""
                if is_evolved:
                    result = await asyncio.wait_for(evolved_op(problem=problem_text), timeout=60)
                    output = result.get("response", "")
                else:
                    # Custom 需要 input + instruction
                    solve_prompt = getattr(prompts_module, "SOLVE_PROMPT", "Solve the problem step by step. Show all reasoning clearly. Output your final answer within \\boxed{}.\n")
                    result = await asyncio.wait_for(
                        custom_op(input=problem_text, instruction=solve_prompt),
                        timeout=60,
                    )
                    output = result.get("response", "")
                score, extracted = benchmark.calculate_score(solution_text, output)
                return {"score": score, "extracted": extracted}
            except asyncio.TimeoutError:
                return {"score": 0, "extracted": "TIMEOUT"}
            except Exception as e:
                return {"score": 0, "extracted": f"ERROR: {e}"}

        semaphore = asyncio.Semaphore(15)

        async def sem_eval(problem, is_evolved):
            async with semaphore:
                return await eval_single(problem, is_evolved)

        custom_tasks = [sem_eval(p, False) for p in eval_data]
        evolved_tasks = [sem_eval(p, True) for p in eval_data]

        custom_results = await asyncio.gather(*custom_tasks)
        evolved_results = await asyncio.gather(*evolved_tasks)

        custom_score = sum(r["score"] for r in custom_results) / len(custom_results)
        evolved_score = sum(r["score"] for r in evolved_results) / len(evolved_results)

        # 4. 逐题对比
        per_problem = []
        wins = ties = losses = 0
        for i, (c, e) in enumerate(zip(custom_results, evolved_results)):
            delta = e["score"] - c["score"]
            if delta > 0:
                wins += 1
            elif delta == 0:
                ties += 1
            else:
                losses += 1
            per_problem.append({
                "idx": i,
                "custom": c["score"],
                "evolved": e["score"],
                "delta": delta,
            })

        diff = evolved_score - custom_score
        passed = diff >= baseline_threshold

        details = {
            "dataset": dataset,
            "sample_size": sample_size,
            "custom_score": custom_score,
            "evolved_score": evolved_score,
            "diff": diff,
            "threshold": baseline_threshold,
            "wins": wins,
            "ties": ties,
            "losses": losses,
            "passed": passed,
            "per_problem": per_problem,
        }

        if self.operator_library.get(operator_name):
            self.operator_library.record_evidence(
                operator_name,
                {
                    "source": "legacy_quality_gate",
                    "scope": "quality_gate_overall",
                    "dataset": dataset,
                    "baseline": "Custom",
                    "sample_size": sample_size,
                    "wins": wins,
                    "ties": ties,
                    "losses": losses,
                    "candidate_score": evolved_score,
                    "baseline_score": custom_score,
                    "threshold": baseline_threshold,
                    "passed": passed,
                    "confidence": "low_comparability_one_call_vs_operator",
                },
            )
            record = self.operator_library.get(operator_name) or {}
            has_rules = bool(record.get("applicability_rules"))
            if passed and has_rules:
                self.operator_library.set_status(
                    operator_name,
                    "candidate",
                    "Passed legacy quality gate; paired contextual evidence is still required before routing.",
                )
            elif passed:
                self.operator_library.set_status(
                    operator_name,
                    "candidate",
                    "Passed quality gate but has no valid applicability metadata.",
                )
            else:
                self.operator_library.set_status(
                    operator_name,
                    "rejected",
                    "Failed quality gate; artifact retained for audit.",
                )

        status = "✅ 通过" if passed else "❌ 未通过"
        logger.info(
            f"🧬 准入门槛 {status}: 新算子={evolved_score:.4f}, "
            f"Custom={custom_score:.4f}, Δ={diff:+.4f} "
            f"(W/T/L={wins}/{ties}/{losses}, 门槛={baseline_threshold:+.2f})"
        )

        return passed, details
