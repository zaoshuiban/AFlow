# -*- coding: utf-8 -*-
"""Direct capability tournament for staged, auto-evolved operators.

Candidates are executed from immutable library artifacts.  They are not
installed into a workflow template and cannot be routed by AFlow until one
passes the promotion policy below.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from benchmarks.math import MATHBenchmark
from scripts.async_llm import create_llm_instance
from scripts.contextual_operator_router import GenericTwoStageSolver
from scripts.operator_library import OperatorLibrary, stable_problem_id


GENERIC_PROGRAMMER_ANALYSIS = (
    "Use Python to compute the exact answer to this problem. Choose a correct "
    "general algorithm from the problem statement itself, do not read files or "
    "use network access, and define a zero-argument solve() that returns only "
    "the final answer value. Only standard exact-computation modules such as "
    "math, fractions, itertools, functools, collections, decimal, and statistics "
    "are permitted."
)


class GenericProgrammerSolver:
    """Tool-access control: generic Programmer without evolved instructions."""

    def __init__(self, llm: Any, operator_module: str):
        module = importlib.import_module(operator_module)
        self.programmer = getattr(module, "Programmer")(llm, "GenericProgrammerControl")

    async def __call__(self, problem: str) -> Dict[str, Any]:
        result = await self.programmer(
            problem=problem,
            analysis=GENERIC_PROGRAMMER_ANALYSIS,
        )
        code = str(result.get("code", "") or "")
        output = str(result.get("output", "") or "").strip()
        trace = {"code": code, "output": output}
        if not output or output == "No code generated" or output.startswith(
            (
                "Execution error",
                "Code execution timed out",
                "Unknown error",
                "Prohibited import",
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
            "response": f"Python execution result: {output}\n\\boxed{{{output}}}",
            "observation": "",
            "tool_trace": trace,
        }


@dataclass(frozen=True)
class QualityWeights:
    """Weights for quality, not hard promotion requirements."""

    target_loss: float = 1.25
    regression_loss: float = 1.50
    instability: float = 0.30
    decline: float = 0.25
    error: float = 0.50
    token_overhead: float = 0.05


@dataclass(frozen=True)
class PromotionPolicy:
    min_target_observations: int = 6
    min_unique_target_problems: int = 2
    min_unique_winning_problems: int = 2
    min_net_wins: int = 2
    max_regression_loss_rate: float = 0.10
    max_target_decline_rate: float = 0.25
    max_error_rate: float = 0.10


def _bounded_stability(rows: Sequence[Mapping[str, Any]]) -> float:
    by_problem: Dict[str, List[int]] = {}
    for row in rows:
        by_problem.setdefault(str(row["problem_id"]), []).append(
            int(row["candidate_score"])
        )
    if not by_problem:
        return 0.0
    variances = [statistics.pvariance(values) for values in by_problem.values()]
    return max(0.0, min(1.0, 1.0 - 4.0 * statistics.mean(variances)))


def calculate_operator_quality(
    target_rows: Sequence[Mapping[str, Any]],
    regression_rows: Sequence[Mapping[str, Any]],
    weights: QualityWeights | None = None,
    policy: PromotionPolicy | None = None,
) -> Dict[str, Any]:
    """Calculate rank score and independent hard promotion gates.

    A decline on a regression item is treated as safe fallback to the baseline;
    a decline on a target item is a failed specialist attempt.
    """
    weights = weights or QualityWeights()
    policy = policy or PromotionPolicy()
    if not target_rows:
        raise ValueError("target_rows cannot be empty")

    wins = sum(row["candidate_score"] > row["baseline_score"] for row in target_rows)
    ties = sum(row["candidate_score"] == row["baseline_score"] for row in target_rows)
    losses = sum(row["candidate_score"] < row["baseline_score"] for row in target_rows)
    unique_problem_ids = {str(row["problem_id"]) for row in target_rows}
    unique_winning_ids = {
        str(row["problem_id"])
        for row in target_rows
        if row["candidate_score"] > row["baseline_score"]
    }
    target_n = len(target_rows)
    regression_n = len(regression_rows)
    regression_losses = sum(
        row["effective_candidate_score"] < row["baseline_score"]
        for row in regression_rows
    )
    target_declines = sum(bool(row.get("declined")) for row in target_rows)
    errors = sum(bool(row.get("error")) for row in [*target_rows, *regression_rows])
    all_n = target_n + regression_n
    stability = _bounded_stability(target_rows)

    baseline_tokens = sum(max(0, int(row.get("baseline_tokens", 0))) for row in target_rows)
    candidate_tokens = sum(max(0, int(row.get("candidate_tokens", 0))) for row in target_rows)
    token_ratio = candidate_tokens / baseline_tokens if baseline_tokens else 1.0
    positive_token_overhead = max(0.0, token_ratio - 1.0)

    target_term = (wins - weights.target_loss * losses) / target_n
    regression_loss_rate = regression_losses / regression_n if regression_n else 0.0
    target_decline_rate = target_declines / target_n
    error_rate = errors / all_n if all_n else 0.0
    quality_score = (
        target_term
        - weights.regression_loss * regression_loss_rate
        - weights.instability * (1.0 - stability)
        - weights.decline * target_decline_rate
        - weights.error * error_rate
        - weights.token_overhead * math.log1p(positive_token_overhead)
    )

    gate_failures = []
    if target_n < policy.min_target_observations:
        gate_failures.append(
            f"target observations {target_n} < {policy.min_target_observations}"
        )
    if len(unique_problem_ids) < policy.min_unique_target_problems:
        gate_failures.append(
            "unique target problems "
            f"{len(unique_problem_ids)} < {policy.min_unique_target_problems}"
        )
    if len(unique_winning_ids) < policy.min_unique_winning_problems:
        gate_failures.append(
            "unique winning problems "
            f"{len(unique_winning_ids)} < {policy.min_unique_winning_problems}"
        )
    if wins - losses < policy.min_net_wins:
        gate_failures.append(f"net wins {wins - losses} < {policy.min_net_wins}")
    if regression_loss_rate > policy.max_regression_loss_rate:
        gate_failures.append(
            f"regression loss rate {regression_loss_rate:.3f} > "
            f"{policy.max_regression_loss_rate:.3f}"
        )
    if target_decline_rate > policy.max_target_decline_rate:
        gate_failures.append(
            f"target decline rate {target_decline_rate:.3f} > "
            f"{policy.max_target_decline_rate:.3f}"
        )
    if error_rate > policy.max_error_rate:
        gate_failures.append(
            f"error rate {error_rate:.3f} > {policy.max_error_rate:.3f}"
        )

    return {
        "quality_score": quality_score,
        "passed": not gate_failures,
        "gate_failures": gate_failures,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "net_wins": wins - losses,
        "target_observations": target_n,
        "unique_target_problems": len(unique_problem_ids),
        "unique_winning_problems": len(unique_winning_ids),
        "unique_winning_problem_ids": sorted(unique_winning_ids),
        "regression_observations": regression_n,
        "regression_losses": regression_losses,
        "regression_loss_rate": regression_loss_rate,
        "target_declines": target_declines,
        "target_decline_rate": target_decline_rate,
        "errors": errors,
        "error_rate": error_rate,
        "stability": stability,
        "target_token_ratio": token_ratio,
        "weights": asdict(weights),
        "promotion_policy": asdict(policy),
    }


class OperatorTournament:
    def __init__(
        self,
        *,
        library_path: str | Path,
        template_dir: str | Path,
        llm_config: Any,
        timeout: float = 120.0,
        concurrency: int = 8,
        weights: QualityWeights | None = None,
        policy: PromotionPolicy | None = None,
    ):
        self.library = OperatorLibrary(library_path, auto_create=False)
        self.template_dir = Path(template_dir)
        self.llm_config = llm_config
        self.timeout = timeout
        self.semaphore = asyncio.Semaphore(concurrency)
        self.weights = weights or QualityWeights()
        self.policy = policy or PromotionPolicy()
        self.operator_module = self._operator_module_name()

    def _operator_module_name(self) -> str:
        parts = self.template_dir.parts
        try:
            workspace_index = list(parts).index("workspace")
        except ValueError as exc:
            raise ValueError(f"cannot derive module from {self.template_dir}") from exc
        return ".".join(parts[workspace_index:]) + ".operator"

    def _load_candidate_factory(self, operator_name: str) -> type:
        record = self.library.get(operator_name)
        if not record:
            raise KeyError(operator_name)
        if record.get("status") != "candidate":
            raise ValueError(
                f"tournament accepts candidate status only: {operator_name}="
                f"{record.get('status')}"
            )
        artifact = record.get("artifact", {})
        class_code = self.library._artifact_text(
            artifact, "class_code", "class_code_path"
        )
        prompt_code = self.library._artifact_text(
            artifact, "prompt_code", "prompt_code_path"
        )
        module = importlib.import_module(self.operator_module)
        namespace = dict(vars(module))
        namespace["__name__"] = f"{self.operator_module}.__tournament__"
        if prompt_code:
            exec(compile(prompt_code, f"<{operator_name}:prompts>", "exec"), namespace)
        exec(compile(class_code, f"<{operator_name}:class>", "exec"), namespace)
        factory = namespace.get(operator_name)
        if not isinstance(factory, type):
            raise ValueError(f"artifact does not define {operator_name}")
        return factory

    async def _execute(
        self,
        *,
        item: Mapping[str, Any],
        candidate_factory: type | None,
        control: str = "",
    ) -> Dict[str, Any]:
        async with self.semaphore:
            llm = create_llm_instance(self.llm_config)
            try:
                if control == "generic_programmer":
                    solver = GenericProgrammerSolver(
                        llm=llm, operator_module=self.operator_module
                    )
                elif candidate_factory is None:
                    solver = GenericTwoStageSolver(
                        llm=llm, operator_module=self.operator_module
                    )
                else:
                    solver = candidate_factory(llm)
                result = await asyncio.wait_for(
                    solver(str(item["problem"])), timeout=self.timeout
                )
                if not isinstance(result, dict):
                    result = {"status": "success", "response": str(result)}
                status = str(result.get("status", "success"))
                declined = status == "declined"
                response = str(result.get("response", "") or "")
                observation = str(result.get("observation", "") or "")
                error = "" if status in {"success", "declined"} else str(
                    result.get("observation", status)
                )
                tool_trace = result.get("tool_trace", {})
            except Exception as exc:
                response = ""
                declined = False
                error = f"{type(exc).__name__}: {exc}"
                observation = error
                tool_trace = {}
            scorer = MATHBenchmark(
                name="MATH", file_path="", log_path=str(self.template_dir)
            )
            score, extracted = scorer.calculate_score(
                str(item["solution"]), response
            )
            usage = llm.get_usage_summary()
            return {
                "score": int(score),
                "prediction": response,
                "extracted": extracted,
                "declined": declined,
                "error": error,
                "observation": observation,
                "tokens": int(usage.get("total_tokens", 0) or 0),
                "calls": int(usage.get("call_count", 0) or 0),
                "cost": float(usage.get("total_cost", 0.0) or 0.0),
                "tool_trace": tool_trace,
            }

    async def evaluate(
        self,
        *,
        candidate_names: Sequence[str],
        data: Sequence[Mapping[str, Any]],
        target_indices: Sequence[int],
        regression_indices: Sequence[int],
        num_runs: int = 3,
        promote: bool = True,
        include_programmer_control: bool = False,
    ) -> Dict[str, Any]:
        # A generated-candidate tournament normally has multiple entrants, but
        # a frozen reusable artifact is intentionally confirmed as one
        # candidate against the paired generic baseline.  The baseline is
        # already evaluated below, so requiring two evolved candidates would
        # reject a valid single-candidate confirmation before execution.
        if not candidate_names:
            raise ValueError("paired evaluation requires at least one candidate")
        target_set = set(target_indices)
        regression_set = set(regression_indices)
        if not target_set:
            raise ValueError("target_indices cannot be empty")
        if target_set & regression_set:
            raise ValueError("target and regression indices must be disjoint")
        requested = sorted(target_set | regression_set)
        if any(index < 0 or index >= len(data) for index in requested):
            raise IndexError("tournament index outside dataset")
        if num_runs <= 0:
            raise ValueError("num_runs must be positive")

        baseline: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for run in range(num_runs):
            outputs = await asyncio.gather(
                *(
                    self._execute(item=data[index], candidate_factory=None)
                    for index in requested
                )
            )
            baseline.update(
                {(run, index): output for index, output in zip(requested, outputs)}
            )

        candidates = []
        entrants = []
        if include_programmer_control:
            entrants.append(
                ("GenericProgrammerControl", None, "generic_programmer_control")
            )
        entrants.extend(
            (name, self._load_candidate_factory(name), "evolved_candidate")
            for name in candidate_names
        )
        for operator_name, factory, entrant_type in entrants:
            rows = []
            for run in range(num_runs):
                outputs = await asyncio.gather(
                    *(
                        self._execute(
                            item=data[index],
                            candidate_factory=factory,
                            control=(
                                "generic_programmer"
                                if entrant_type == "generic_programmer_control"
                                else ""
                            ),
                        )
                        for index in requested
                    )
                )
                for index, output in zip(requested, outputs):
                    base = baseline[(run, index)]
                    split = "target" if index in target_set else "regression"
                    effective_score = (
                        base["score"]
                        if split == "regression" and output["declined"]
                        else output["score"]
                    )
                    rows.append(
                        {
                            "run": run + 1,
                            "index": index,
                            "split": split,
                            "problem_id": stable_problem_id(str(data[index]["problem"])),
                            "baseline_score": base["score"],
                            "candidate_score": output["score"],
                            "effective_candidate_score": effective_score,
                            "baseline_prediction": base["prediction"],
                            "baseline_extracted": base["extracted"],
                            "candidate_prediction": output["prediction"],
                            "candidate_extracted": output["extracted"],
                            "declined": output["declined"],
                            "error": output["error"],
                            "baseline_error": base["error"],
                            "candidate_error": output["error"],
                            "baseline_observation": base.get("observation", ""),
                            "candidate_observation": output.get("observation", ""),
                            "baseline_tokens": base["tokens"],
                            "candidate_tokens": output["tokens"],
                            "baseline_calls": base["calls"],
                            "candidate_calls": output["calls"],
                            "baseline_cost": base["cost"],
                            "candidate_cost": output["cost"],
                            "baseline_tool_trace": base.get("tool_trace", {}),
                            "candidate_tool_trace": output.get("tool_trace", {}),
                        }
                    )
            target_rows = [row for row in rows if row["split"] == "target"]
            regression_rows = [row for row in rows if row["split"] == "regression"]
            metrics = calculate_operator_quality(
                target_rows,
                regression_rows,
                weights=self.weights,
                policy=self.policy,
            )
            candidates.append(
                {
                    "operator_name": operator_name,
                    "entrant_type": entrant_type,
                    "metrics": metrics,
                    "rows": rows,
                }
            )

        candidates.sort(
            key=lambda item: (
                not item["metrics"]["passed"],
                -item["metrics"]["quality_score"],
                item["operator_name"],
            )
        )
        passed = [item for item in candidates if item["metrics"]["passed"]]
        winner = passed[0]["operator_name"] if passed else None
        evolved_passed = [
            item for item in passed if item["entrant_type"] == "evolved_candidate"
        ]
        evolved_winner = (
            evolved_passed[0]["operator_name"] if evolved_passed else None
        )

        if promote:
            for item in candidates:
                if item["entrant_type"] != "evolved_candidate":
                    continue
                name = item["operator_name"]
                metrics = item["metrics"]
                self.library.record_evidence(
                    name,
                    {
                        "source": "operator_tournament",
                        "scope": "generated_applicability",
                        "dataset": "MATH",
                        "baseline": "generic_two_stage",
                        "protocol": "paired_direct_capability_v2",
                        "routing_eligible": name == evolved_winner,
                        "wins": metrics["wins"],
                        "ties": metrics["ties"],
                        "losses": metrics["losses"],
                        "unique_problems": metrics["unique_target_problems"],
                        "unique_winning_problems": metrics[
                            "unique_winning_problems"
                        ],
                        "quality_score": metrics["quality_score"],
                        "gate_failures": metrics["gate_failures"],
                    },
                )
                if name == evolved_winner:
                    self.library.set_status(
                        name,
                        "experimental",
                        "Won direct capability tournament and passed promotion gates.",
                    )
                else:
                    self.library.set_status(
                        name,
                        "rejected",
                        "Did not win or pass direct capability tournament.",
                    )
            if evolved_winner:
                self.library.install_artifact(evolved_winner, self.template_dir)

        return {
            "evaluation_mode": (
                "paired_single_candidate_confirmation"
                if len(candidate_names) == 1 and not include_programmer_control
                else "multi_candidate_tournament"
            ),
            "winner": winner,
            "evolved_winner": evolved_winner,
            "promoted": bool(promote and evolved_winner),
            "target_indices": sorted(target_set),
            "regression_indices": sorted(regression_set),
            "num_runs": num_runs,
            "ranking": candidates,
        }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            value = json.loads(line)
            if not isinstance(value, dict) or "problem" not in value or "solution" not in value:
                raise ValueError(f"malformed dataset row {line_number}: {path}")
            rows.append(value)
    return rows
