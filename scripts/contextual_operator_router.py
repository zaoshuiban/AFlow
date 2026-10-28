# -*- coding: utf-8 -*-
"""Context-aware execution layer for evidence-backed evolved operators."""

from __future__ import annotations

import importlib
import inspect
import json
import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from scripts.operator_library import (
    OperatorLibrary,
    ProblemFeatureExtractor,
    RetrievalDecision,
    semantic_python_sha256,
    stable_problem_id,
)


GENERIC_ANALYSIS_PROMPT = (
    "Analyze the mathematical problem. Identify its type, constraints, useful "
    "theorems, and a reliable solution plan. Do not guess the final answer.\n"
)
GENERIC_SOLVE_PROMPT = (
    "Use the supplied analysis to solve the problem carefully. Verify every "
    "step and put the final answer in \\boxed{}.\n"
)


class GenericTwoStageSolver:
    """Two-call fallback with the same skeleton budget as current specialists."""

    def __init__(
        self,
        llm: Any,
        operator_module: str,
        custom_factory: Optional[Callable[..., Any]] = None,
    ):
        self.llm = llm
        if custom_factory is None:
            module = importlib.import_module(operator_module)
            custom_factory = getattr(module, "Custom")
        self.custom = self._construct(custom_factory)

    def _construct(self, factory: Callable[..., Any]) -> Any:
        try:
            return factory(self.llm)
        except TypeError:
            return factory()

    async def __call__(self, problem: str) -> Dict[str, Any]:
        analysis = await self.custom(
            input=problem,
            instruction=GENERIC_ANALYSIS_PROMPT,
        )
        if analysis.get("status") == "error":
            return {
                "status": "error",
                "response": "",
                "observation": f"fallback analysis failed: {analysis.get('observation', '')}",
            }
        combined = f"Problem:\n{problem}\n\nAnalysis:\n{analysis.get('response', '')}"
        result = await self.custom(input=combined, instruction=GENERIC_SOLVE_PROMPT)
        if result.get("status") == "error":
            return {
                "status": "error",
                "response": "",
                "observation": f"fallback solve failed: {result.get('observation', '')}",
            }
        return {
            "status": "success",
            "response": result.get("response", ""),
            "observation": "",
        }


