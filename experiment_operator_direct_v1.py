# -*- coding: utf-8 -*-
"""Direct, budget-controlled evaluation of generic and evolved operators.

Unlike the workflow ablation, this script deliberately excludes ScEnsemble so
that operator ability is not hidden by a downstream selector.  The primary
paired analysis includes only rows where both sides completed without an error
and made exactly two LLM calls.  A contextual specialist that fails may invoke
the two-call fallback after already spending up to two specialist calls, so
recovery rows can use as many as four calls and are reported separately.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import csv
import hashlib
import importlib
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmarks.math import MATHBenchmark
from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.operator_library import stable_problem_id


GENERIC_ANALYSIS_PROMPT = (
    "Analyze the mathematical problem. Identify its type, constraints, useful "
    "theorems, and a reliable solution plan. Do not guess the final answer.\n"
)
GENERIC_SOLVE_PROMPT = (
    "Use the supplied analysis to solve the problem carefully. Verify every "
    "step and put the final answer in \\boxed{}.\n"
)

CONFIGS = {
    "generic_two_stage": "Generic two-stage control",
    "evolved_geo": "GeometricCombinatorialDecomposer",
    "evolved_combo": "CombinatorialSymmetryDecomposer",
    "contextual_router": "Evidence-aware contextual router with generic fallback",
}

REPO_ROOT = Path(__file__).resolve().parent
MATH_TEMPLATE_DIR = REPO_ROOT / "workspace" / "MATH" / "workflows" / "template"
OPERATOR_LIBRARY_PATH = (
    REPO_ROOT / "workspace" / "MATH" / "operator_library" / "operators.json"
)
COMBO_OPERATOR_NAME = "CombinatorialSymmetryDecomposer"
EXPECTED_CALL_COUNT = 2
MATH_SCORER_PATH = REPO_ROOT / "benchmarks" / "math.py"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _artifact_text(
    library_path: Path,
    artifact: dict[str, Any],
    inline_key: str,
    path_key: str,
) -> str:
    inline = artifact.get(inline_key, "")
    if inline:
        return str(inline)
    raw_path = str(artifact.get(path_key, "") or "").strip()
    if not raw_path:
        return ""
    path = Path(raw_path)
    candidates = [path] if path.is_absolute() else [REPO_ROOT / path]
    if not path.is_absolute():
        candidates.extend(parent / path for parent in library_path.parents)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return ""


def _class_ast(source: str, class_name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return ast.dump(node, include_attributes=False)
    raise ValueError(f"Artifact does not define class {class_name}")


def _literal_prompt_values(source: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value_node = node.value
        for target in targets:
            if not isinstance(target, ast.Name) or not target.id.endswith("_PROMPT"):
                continue
            try:
                value = ast.literal_eval(value_node)
            except (ValueError, TypeError):
                continue
            if isinstance(value, str):
                values[target.id] = value
    return values


def prepare_combo_artifact() -> tuple[type, dict[str, Any]]:
    """Install, verify, and load the library's current combo artifact.

    The class is executed in an isolated namespace populated from the MATH
    template, then overwritten with the archived prompt values and class body.
    This makes the direct experiment execute the archived revision even if the
    mutable template contains a stale class or prompt with the same name.
    """
    from scripts.operator_library import (
        OperatorLibrary,
        combined_artifact_sha256,
        semantic_python_sha256,
    )

    library = OperatorLibrary(OPERATOR_LIBRARY_PATH, auto_create=False)
    record = library.get(COMBO_OPERATOR_NAME)
    if not record:
        raise RuntimeError(
            f"{COMBO_OPERATOR_NAME} is missing from {OPERATOR_LIBRARY_PATH}"
        )
    if record.get("status") in {"rejected", "retired"}:
        raise RuntimeError(
            f"Refusing to evaluate {COMBO_OPERATOR_NAME} with status "
            f"{record.get('status')!r}"
        )

    artifact = record.get("artifact", {})
    class_code = _artifact_text(
        OPERATOR_LIBRARY_PATH, artifact, "class_code", "class_code_path"
    )
    prompt_code = _artifact_text(
        OPERATOR_LIBRARY_PATH, artifact, "prompt_code", "prompt_code_path"
    )
    if not class_code:
        raise RuntimeError(f"No archived class code for {COMBO_OPERATOR_NAME}")
    _class_ast(class_code, COMBO_OPERATOR_NAME)
    compile(class_code, f"<{COMBO_OPERATOR_NAME}:class>", "exec")
    if prompt_code:
        compile(prompt_code, f"<{COMBO_OPERATOR_NAME}:prompts>", "exec")

    class_sha256 = semantic_python_sha256(class_code)
    prompt_sha256 = semantic_python_sha256(prompt_code)
    artifact_sha256 = combined_artifact_sha256(class_code, prompt_code)
    declared_class_sha = str(artifact.get("class_sha256", "") or "")
    declared_prompt_sha = str(artifact.get("prompt_sha256", "") or "")
    declared_artifact_sha = str(artifact.get("artifact_sha256", "") or "")
    if declared_class_sha and declared_class_sha != class_sha256:
        raise RuntimeError("Archived class digest does not match library metadata")
    if declared_prompt_sha and declared_prompt_sha != prompt_sha256:
        raise RuntimeError("Archived prompt digest does not match library metadata")
    if declared_artifact_sha and declared_artifact_sha != artifact_sha256:
        raise RuntimeError("Combined artifact digest does not match library metadata")

    installed = library.install_artifact(COMBO_OPERATOR_NAME, MATH_TEMPLATE_DIR)
    importlib.invalidate_caches()
    module_name = str(
        record.get("module") or "workspace.MATH.workflows.template.operator"
    )
    for cached_name in list(sys.modules):
        if cached_name == module_name or cached_name.startswith(
            module_name.rsplit(".", 1)[0] + "."
        ):
            del sys.modules[cached_name]
    template_module = importlib.import_module(module_name)
    if not hasattr(template_module, COMBO_OPERATOR_NAME):
        raise RuntimeError(
            f"Installed module {module_name} has no {COMBO_OPERATOR_NAME}"
        )

    template_source = (MATH_TEMPLATE_DIR / "operator.py").read_text(encoding="utf-8")
    class_ast_matches = (
        _class_ast(template_source, COMBO_OPERATOR_NAME)
        == _class_ast(class_code, COMBO_OPERATOR_NAME)
    )
    if not class_ast_matches:
        raise RuntimeError(
            f"Installed {COMBO_OPERATOR_NAME} class differs from archived artifact"
        )

    expected_prompts = _literal_prompt_values(prompt_code)
    installed_prompts = {
        name: getattr(template_module, name, None) for name in expected_prompts
    }
    template_prompts_match = installed_prompts == expected_prompts

    # Execute the exact archived revision in an isolated namespace as a final
    # guard against import-cache or mutable-template drift.
    namespace = dict(vars(template_module))
    namespace["__name__"] = f"{module_name}.__direct_library_artifact__"
    if prompt_code:
        exec(compile(prompt_code, f"<{COMBO_OPERATOR_NAME}:prompts>", "exec"), namespace)
    exec(compile(class_code, f"<{COMBO_OPERATOR_NAME}:class>", "exec"), namespace)
    operator_class = namespace.get(COMBO_OPERATOR_NAME)
    if not isinstance(operator_class, type):
        raise RuntimeError(f"Failed to load archived class {COMBO_OPERATOR_NAME}")

    verification = {
        "operator_name": COMBO_OPERATOR_NAME,
        "library": str(OPERATOR_LIBRARY_PATH),
        "status": record.get("status", "candidate"),
        "version": record.get("version", 1),
        "installed": bool(installed),
        "module": module_name,
        "class_sha256": class_sha256,
        "prompt_sha256": prompt_sha256,
        "artifact_sha256": artifact_sha256,
        "declared_class_sha256": declared_class_sha,
        "declared_prompt_sha256": declared_prompt_sha,
        "declared_artifact_sha256": declared_artifact_sha,
        "class_ast_matches_template": class_ast_matches,
        "template_prompts_match_artifact": template_prompts_match,
        "execution_source": "isolated_archived_artifact",
    }
    return operator_class, verification


class DirectOperator:
    def __init__(
        self,
        config_name: str,
        llm_config: Any,
        combo_factory: type | None = None,
    ):
        operator_module = importlib.import_module(
            "workspace.MATH.workflows.template.operator"
        )

        self.config_name = config_name
        self.llm = create_llm_instance(llm_config)
        self.custom = operator_module.Custom(self.llm)
        self.geo = None
        self.combo = None
        if config_name == "evolved_geo":
            self.geo = operator_module.GeometricCombinatorialDecomposer(self.llm)
        if config_name == "evolved_combo":
            if combo_factory is None:
                raise RuntimeError("evolved_combo requires a verified library artifact")
            self.combo = combo_factory(self.llm)
        self.contextual_router = None
        if config_name == "contextual_router":
            from scripts.contextual_operator_router import ContextAwareOperatorRouter

            operator_factories = (
                {COMBO_OPERATOR_NAME: combo_factory} if combo_factory else None
            )
            self.contextual_router = ContextAwareOperatorRouter(
                llm=self.llm,
                library_path=str(OPERATOR_LIBRARY_PATH),
                operator_module="workspace.MATH.workflows.template.operator",
                min_score=0.70,
                operator_factories=operator_factories,
            )

    async def __call__(self, problem: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        route_metadata: dict[str, Any] = {}
        if self.config_name == "generic_two_stage":
            analysis = await self.custom(
                input=problem, instruction=GENERIC_ANALYSIS_PROMPT
            )
            second_input = (
                f"Problem:\n{problem}\n\nAnalysis:\n"
                f"{analysis.get('response', '')}"
            )
            result = await self.custom(
                input=second_input, instruction=GENERIC_SOLVE_PROMPT
            )
        elif self.config_name == "evolved_geo":
            assert self.geo is not None
            result = await self.geo(problem=problem)
        elif self.config_name == "evolved_combo":
            assert self.combo is not None
            result = await self.combo(problem=problem)
        elif self.config_name == "contextual_router":
            assert self.contextual_router is not None
            result = await self.contextual_router(problem)
            route = result.get("route", {})
            route_selected = (route.get("selected") or {}).get("operator_name", "")
            route_metadata = {
                "selected_operator": result.get("selected_operator"),
                "route_selected_operator": route_selected,
                "used_fallback": result.get("used_fallback", True),
                "fallback_reason": result.get("fallback_reason", ""),
                "route": route,
            }
        else:
            raise ValueError(f"Unknown config: {self.config_name}")

        return result.get("response", ""), self.llm.get_usage_summary(), route_metadata


def exact_mcnemar_p(wins: int, losses: int) -> float:
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, k) for k in range(min(wins, losses) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def _row_budget_compliant(row: dict[str, Any]) -> bool:
    value = row.get("budget_compliant")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    try:
        return int(row.get("call_count", -1)) == EXPECTED_CALL_COUNT
    except (TypeError, ValueError):
        return False


def _row_bool(row: dict[str, Any], key: str) -> bool:
    value = row.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _row_route(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("route", {})
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _route_metadata_available(rows: list[dict[str, Any]]) -> bool:
    return any(
        bool(row.get("selected_operator"))
        or bool(row.get("route_selected_operator"))
        or isinstance(row.get("used_fallback"), bool)
        or bool(_row_route(row))
        for row in rows
    )


def _slice_predicates() -> dict[str, Any]:
    return {
        # A specialist selected by the routing decision.  A recovery row can
        # appear in both selected and fallback, which is intentional.
        "selected": lambda row: bool(
            row.get("route_selected_operator") or row.get("selected_operator")
        ),
        "fallback": lambda row: _row_bool(row, "used_fallback"),
        "hard_boundary": lambda row: _row_bool(row, "hard_boundary")
        or bool(_row_route(row).get("blocked")),
    }


def summarize_route_slices(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not _route_metadata_available(rows):
        return {}
    output: dict[str, Any] = {}
    for name, predicate in _slice_predicates().items():
        selected = [row for row in rows if predicate(row)]
        if not selected:
            output[name] = {
                "rows": 0,
                "correct": 0,
                "accuracy": None,
                "eligible_rows": 0,
                "eligible_correct": 0,
                "eligible_accuracy": None,
                "errors": 0,
                "budget_violations": 0,
                "mean_calls": None,
            }
            continue
        eligible = [
            row
            for row in selected
            if not row.get("error") and _row_budget_compliant(row)
        ]
        output[name] = {
            "rows": len(selected),
            "correct": sum(int(row["score"]) for row in selected),
            "accuracy": statistics.mean(int(row["score"]) for row in selected),
            "eligible_rows": len(eligible),
            "eligible_correct": sum(int(row["score"]) for row in eligible),
            "eligible_accuracy": (
                statistics.mean(int(row["score"]) for row in eligible)
                if eligible
                else None
            ),
            "errors": sum(bool(row.get("error")) for row in selected),
            "budget_violations": sum(
                not _row_budget_compliant(row) for row in selected
            ),
            "mean_calls": statistics.mean(
                int(row.get("call_count", 0) or 0) for row in selected
            ),
        }
    return output


def _paired_metrics(
    pairs: list[tuple[int, dict[str, Any], dict[str, Any]]]
) -> dict[str, Any]:
    wins = ties = losses = 0
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for _, baseline_row, candidate_row in pairs:
        kind = str(baseline_row.get("type", "unknown"))
        baseline_score = int(baseline_row["score"])
        candidate_score = int(candidate_row["score"])
        if candidate_score > baseline_score:
            wins += 1
            by_type[kind][0] += 1
        elif candidate_score < baseline_score:
            losses += 1
            by_type[kind][2] += 1
        else:
            ties += 1
            by_type[kind][1] += 1
    baseline_accuracy = (
        statistics.mean(int(row[1]["score"]) for row in pairs) if pairs else None
    )
    candidate_accuracy = (
        statistics.mean(int(row[2]["score"]) for row in pairs) if pairs else None
    )
    return {
        "included_rows": len(pairs),
        "baseline_accuracy": baseline_accuracy,
        "candidate_accuracy": candidate_accuracy,
        "delta": (
            candidate_accuracy - baseline_accuracy
            if baseline_accuracy is not None and candidate_accuracy is not None
            else None
        ),
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "mcnemar_exact_p": exact_mcnemar_p(wins, losses) if pairs else None,
        "by_type_wtl": {
            key: {"wins": value[0], "ties": value[1], "losses": value[2]}
            for key, value in sorted(by_type.items())
        },
    }


async def evaluate_config(
    config_name: str,
    run_index: int,
    data: list[tuple[int, dict[str, Any]]],
    benchmark: MATHBenchmark,
    llm_config: Any,
    concurrency: int,
    timeout: float,
    output_root: str,
    combo_factory: type | None,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_one(index: int, item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            operator: DirectOperator | None = None
            try:
                operator = DirectOperator(config_name, llm_config, combo_factory)
                prediction, usage, route_metadata = await asyncio.wait_for(
                    operator(item["problem"]), timeout=timeout
                )
                score, extracted = benchmark.calculate_score(
                    item["solution"], prediction
                )
                error = ""
            except Exception as exc:
                prediction, extracted, score = "", "", 0
                try:
                    usage = (
                        operator.llm.get_usage_summary()
                        if operator is not None
                        else {
                            "total_input_tokens": 0,
                            "total_output_tokens": 0,
                            "total_tokens": 0,
                            "total_cost": 0.0,
                            "call_count": 0,
                        }
                    )
                except Exception:
                    usage = {
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "total_tokens": 0,
                        "total_cost": 0.0,
                        "call_count": 0,
                    }
                error = f"{type(exc).__name__}: {exc}"
                route_metadata = {}

            try:
                call_count = int(usage.get("call_count", 0) or 0)
            except (TypeError, ValueError):
                call_count = -1
            budget_compliant = call_count == EXPECTED_CALL_COUNT
            fallback_reason = str(route_metadata.get("fallback_reason", "") or "")
            route = route_metadata.get("route", {}) or {}
            hard_boundary = bool(route.get("blocked"))
            route_selected_operator = str(
                route_metadata.get("route_selected_operator", "") or ""
            )
            specialist_recovery = bool(
                route_metadata.get("used_fallback") is True
                and fallback_reason.startswith("specialist_")
            )

            return {
                "index": index,
                "problem_id": stable_problem_id(item["problem"]),
                "type": item.get("type", "unknown"),
                "level": item.get("level", "unknown"),
                "score": int(score),
                "prediction": prediction,
                "extracted": extracted,
                "input_tokens": usage.get("total_input_tokens", 0),
                "output_tokens": usage.get("total_output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "call_count": call_count,
                "budget_compliant": budget_compliant,
                "budget_violation_reason": (
                    "" if budget_compliant else f"expected_2_calls_observed_{call_count}"
                ),
                "cost": usage.get("total_cost", 0.0),
                "selected_operator": route_metadata.get("selected_operator") or "",
                "route_selected_operator": route_selected_operator,
                "used_fallback": route_metadata.get("used_fallback", ""),
                "fallback_reason": fallback_reason,
                "specialist_recovery": specialist_recovery,
                "hard_boundary": hard_boundary,
                "route": json.dumps(route, ensure_ascii=False),
                "error": error,
            }

    rows = await asyncio.gather(
        *(evaluate_one(index, item) for index, item in data)
    )
    score = statistics.mean(row["score"] for row in rows)
    result = {
        "config": config_name,
        "run": run_index + 1,
        "score": score,
        "correct": sum(row["score"] for row in rows),
        "total": len(rows),
        "errors": sum(bool(row["error"]) for row in rows),
        "budget_compliant_rows": sum(row["budget_compliant"] for row in rows),
        "budget_violations": sum(not row["budget_compliant"] for row in rows),
        "specialist_recoveries": sum(row["specialist_recovery"] for row in rows),
        "total_tokens": sum(row["total_tokens"] for row in rows),
        "call_count": sum(row["call_count"] for row in rows),
        "rows": rows,
    }

    run_dir = os.path.join(output_root, config_name)
    os.makedirs(run_dir, exist_ok=True)
    csv_path = os.path.join(run_dir, f"run_{run_index + 1}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result["csv"] = csv_path
    print(
        f"{config_name} run={run_index + 1}: "
        f"{result['correct']}/{result['total']}={score:.5f}, "
        f"errors={result['errors']}, budget_ok={result['budget_compliant_rows']}/"
        f"{result['total']}, recoveries={result['specialist_recoveries']}, "
        f"calls={result['call_count']}, "
        f"tokens={result['total_tokens']}"
    )
    return result


def compare_rows(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    base = {row["index"]: row for row in baseline["rows"]}
    cand = {row["index"]: row for row in candidate["rows"]}
    eligible_pairs: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    error_indices: list[int] = []
    budget_excluded_indices: list[int] = []
    budget_violations: list[dict[str, Any]] = []
    for index in sorted(set(base) & set(cand)):
        baseline_row = base[index]
        candidate_row = cand[index]
        baseline_budget_ok = _row_budget_compliant(baseline_row)
        candidate_budget_ok = _row_budget_compliant(candidate_row)
        if not baseline_budget_ok or not candidate_budget_ok:
            budget_violations.append(
                {
                    "index": index,
                    "baseline_call_count": baseline_row.get("call_count", 0),
                    "candidate_call_count": candidate_row.get("call_count", 0),
                    "baseline_budget_compliant": baseline_budget_ok,
                    "candidate_budget_compliant": candidate_budget_ok,
                    "candidate_specialist_recovery": _row_bool(
                        candidate_row, "specialist_recovery"
                    ),
                }
            )
        if baseline_row.get("error") or candidate_row.get("error"):
            error_indices.append(index)
            continue
        if not baseline_budget_ok or not candidate_budget_ok:
            budget_excluded_indices.append(index)
            continue
        eligible_pairs.append((index, baseline_row, candidate_row))

    primary = _paired_metrics(eligible_pairs)
    result = {
        "run": baseline["run"],
        "baseline": baseline["config"],
        "candidate": candidate["config"],
        "analysis_population": "paired rows with no error and exactly 2 calls on both sides",
        **primary,
        "raw_delta_all_rows": candidate["score"] - baseline["score"],
        "common_rows": len(set(base) & set(cand)),
        "excluded_errors": len(error_indices),
        "excluded_error_indices": error_indices,
        "excluded_budget_violations": len(budget_excluded_indices),
        "excluded_budget_indices": budget_excluded_indices,
        "budget_violation_rows": budget_violations,
    }
    if _route_metadata_available(list(cand.values())):
        slices: dict[str, Any] = {}
        for slice_name, predicate in _slice_predicates().items():
            slice_pairs = [pair for pair in eligible_pairs if predicate(pair[2])]
            slice_metrics = _paired_metrics(slice_pairs)
            slice_metrics["candidate_slice_rows_all"] = sum(
                predicate(row) for row in cand.values()
            )
            slice_metrics["candidate_slice_budget_violations"] = sum(
                predicate(row) and not _row_budget_compliant(row)
                for row in cand.values()
            )
            slice_metrics["candidate_slice_errors"] = sum(
                predicate(row) and bool(row.get("error")) for row in cand.values()
            )
            slices[slice_name] = slice_metrics
        result["slice_comparisons"] = slices
    return result


def _run_analysis_self_check() -> None:
    baseline = {
        "config": "generic_two_stage",
        "run": 1,
        "score": 0.5,
        "rows": [
            {
                "index": 1,
                "type": "test",
                "score": 0,
                "call_count": 2,
                "budget_compliant": True,
                "error": "",
            },
            {
                "index": 2,
                "type": "test",
                "score": 1,
                "call_count": 2,
                "budget_compliant": True,
                "error": "",
            },
        ],
    }
    candidate = {
        "config": "contextual_router",
        "run": 1,
        "score": 1.0,
        "rows": [
            {
                "index": 1,
                "type": "test",
                "score": 1,
                "call_count": 2,
                "budget_compliant": True,
                "selected_operator": "Specialist",
                "route_selected_operator": "Specialist",
                "used_fallback": False,
                "hard_boundary": False,
                "route": {"selected": {"operator_name": "Specialist"}},
                "error": "",
            },
            {
                "index": 2,
                "type": "test",
                "score": 1,
                "call_count": 4,
                "budget_compliant": False,
                "selected_operator": "",
                "route_selected_operator": "Specialist",
                "used_fallback": True,
                "specialist_recovery": True,
                "hard_boundary": False,
                "route": {"selected": {"operator_name": "Specialist"}},
                "error": "",
            },
        ],
    }
    comparison = compare_rows(baseline, candidate)
    assert comparison["included_rows"] == 1
    assert comparison["excluded_budget_violations"] == 1
    assert comparison["wins"] == 1
    assert comparison["slice_comparisons"]["selected"]["included_rows"] == 1
    assert comparison["slice_comparisons"]["fallback"]["included_rows"] == 0
    assert comparison["slice_comparisons"]["hard_boundary"]["included_rows"] == 0


async def main(args: argparse.Namespace) -> None:
    _run_analysis_self_check()
    print("Analysis self-check: OK")

    combo_factory: type | None = None
    artifact_verification: dict[str, Any] | None = None
    if any(name in {"evolved_combo", "contextual_router"} for name in args.configs):
        combo_factory, artifact_verification = prepare_combo_artifact()
        print(
            "Artifact preflight: "
            f"{artifact_verification['operator_name']} "
            f"status={artifact_verification['status']} "
            f"class_sha256={artifact_verification['class_sha256'][:12]} "
            f"prompt_sha256={artifact_verification['prompt_sha256'][:12]} "
            f"template_prompt_match="
            f"{artifact_verification['template_prompts_match_artifact']}"
        )

    model_config = LLMsConfig.default().get(args.model)
    if model_config is None:
        raise SystemExit(f"Unknown model configuration: {args.model}")
    benchmark = MATHBenchmark(
        name="MATH",
        file_path=args.data,
        log_path=os.path.join(args.output_dir, "mismatches"),
    )
    raw_data = await benchmark.load_data()
    data_path = Path(args.data)
    provenance = {
        "dataset": {
            "path": _display_path(data_path),
            "sha256": _file_sha256(data_path),
            "row_count": len(raw_data),
        },
        "scorer": {
            "class": "benchmarks.math.MATHBenchmark",
            "path": _display_path(MATH_SCORER_PATH),
            "sha256": _file_sha256(MATH_SCORER_PATH),
        },
        "operator_library": {
            "path": _display_path(OPERATOR_LIBRARY_PATH),
            "sha256": _file_sha256(OPERATOR_LIBRARY_PATH),
        },
        "experiment_script": {
            "path": _display_path(Path(__file__)),
            "sha256": _file_sha256(Path(__file__)),
        },
        "problem_id": {
            "field": "problem_id",
            "algorithm": (
                "first 16 hex characters of SHA256 over lower-cased, "
                "whitespace-normalized problem text"
            ),
        },
    }
    indexed_data = list(enumerate(raw_data))
    if args.indices:
        requested_indices = set(args.indices)
        indexed_data = [
            (index, item)
            for index, item in indexed_data
            if index in requested_indices
        ]
    if args.types:
        requested_types = set(args.types)
        indexed_data = [
            (index, item)
            for index, item in indexed_data
            if item.get("type") in requested_types
        ]
    if args.sample_size:
        indexed_data = indexed_data[: args.sample_size]
    if not indexed_data:
        raise SystemExit("No problems matched the requested filters")

    os.makedirs(args.output_dir, exist_ok=True)
    print(
        f"Dataset={args.data}, matched={len(indexed_data)}, "
        f"types={args.types or 'ALL'}, model={args.model}"
    )
    print(
        "Primary paired analysis requires exactly 2 calls on both sides; "
        "budget violations are excluded and reported separately."
    )
    print(
        "No ensemble. Contextual specialist recovery may spend up to 4 calls "
        "(specialist attempt plus generic two-stage fallback)."
    )

    results = []
    for run_index in range(args.num_runs):
        # Alternate order to reduce time/order bias across repeated runs.
        order = list(args.configs)
        if run_index % 2:
            order.reverse()
        for config_name in order:
            results.append(
                await evaluate_config(
                    config_name,
                    run_index,
                    indexed_data,
                    benchmark,
                    model_config,
                    args.concurrency,
                    args.timeout,
                    args.output_dir,
                    combo_factory,
                )
            )

    aggregate = {}
    for name in args.configs:
        selected = [result for result in results if result["config"] == name]
        scores = [result["score"] for result in selected]
        all_rows = [row for result in selected for row in result["rows"]]
        eligible_rows = [
            row
            for row in all_rows
            if not row.get("error") and _row_budget_compliant(row)
        ]
        aggregate[name] = {
            "label": CONFIGS[name],
            "raw_mean_all_rows": statistics.mean(scores),
            "raw_std_all_rows": statistics.stdev(scores) if len(scores) > 1 else 0.0,
            "mean": statistics.mean(scores),
            "std": statistics.stdev(scores) if len(scores) > 1 else 0.0,
            "scores": scores,
            "budget_policy": "exactly_2_calls_and_no_error",
            "eligible_rows": len(eligible_rows),
            "eligible_correct": sum(int(row["score"]) for row in eligible_rows),
            "eligible_accuracy": (
                statistics.mean(int(row["score"]) for row in eligible_rows)
                if eligible_rows
                else None
            ),
            "total_rows": len(all_rows),
            "total_budget_violations": sum(
                not _row_budget_compliant(row) for row in all_rows
            ),
            "total_specialist_recoveries": sum(
                _row_bool(row, "specialist_recovery") for row in all_rows
            ),
            "mean_tokens": statistics.mean(
                result["total_tokens"] for result in selected
            ),
            "mean_calls": statistics.mean(
                result["call_count"] for result in selected
            ),
            "mean_specialist_selections": statistics.mean(
                sum(
                    bool(
                        row.get("route_selected_operator")
                        or row.get("selected_operator")
                    )
                    for row in result["rows"]
                )
                for result in selected
            ),
            "mean_fallbacks": statistics.mean(
                sum(row["used_fallback"] is True for row in result["rows"])
                for result in selected
            ),
            "total_errors": sum(result["errors"] for result in selected),
            "route_slices": summarize_route_slices(all_rows),
        }

    comparisons = []
    if "generic_two_stage" in args.configs:
        for run_index in range(1, args.num_runs + 1):
            run_results = {
                result["config"]: result
                for result in results
                if result["run"] == run_index
            }
            for candidate_name in ("evolved_geo", "evolved_combo", "contextual_router"):
                if candidate_name in run_results:
                    comparisons.append(
                        compare_rows(
                            run_results["generic_two_stage"],
                            run_results[candidate_name],
                        )
                    )

    report_path = os.path.join(args.output_dir, "summary.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "arguments": vars(args),
                "provenance": provenance,
                "analysis_policy": {
                    "expected_call_count_per_row": EXPECTED_CALL_COUNT,
                    "primary_paired_population": (
                        "Rows with no execution error and call_count == 2 for "
                        "both baseline and candidate"
                    ),
                    "budget_violations": (
                        "Excluded from primary paired metrics and listed under "
                        "budget_violation_rows"
                    ),
                    "specialist_recovery": (
                        "A contextual specialist may fail after spending calls, "
                        "then execute the 2-call generic fallback; such rows can "
                        "reach 4 calls and are not equal-budget rows"
                    ),
                    "route_slices": (
                        "selected, fallback, and hard_boundary are independent "
                        "slices; specialist recovery can belong to selected and "
                        "fallback simultaneously"
                    ),
                },
                "artifact_verification": artifact_verification,
                "matched_indices": [index for index, _ in indexed_data],
                "aggregate": aggregate,
                "paired_comparisons": comparisons,
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )

    print("\nSummary")
    for name, item in aggregate.items():
        eligible_accuracy = item["eligible_accuracy"]
        eligible_text = (
            f"{eligible_accuracy:.5f}" if eligible_accuracy is not None else "N/A"
        )
        print(
            f"{name}: raw={item['raw_mean_all_rows']:.5f} ± "
            f"{item['raw_std_all_rows']:.5f}; "
            f"eligible={eligible_text} ({item['eligible_rows']}/"
            f"{item['total_rows']} rows); "
            f"budget_violations={item['total_budget_violations']}; "
            f"recoveries={item['total_specialist_recoveries']}; "
            f"mean_tokens={item['mean_tokens']:.0f}; "
            f"selected={item['mean_specialist_selections']:.1f}; "
            f"fallbacks={item['mean_fallbacks']:.1f}; "
            f"errors={item['total_errors']}"
        )
    for item in comparisons:
        delta = item["delta"]
        delta_text = f"{delta:+.5f}" if delta is not None else "N/A"
        p_value = item["mcnemar_exact_p"]
        p_text = f"{p_value:.4f}" if p_value is not None else "N/A"
        print(
            f"run={item['run']} {item['candidate']} vs generic_two_stage: "
            f"eligible={item['included_rows']}, delta={delta_text}, "
            f"W/T/L={item['wins']}/{item['ties']}/{item['losses']}, "
            f"p={p_text}, errors={item['excluded_errors']}, "
            f"budget_excluded={item['excluded_budget_violations']}"
        )
    print(f"Saved: {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-runs", type=int, default=3)
    parser.add_argument("--sample-size", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--data", default="data/datasets/math_validate.jsonl")
    parser.add_argument(
        "--indices",
        nargs="+",
        type=int,
        help="Evaluate only these zero-based dataset indices.",
    )
    parser.add_argument(
        "--output-dir", default="workspace/MATH/operator_direct_v1"
    )
    parser.add_argument("--types", nargs="+")
    parser.add_argument(
        "--configs", nargs="+", choices=sorted(CONFIGS), required=True
    )
    asyncio.run(main(parser.parse_args()))