class ContextAwareOperatorRouter:
    """Retrieve one safe specialist, otherwise execute a generic fallback.

    The router is intentionally deterministic: it uses declared rules and
    stored evidence only.  ``operator_factories`` is injectable so routing and
    fallback behavior can be tested without network calls.
    """

    def __init__(
        self,
        llm: Any,
        library_path: str,
        operator_module: str,
        *,
        min_score: float = 0.70,
        operator_factories: Optional[Dict[str, Callable[..., Any]]] = None,
        fallback: Optional[Callable[[str], Any]] = None,
        custom_factory: Optional[Callable[..., Any]] = None,
    ):
        self.llm = llm
        self.library = OperatorLibrary(library_path, auto_create=False)
        self.operator_module = operator_module
        self.min_score = min_score
        self.operator_factories = operator_factories or {}
        self._instances: Dict[str, tuple[str, Any]] = {}
        self.fallback = fallback or GenericTwoStageSolver(
            llm=llm,
            operator_module=operator_module,
            custom_factory=custom_factory,
        )

    def decide(self, problem: str) -> RetrievalDecision:
        try:
            return self.library.retrieve(problem, top_k=3, min_score=self.min_score)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
            context = ProblemFeatureExtractor().extract(problem)
            return RetrievalDecision(
                context=context,
                selected=None,
                candidates=[],
                blocked=[],
                threshold=self.min_score,
                library_error=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _construct(factory: Callable[..., Any], llm: Any) -> Any:
        try:
            return factory(llm)
        except TypeError:
            return factory()

    def _resolve(self, operator_name: str) -> Any:
        record = self.library.get(operator_name) or {}
        artifact = record.get("artifact", {})
        artifact_sha = artifact.get("artifact_sha256", "")
        cached = self._instances.get(operator_name)
        if cached and cached[0] == artifact_sha:
            return cached[1]
        factory = self.operator_factories.get(operator_name)
        if factory is None:
            module_name = self.operator_module or record.get("module")
            class_name = record.get("class_name") or operator_name
            module = importlib.import_module(module_name)
            factory = getattr(module, class_name)
            expected_class_hash = artifact.get("class_sha256", "")
            if not expected_class_hash:
                raise ValueError(f"missing class hash for {operator_name}")
            actual_class_hash = semantic_python_sha256(inspect.getsource(factory))
            if actual_class_hash != expected_class_hash:
                raise ValueError(f"class artifact drift for {operator_name}")
            prompt_names = artifact.get("prompt_names", [])
            expected_prompt_hash = artifact.get("prompt_sha256", "")
            if expected_prompt_hash:
                prompt_source = "\n".join(
                    f"{name} = {getattr(module, name)!r}" for name in prompt_names
                )
                if semantic_python_sha256(prompt_source) != expected_prompt_hash:
                    raise ValueError(f"prompt artifact drift for {operator_name}")
            if str(record.get("skeleton_type", "")).startswith(
                "reusable_parameterized_tool"
            ):
                expected_runtime_hash = artifact.get(
                    "runtime_dependency_sha256", ""
                )
                if not expected_runtime_hash:
                    raise ValueError(
                        f"missing runtime dependency hash for {operator_name}"
                    )
                dependency = str(artifact.get("runtime_dependency", ""))
                if not dependency:
                    raise ValueError(
                        f"missing runtime dependency path for {operator_name}"
                    )
                repository_root = Path(__file__).resolve().parent.parent
                runtime_path = (repository_root / dependency).resolve()
                if repository_root not in runtime_path.parents:
                    raise ValueError(
                        f"runtime dependency escapes repository for {operator_name}"
                    )
                if not runtime_path.is_file():
                    raise ValueError(
                        f"runtime dependency is missing for {operator_name}"
                    )
                actual_runtime_hash = hashlib.sha256(
                    runtime_path.read_bytes()
                ).hexdigest()
                if actual_runtime_hash != expected_runtime_hash:
                    raise ValueError(
                        f"runtime dependency drift for {operator_name}"
                    )
        instance = self._construct(factory, self.llm)
        self._instances[operator_name] = (artifact_sha, instance)
        return instance

    async def _fallback(
        self,
        problem: str,
        decision: RetrievalDecision,
        reason: str,
    ) -> Dict[str, Any]:
        try:
            result = await self.fallback(problem)
        except Exception as exc:
            result = {
                "status": "error",
                "response": "",
                "observation": f"fallback failed: {type(exc).__name__}: {exc}",
            }
        if not isinstance(result, dict):
            result = {"status": "success", "response": str(result)}
        return {
            **result,
            "selected_operator": None,
            "used_fallback": True,
            "fallback_reason": reason,
            "route": decision.to_dict(),
        }

    async def __call__(self, problem: str) -> Dict[str, Any]:
        decision = self.decide(problem)
        if decision.selected is None:
            reason = (
                f"library_unavailable:{decision.library_error}"
                if decision.library_error
                else "no_safe_match"
            )
            return await self._fallback(problem, decision, reason)

        operator_name = decision.selected.operator_name
        try:
            operator = self._resolve(operator_name)
            result = await operator(problem=problem)
            if not isinstance(result, dict):
                result = {"status": "success", "response": str(result)}
            if result.get("status") == "declined":
                return await self._fallback(
                    problem,
                    decision,
                    f"specialist_declined:{result.get('observation', 'not applicable')}",
                )
            if result.get("status") == "error" or not result.get("response"):
                return await self._fallback(
                    problem,
                    decision,
                    f"specialist_error:{result.get('observation', 'empty response')}",
                )
            return {
                **result,
                "selected_operator": operator_name,
                "used_fallback": False,
                "fallback_reason": "",
                "route": decision.to_dict(),
            }
        except Exception as exc:
            return await self._fallback(
                problem,
                decision,
                f"specialist_exception:{type(exc).__name__}:{exc}",
            )

    def record_feedback(
        self,
        *,
        problem: str,
        operator_name: str,
        operator_correct: bool,
        baseline_correct: bool,
        source: str,
        run_id: str = "",
        notes: str = "",
    ) -> Dict[str, Any]:
        """Persist paired feedback after ground truth becomes available."""
        record = self.library.get(operator_name)
        if not record:
            raise KeyError(operator_name)
        context = self.library.extractor.extract(problem)
        selected_scope = "overall"
        matching_rules = [
            rule
            for rule in record.get("applicability_rules", [])
            if self.library._rule_match(rule, context)
        ]
        if matching_rules:
            selected_scope = max(
                matching_rules, key=self.library._rule_weight
            ).get("name", "overall")
        win = int(operator_correct and not baseline_correct)
        loss = int(baseline_correct and not operator_correct)
        tie = int(operator_correct == baseline_correct)
        return self.library.record_evidence(
            operator_name,
            {
                "source": source,
                "run_id": run_id,
                "scope": selected_scope,
                "baseline": "generic_two_stage",
                "protocol": "paired_equal_call_v1",
                "routing_eligible": True,
                "wins": win,
                "ties": tie,
                "losses": loss,
                "problem_id": stable_problem_id(problem),
                "problem_features": sorted(context.features),
                "notes": notes,
            },
        )
