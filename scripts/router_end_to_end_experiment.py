# -*- coding: utf-8 -*-
"""Promote a frozen specialist and run a preregistered routed-AFlow study.

The formal dataset mixes fresh generated positive cases with untouched MATH
test problems and generated semantic near misses.  Candidate code and routing
metadata are frozen before the mixed dataset is written.  The execution stage
writes its protocol before loading any hidden row and can run only once.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from benchmarks.math import MATHBenchmark
from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.contextual_operator_router import (
    ContextAwareOperatorRouter,
    GenericTwoStageSolver,
)
from scripts.operator_library import OperatorLibrary, ProblemFeatureExtractor, stable_problem_id
from scripts.operator_tournament import OperatorTournament
from scripts.parameterized_tools import (
    PARENTHESIZATION_MECHANISM,
    ParameterizedToolRegistry,
    ParenthesizationDistinctValueTool,
)
from scripts.trajectory_failure_expander import file_sha256
from scripts.baseline_failure_mechanisms_v2 import _unique_rows


OPERATOR_NAME = "ReusableParenthesizationIntervalDPTool"
RULE_NAME = "parenthesization_distinct_value_dedup_v1"
EXPERIMENT_ROLE = "blind_end_to_end_contextual_router_comparison"
GENERATOR_VERSION = "mixed_math_router_hidden_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"malformed JSONL row {line_number}: {path}")
        rows.append(value)
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _candidate_entry(summary: Mapping[str, Any]) -> Dict[str, Any]:
    entries = [
        row for row in summary.get("ranking", [])
        if row.get("operator_name") == OPERATOR_NAME
    ]
    if len(entries) != 1:
        raise ValueError(f"comparison must contain exactly one {OPERATOR_NAME}")
    return dict(entries[0])


def _verify_promotion_evidence(
    *, comparison_summary_path: Path, library: OperatorLibrary
) -> Dict[str, Any]:
    summary = _read_json(comparison_summary_path)
    if summary.get("experiment_role") != "blind_track_comparison":
        raise ValueError("promotion source is not a blind track comparison")
    if summary.get("primary_metric") != "correct_within_timeout":
        raise ValueError("promotion source used a different primary metric")
    if summary.get("winner") != OPERATOR_NAME:
        raise ValueError(f"{OPERATOR_NAME} did not win the comparison")
    entry = _candidate_entry(summary)
    metrics = entry.get("metrics", {})
    accuracy = entry.get("accuracy", {})
    required = {
        "passed": metrics.get("passed") is True,
        "no_losses": int(metrics.get("losses", -1)) == 0,
        "no_regression_losses": int(metrics.get("regression_losses", -1)) == 0,
        "no_errors": int(metrics.get("errors", -1)) == 0,
        "all_target_correct": int(accuracy.get("target_correct", -1))
        == int(accuracy.get("target_total", -2)),
        "all_regression_correct": int(accuracy.get("regression_correct", -1))
        == int(accuracy.get("regression_total", -2)),
        "enough_unique_wins": int(metrics.get("unique_winning_problems", 0)) >= 2,
    }
    failed = [name for name, passed in required.items() if not passed]
    if failed:
        raise ValueError("comparison is not promotion-safe: " + ", ".join(failed))

    protocol_path = Path(str(summary.get("protocol", ""))).resolve()
    details_path = Path(str(summary.get("details_path", ""))).resolve()
    if not protocol_path.is_file() or not details_path.is_file():
        raise ValueError("comparison protocol/details are missing")
    if file_sha256(protocol_path) != summary.get("protocol_sha256"):
        raise ValueError("comparison protocol changed after evaluation")
    protocol = _read_json(protocol_path)
    pinned_tool_runtime = protocol.get("runtime_component_sha256", {}).get(
        "parameterized_tools"
    )
    current_tool_runtime = file_sha256(Path(__file__).with_name("parameterized_tools.py"))
    if not pinned_tool_runtime or pinned_tool_runtime != current_tool_runtime:
        raise ValueError(
            "parameterized tool runtime differs from the blind comparison revision"
        )
    expected_artifact = protocol.get("candidate_artifact_sha256", {}).get(OPERATOR_NAME)
    record = library.get(OPERATOR_NAME)
    if not record:
        raise ValueError(f"operator library is missing {OPERATOR_NAME}")
    actual_artifact = record.get("artifact", {}).get("artifact_sha256")
    if not expected_artifact or actual_artifact != expected_artifact:
        raise ValueError("candidate artifact differs from preregistered comparison")

    rows = [
        row for row in entry.get("rows", [])
        if row.get("operator_name", OPERATOR_NAME) == OPERATOR_NAME
    ]
    if not rows:
        # Compact summaries still have a canonical detailed file.
        rows = [
            row for row in _read_jsonl(details_path)
            if row.get("operator_name") == OPERATOR_NAME
        ]
    if not rows:
        raise ValueError("comparison has no candidate observations")
    if any(
        int(row.get("candidate_score", 0)) != 1
        or row.get("candidate_error")
        or row.get("declined")
        or int(row.get("candidate_calls", -1)) != 0
        or (row.get("candidate_tool_trace") or {}).get("route")
        != "reusable_parameterized_tool"
        for row in rows
    ):
        raise ValueError("saved candidate observations fail reusable-tool audit")
    return {
        "summary": summary,
        "entry": entry,
        "record": record,
        "protocol": protocol,
        "protocol_path": protocol_path,
        "details_path": details_path,
        "summary_sha256": file_sha256(comparison_summary_path),
        "details_sha256": file_sha256(details_path),
    }


def _promote_and_install(
    *, evidence: Mapping[str, Any], library: OperatorLibrary, template_dir: Path
) -> Dict[str, Any]:
    entry = evidence["entry"]
    metrics = entry["metrics"]
    record = evidence["record"]
    forbidden = [
        "geometry_problem",
        "probability_problem",
        "continuous_probability",
        "number_theory_problem",
        "parametric_curve",
        "trajectory_trace",
        "distance_traveled_request",
    ]
    library.upsert(
        {
            "name": OPERATOR_NAME,
            "applicability_rules": [
                {
                    "name": RULE_NAME,
                    "description": (
                        "Count distinct numerical results over every full "
                        "parenthesization of a fixed +, -, * expression."
                    ),
                    "required_features": [PARENTHESIZATION_MECHANISM],
                    "forbidden_features": forbidden,
                    "weight": 0.82,
                    "min_observations": 6,
                    "min_net_wins": 2,
                    "min_unique_problems": 2,
                    "min_unique_winning_problems": 2,
                }
            ],
            "failure_boundaries": [
                {
                    "name": "outside_exact_parenthesization_scope",
                    "features_any": forbidden,
                    "hard_block": True,
                    "reason": "The exact interval-DP tool is not defined for this domain.",
                }
            ],
            "provenance": {
                **record.get("provenance", {}),
                "promotion_comparison_id": evidence["summary"]["comparison_id"],
                "promotion_summary_sha256": evidence["summary_sha256"],
                "promotion_details_sha256": evidence["details_sha256"],
                "routing_rule": RULE_NAME,
            },
        },
        audit_event="operator_routing_scope_narrowed_before_promotion",
    )
    evidence_item = library.record_evidence(
        OPERATOR_NAME,
        {
            "id": f"blind-cwit-{evidence['summary']['comparison_id']}",
            "source": str(evidence["details_path"]),
            "source_summary": str(
                Path(evidence["summary"]["details_path"]).with_name(
                    "comparison_summary.json"
                )
            ),
            "scope": RULE_NAME,
            "dataset": "MATH",
            "baseline": "generic_two_stage",
            "protocol": "paired_correct_within_timeout_blind_v1",
            "routing_eligible": True,
            "wins": int(metrics["wins"]),
            "ties": int(metrics["ties"]),
            "losses": int(metrics["losses"]),
            "unique_problems": int(metrics["unique_target_problems"]),
            "unique_winning_problems": int(metrics["unique_winning_problems"]),
            "comparison_summary_sha256": evidence["summary_sha256"],
            "comparison_details_sha256": evidence["details_sha256"],
            "primary_metric": "correct_within_timeout",
        },
    )
    library.set_status(
        OPERATOR_NAME,
        "experimental",
        "Passed fresh-seed blind correct-within-timeout comparison; admitted for narrow routed evaluation.",
    )
    library.install_artifact(OPERATOR_NAME, template_dir)
    promoted = library.get(OPERATOR_NAME)
    decision = library.retrieve(
        "Consider the expression 2 + 3 * 4 - 5. Fully parenthesize it in every "
        "possible way. How many distinct values are possible?"
    )
    if not decision.selected or decision.selected.operator_name != OPERATOR_NAME:
        raise RuntimeError("promoted operator is still not retrievable")
    return {"record": promoted, "evidence": evidence_item, "decision": decision.to_dict()}


def _occupied_mechanism_ids(workspace_root: Path) -> set[str]:
    occupied: set[str] = set()
    roots = [
        workspace_root / "failure_mechanisms",
        workspace_root / "baseline_mechanism_calibration",
        workspace_root / "baseline_mechanism_screening",
        workspace_root / "operational_headroom",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                rows = _read_jsonl(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for row in rows:
                problem_id = row.get("mechanism_metadata", {}).get("problem_id")
                if problem_id:
                    occupied.add(str(problem_id))
    return occupied


def _decorate_positive(row: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(row)
    metadata = dict(result.get("mechanism_metadata", {}))
    metadata.update(
        {
            "mixed_split": "specialist_positive",
            "expected_route": OPERATOR_NAME,
            "visible_to_operator_generator": False,
        }
    )
    result["mechanism_metadata"] = metadata
    return result


def _sample_math_negatives(
    *, path: Path, seed: int, count: int, excluded_ids: set[str]
) -> list[Dict[str, Any]]:
    if count <= 0:
        return []
    extractor = ProblemFeatureExtractor()
    candidates = []
    for row in _read_jsonl(path):
        problem = str(row.get("problem", ""))
        problem_id = stable_problem_id(problem)
        if problem_id in excluded_ids:
            continue
        context = extractor.extract(problem)
        if PARENTHESIZATION_MECHANISM in context.features:
            continue
        candidates.append((problem_id, row))
    grouped: Dict[str, list[tuple[str, Dict[str, Any]]]] = defaultdict(list)
    for item in candidates:
        grouped[str(item[1].get("type", "Unknown"))].append(item)
    rng = random.Random(seed)
    types = sorted(grouped)
    for values in grouped.values():
        rng.shuffle(values)
    selected: list[tuple[str, Dict[str, Any]]] = []
    cursor = {name: 0 for name in types}
    while len(selected) < count:
        progressed = False
        for name in types:
            index = cursor[name]
            if index < len(grouped[name]) and len(selected) < count:
                selected.append(grouped[name][index])
                cursor[name] += 1
                progressed = True
        if not progressed:
            break
    if len(selected) != count:
        raise ValueError(f"not enough eligible MATH negatives: {len(selected)} < {count}")
    output = []
    for problem_id, source in selected:
        row = dict(source)
        row["mechanism_metadata"] = {
            "family": "unrelated_math_test",
            "generator_version": GENERATOR_VERSION,
            "problem_id": problem_id,
            "origin": "untouched_math_test_seeded_sample",
            "source_type": row.get("type", "Unknown"),
            "mixed_split": "non_specialist_negative",
            "expected_route": "generic_two_stage_fallback",
            "visible_to_operator_generator": False,
        }
        output.append(row)
    return output


def _all_values(numbers: Sequence[int], operators: Sequence[str]) -> set[int]:
    n = len(numbers)
    table = [[set() for _ in range(n)] for _ in range(n)]
    for index, value in enumerate(numbers):
        table[index][index].add(value)
    for length in range(2, n + 1):
        for start in range(n - length + 1):
            end = start + length - 1
            for split in range(start, end):
                for left in table[start][split]:
                    for right in table[split + 1][end]:
                        op = operators[split]
                        table[start][end].add(
                            left + right if op == "+" else left - right if op == "-" else left * right
                        )
    return table[0][n - 1]


def _near_miss_rows(*, seed: int, count: int) -> list[Dict[str, Any]]:
    if count <= 0:
        return []
    rng = random.Random(seed)
    rows = []
    templates = ("maximum", "minimum", "forms", "explicit_negative")
    for ordinal in range(count):
        operand_count = rng.choice((5, 6, 7))
        numbers = [rng.randint(2, 9) for _ in range(operand_count)]
        operators = [rng.choice(("+", "-", "*")) for _ in range(operand_count - 1)]
        expression = " ".join(
            str(numbers[i]) + (f" {operators[i]} " if i < len(operators) else "")
            for i in range(len(numbers))
        ).strip()
        kind = templates[ordinal % len(templates)]
        values = _all_values(numbers, operators)
        if kind == "maximum":
            answer = max(values)
            problem = (
                f"Consider the expression {expression}. Fully parenthesize it in every "
                "possible way without changing the order. What is the largest numerical "
                "value obtainable?"
            )
        elif kind == "minimum":
            answer = min(values)
            problem = (
                f"Consider the expression {expression}. Fully parenthesize it in every "
                "possible way without changing the order. What is the smallest numerical "
                "value obtainable?"
            )
        elif kind == "forms":
            answer = math.comb(2 * (operand_count - 1), operand_count - 1) // operand_count
            problem = (
                f"The expression {expression} has {operand_count} operands. How many full "
                "parenthesizations are possible? Count parenthesization forms separately "
                "even when they produce the same value."
            )
        else:
            answer = math.comb(2 * (operand_count - 1), operand_count - 1) // operand_count
            problem = (
                f"The expression {expression} has {operand_count} operands. How many full "
                "parenthesizations are possible? Do not count distinct values; count every "
                "parenthesization form separately."
            )
        problem_id = stable_problem_id(problem)
        rows.append(
            {
                "problem": problem,
                "solution": (
                    "This is a semantic routing boundary case. The requested quantity is "
                    f"computed exactly and equals $\\boxed{{{answer}}}$."
                ),
                "level": "Level 5",
                "type": "Counting & Probability",
                "mechanism_metadata": {
                    "family": "parenthesization_semantic_near_miss",
                    "generator_version": GENERATOR_VERSION,
                    "problem_id": problem_id,
                    "origin": "generated_exact_oracle",
                    "ordinal": ordinal,
                    "near_miss_kind": kind,
                    "oracle_answer": str(answer),
                    "parameters": {"numbers": numbers, "operators": operators},
                    "mixed_split": "non_specialist_near_miss",
                    "expected_route": "generic_two_stage_fallback",
                    "visible_to_operator_generator": False,
                },
            }
        )
    return rows


def prepare(
    *, workspace_root: Path, comparison_summary: Path, output_dir: Path,
    math_test: Path, math_validate: Path, seed: int, positive_count: int,
    negative_count: int, near_miss_count: int, exec_model: str,
    num_runs: int, timeout: float,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"router experiment already exists: {output_dir}")
    if positive_count < 8 or negative_count < 8 or near_miss_count < 2:
        raise ValueError("use at least 8 positives, 8 MATH negatives, and 2 near misses")
    if num_runs < 2:
        raise ValueError("formal comparison requires at least two runs")
    library_path = workspace_root / "operator_library" / "operators.json"
    template_dir = workspace_root / "workflows" / "template"
    library = OperatorLibrary(library_path, auto_create=False)
    evidence = _verify_promotion_evidence(
        comparison_summary_path=comparison_summary, library=library
    )

    # Freeze the executable, route scope, generation seed, sources, and gates
    # before materializing any formal hidden example.
    promoted = _promote_and_install(
        evidence=evidence, library=library, template_dir=template_dir
    )
    promoted_record = promoted["record"]
    execution_protocol = {
        "primary_metric": "correct_within_timeout",
        "methods": ["generic_two_stage_aflow", "contextually_routed_aflow"],
        "exec_model": exec_model,
        "num_runs": num_runs,
        "timeout_seconds": timeout,
        "timeout_counts_as_failure": True,
        "formal_reruns_allowed": 1,
    }
    success_gates = {
        "overall_accuracy_strictly_above_baseline": True,
        "specialist_accuracy_at_least": 0.95,
        "positive_route_recall": 1.0,
        "negative_false_route_rate": 0.0,
        "negative_accuracy_delta_at_least": -0.05,
        "routing_attributable_negative_regressions": 0,
    }
    output_dir.mkdir(parents=True)
    promotion_audit_path = output_dir / "promotion_audit.json"
    promotion_audit = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "operator_name": OPERATOR_NAME,
        "status": promoted_record["status"],
        "operator_version": promoted_record["version"],
        "artifact_sha256": promoted_record["artifact"]["artifact_sha256"],
        "operator_record_sha256": _json_sha256(promoted_record),
        "source_comparison": str(comparison_summary.resolve()),
        "source_comparison_sha256": evidence["summary_sha256"],
        "source_details": str(evidence["details_path"]),
        "source_details_sha256": evidence["details_sha256"],
        "source_protocol": str(evidence["protocol_path"]),
        "source_protocol_sha256": file_sha256(evidence["protocol_path"]),
        "routing_rule": RULE_NAME,
        "routing_evidence_id": promoted["evidence"]["id"],
        "installation_target": str(template_dir.resolve()),
        "installed_operator_runtime_sha256": file_sha256(template_dir / "operator.py"),
    }
    _write_json(promotion_audit_path, promotion_audit)
    preparation_protocol_path = output_dir / "preparation_protocol.json"
    _write_json(
        preparation_protocol_path,
        {
            "schema_version": 1,
            "status": "frozen_before_hidden_generation",
            "created_at": _utc_now(),
            "seed": seed,
            "counts": {
                "positive": positive_count,
                "negative_math": negative_count,
                "negative_near_miss": near_miss_count,
            },
            "operator_record_sha256": _json_sha256(promoted_record),
            "operator_artifact_sha256": promoted_record["artifact"]["artifact_sha256"],
            "routing_rule": RULE_NAME,
            "promotion_audit_sha256": file_sha256(promotion_audit_path),
            "source_hashes": {
                "math_test": file_sha256(math_test),
                "math_validate_development_only": file_sha256(math_validate),
            },
            "execution": execution_protocol,
            "success_gates": success_gates,
            "generator_runtime_sha256": file_sha256(Path(__file__).resolve()),
        },
    )

    occupied = _occupied_mechanism_ids(workspace_root)
    prior_occupied = set(occupied)
    positives = [
        _decorate_positive(row)
        for row in _unique_rows(
            PARENTHESIZATION_MECHANISM,
            seed=seed + 100003,
            count=positive_count,
            split="mixed_hidden_positive",
            occupied=occupied,
            difficulty="hard",
        )
    ]
    validate_ids = {
        stable_problem_id(str(row["problem"])) for row in _read_jsonl(math_validate)
    }
    positive_ids = {
        str(row["mechanism_metadata"]["problem_id"]) for row in positives
    }
    negatives = _sample_math_negatives(
        path=math_test,
        seed=seed + 200003,
        count=negative_count,
        excluded_ids=validate_ids | positive_ids,
    )
    near_misses = _near_miss_rows(seed=seed + 300007, count=near_miss_count)
    rows = [*positives, *negatives, *near_misses]
    ids = [str(row["mechanism_metadata"]["problem_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("mixed hidden dataset contains duplicate problem ids")
    if positive_ids & prior_occupied:
        raise ValueError("fresh positives overlap prior mechanism data")
    scorer = MATHBenchmark("MATH", "", "")
    registry = ParameterizedToolRegistry([PARENTHESIZATION_MECHANISM])
    extractor = ProblemFeatureExtractor()
    for row in positives:
        context = extractor.extract(str(row["problem"]))
        if PARENTHESIZATION_MECHANISM not in context.features:
            raise ValueError("positive case is not recognized by frozen routing feature")
        tool_result = registry.solve(str(row["problem"]))
        score, _ = scorer.calculate_score(str(row["solution"]), tool_result.response)
        if tool_result.status != "success" or int(score) != 1:
            raise ValueError("fresh positive oracle/tool consistency check failed")
    for row in [*negatives, *near_misses]:
        context = extractor.extract(str(row["problem"]))
        if PARENTHESIZATION_MECHANISM in context.features:
            raise ValueError("negative case would trigger the specialist before evaluation")

    dataset_path = output_dir / "mixed_hidden.jsonl"
    _write_jsonl(dataset_path, rows)
    manifest = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "experiment_role": EXPERIMENT_ROLE,
        "status": "prepared_unopened",
        "created_at": _utc_now(),
        "seed": seed,
        "operator": {
            "name": OPERATOR_NAME,
            "version": promoted_record["version"],
            "artifact_sha256": promoted_record["artifact"]["artifact_sha256"],
            "record_sha256": _json_sha256(promoted_record),
            "routing_rule": RULE_NAME,
        },
        "promotion": {
            "audit": promotion_audit_path.name,
            "audit_sha256": file_sha256(promotion_audit_path),
        },
        "preparation_protocol": {
            "path": preparation_protocol_path.name,
            "sha256": file_sha256(preparation_protocol_path),
        },
        "hidden_dataset": {
            "path": dataset_path.name,
            "sha256": file_sha256(dataset_path),
            "count": len(rows),
            "positive_count": len(positives),
            "negative_math_count": len(negatives),
            "negative_near_miss_count": len(near_misses),
            "overlap_with_prior_mechanism_rows": 0,
            "overlap_with_math_validate_119": 0,
            "visible_to_operator_generator": False,
        },
        "source_data": {
            "math_test": str(math_test.resolve()),
            "math_test_sha256": file_sha256(math_test),
            "math_validate_development_only": str(math_validate.resolve()),
            "math_validate_sha256": file_sha256(math_validate),
        },
        "preregistered_execution": execution_protocol,
        "preregistered_success_gates": success_gates,
        "freeze_policy": {
            "do_not_tune_on_this_hidden_set": True,
            "a_failed_run_requires_a_new_seed": True,
            "math_validate_119_is_development_evidence_only": True,
        },
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def audit_development_routing(*, workspace_root: Path, data_path: Path) -> Dict[str, Any]:
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    rows = _read_jsonl(data_path)
    selected = []
    for index, row in enumerate(rows):
        decision = library.retrieve(str(row["problem"]))
        if decision.selected:
            selected.append(
                {
                    "index": index,
                    "problem_id": stable_problem_id(str(row["problem"])),
                    "operator_name": decision.selected.operator_name,
                    "score": decision.selected.score,
                }
            )
    return {
        "dataset": str(data_path.resolve()),
        "development_only": True,
        "rows": len(rows),
        "selected_count": len(selected),
        "selected": selected,
    }


def _method_stats(rows: Sequence[Mapping[str, Any]], prefix: str) -> Dict[str, Any]:
    total = len(rows)
    correct = sum(int(row[f"{prefix}_score"]) for row in rows)
    timeouts = sum(bool(row[f"{prefix}_timeout"]) for row in rows)
    errors = sum(bool(row[f"{prefix}_error"]) for row in rows)
    return {
        "correct": correct,
        "total": total,
        "accuracy": correct / total if total else 0.0,
        "timeouts": timeouts,
        "timeout_rate": timeouts / total if total else 0.0,
        "errors": errors,
        "error_rate": errors / total if total else 0.0,
        "tokens": sum(int(row[f"{prefix}_tokens"]) for row in rows),
        "calls": sum(int(row[f"{prefix}_calls"]) for row in rows),
    }


class EndToEndRunner:
    def __init__(
        self, *, workspace_root: Path, model: str, timeout: float, concurrency: int,
        candidate_name: str = "",
    ):
        self.workspace_root = workspace_root
        config = LLMsConfig.default().get(model)
        if config is None:
            raise ValueError(f"unknown model: {model}")
        self.llm_config = config
        self.timeout = timeout
        self.semaphore = asyncio.Semaphore(concurrency)
        self.operator_module = "workspace.MATH.workflows.template.operator"
        self.library_path = workspace_root / "operator_library" / "operators.json"
        self.scorer = MATHBenchmark("MATH", "", str(workspace_root))
        self.candidate_name = candidate_name
        self.candidate_factory = None
        if candidate_name:
            tournament = OperatorTournament(
                library_path=self.library_path,
                template_dir=workspace_root / "workflows" / "template",
                llm_config=self.llm_config,
                timeout=timeout,
                concurrency=concurrency,
            )
            self.candidate_factory = tournament._load_candidate_factory(candidate_name)

    async def _candidate_routed_call(
        self, *, llm: Any, fallback: Any, problem: str
    ) -> Dict[str, Any]:
        """Evaluate a frozen candidate route without making it production-routable."""
        library = OperatorLibrary(self.library_path, auto_create=False)
        record = library.get(self.candidate_name) or {}
        context = library.extractor.extract(problem)
        boundaries = [
            boundary for boundary in record.get("failure_boundaries", [])
            if library._boundary_match(boundary, context)
            and boundary.get("hard_block", True)
        ]
        rules = [
            rule for rule in record.get("applicability_rules", [])
            if library._rule_match(rule, context)
        ]
        selected = bool(rules and not boundaries)
        route = {
            "evaluation_only_candidate_route": True,
            "context": context.to_dict(),
            "selected": (
                {"operator_name": self.candidate_name, "matched_rule": max(
                    rules, key=library._rule_weight
                ).get("name", "default")}
                if selected else None
            ),
            "blocked": boundaries,
        }
        if not selected:
            result = await fallback(problem)
            return {
                **result,
                "selected_operator": None,
                "used_fallback": True,
                "fallback_reason": "candidate_no_safe_match",
                "route": route,
            }
        operator = self.candidate_factory(llm)
        try:
            result = await operator(problem=problem)
        except Exception as exc:
            result = {
                "status": "error",
                "response": "",
                "observation": f"{type(exc).__name__}: {exc}",
            }
        if not isinstance(result, dict):
            result = {"status": "success", "response": str(result)}
        if result.get("status") != "success" or not result.get("response"):
            fallback_result = await fallback(problem)
            return {
                **fallback_result,
                "selected_operator": None,
                "used_fallback": True,
                "fallback_reason": "candidate_specialist_failed_or_declined",
                "route": route,
                "candidate_attempt": result,
            }
        return {
            **result,
            "selected_operator": self.candidate_name,
            "used_fallback": False,
            "fallback_reason": "",
            "route": route,
        }

    async def _execute(self, row: Mapping[str, Any], method: str) -> Dict[str, Any]:
        async with self.semaphore:
            llm = create_llm_instance(self.llm_config)
            try:
                fallback = GenericTwoStageSolver(llm, self.operator_module)
                if method == "baseline":
                    solver: Any = fallback
                elif self.candidate_factory is not None:
                    solver = lambda value: self._candidate_routed_call(
                        llm=llm, fallback=fallback, problem=value
                    )
                else:
                    solver = ContextAwareOperatorRouter(
                        llm=llm,
                        library_path=str(self.library_path),
                        operator_module=self.operator_module,
                        min_score=0.70,
                        fallback=fallback,
                    )
                result = await asyncio.wait_for(
                    solver(str(row["problem"])), timeout=self.timeout
                )
                if not isinstance(result, dict):
                    result = {"status": "success", "response": str(result)}
                response = str(result.get("response", "") or "")
                status = str(result.get("status", "success"))
                error = "" if status == "success" else str(
                    result.get("observation", status)
                )
                timeout_hit = "timeouterror" in error.lower()
            except asyncio.TimeoutError:
                result = {}
                response = ""
                error = "TimeoutError"
                timeout_hit = True
            except Exception as exc:
                result = {}
                response = ""
                error = f"{type(exc).__name__}: {exc}"
                timeout_hit = False
            score, extracted = self.scorer.calculate_score(
                str(row["solution"]), response
            )
            usage = llm.get_usage_summary()
            route = result.get("route", {})
            route_selected = (
                route.get("selected", {}).get("operator_name")
                if isinstance(route, dict) and isinstance(route.get("selected"), dict)
                else None
            )
            return {
                "score": int(score),
                "prediction": response,
                "extracted": extracted,
                "error": error,
                "timeout": timeout_hit,
                "tokens": int(usage.get("total_tokens", 0) or 0),
                "calls": int(usage.get("call_count", 0) or 0),
                "cost": float(usage.get("total_cost", 0.0) or 0.0),
                "selected_operator": result.get("selected_operator"),
                "route_selected_operator": route_selected,
                "used_fallback": result.get("used_fallback"),
                "fallback_reason": result.get("fallback_reason", ""),
                "route": route,
                "tool_trace": result.get("tool_trace", {}),
            }

    async def run(
        self, *, rows: Sequence[Mapping[str, Any]], num_runs: int
    ) -> list[Dict[str, Any]]:
        details = []
        for run_index in range(1, num_runs + 1):
            baseline_outputs = await asyncio.gather(
                *(self._execute(row, "baseline") for row in rows)
            )
            routed_outputs = await asyncio.gather(
                *(self._execute(row, "routed") for row in rows)
            )
            for index, (row, baseline, routed) in enumerate(
                zip(rows, baseline_outputs, routed_outputs)
            ):
                metadata = dict(row.get("mechanism_metadata", {}))
                split = str(metadata.get("mixed_split", "unknown"))
                details.append(
                    {
                        "run": run_index,
                        "index": index,
                        "split": split,
                        "problem_id": metadata.get("problem_id")
                        or stable_problem_id(str(row["problem"])),
                        "problem": row["problem"],
                        "solution": row["solution"],
                        "metadata": metadata,
                        **{f"baseline_{key}": value for key, value in baseline.items()},
                        **{f"routed_{key}": value for key, value in routed.items()},
                    }
                )
        return details


def summarize(
    details: Sequence[Mapping[str, Any]], gates: Mapping[str, Any],
    expected_operator_name: str = OPERATOR_NAME,
) -> Dict[str, Any]:
    positive = [row for row in details if row["split"] == "specialist_positive"]
    negative = [row for row in details if row["split"] != "specialist_positive"]
    negative_math = [row for row in details if row["split"] == "non_specialist_negative"]
    near_miss = [row for row in details if row["split"] == "non_specialist_near_miss"]
    baseline = _method_stats(details, "baseline")
    routed = _method_stats(details, "routed")
    specialist_baseline = _method_stats(positive, "baseline")
    specialist_routed = _method_stats(positive, "routed")
    negative_baseline = _method_stats(negative, "baseline")
    negative_routed = _method_stats(negative, "routed")
    false_routes = [
        row for row in negative if row.get("routed_route_selected_operator")
    ]
    missed_positive_routes = [
        row for row in positive
        if row.get("routed_selected_operator") != expected_operator_name
        or row.get("routed_used_fallback") is True
    ]
    negative_regressions = [
        row for row in negative
        if int(row["baseline_score"]) == 1 and int(row["routed_score"]) == 0
    ]
    attributable_regressions = [
        row for row in negative_regressions
        if row.get("routed_route_selected_operator")
    ]
    wins = sum(int(row["routed_score"]) > int(row["baseline_score"]) for row in details)
    ties = sum(int(row["routed_score"]) == int(row["baseline_score"]) for row in details)
    losses = sum(int(row["routed_score"]) < int(row["baseline_score"]) for row in details)
    positive_recall = 1.0 - len(missed_positive_routes) / len(positive) if positive else 0.0
    false_route_rate = len(false_routes) / len(negative) if negative else 0.0
    negative_delta = negative_routed["accuracy"] - negative_baseline["accuracy"]
    checks = {
        "overall_accuracy_strictly_above_baseline": routed["accuracy"] > baseline["accuracy"],
        "specialist_accuracy_at_least": specialist_routed["accuracy"]
        >= float(gates["specialist_accuracy_at_least"]),
        "positive_route_recall": positive_recall >= float(gates["positive_route_recall"]),
        "negative_false_route_rate": false_route_rate
        <= float(gates["negative_false_route_rate"]),
        "negative_accuracy_delta_at_least": negative_delta
        >= float(gates["negative_accuracy_delta_at_least"]),
        "routing_attributable_negative_regressions": len(attributable_regressions)
        <= int(gates["routing_attributable_negative_regressions"]),
    }
    return {
        "passed": all(checks.values()),
        "gate_checks": checks,
        "primary_metric": "correct_within_timeout",
        "overall": {"baseline": baseline, "routed_aflow": routed},
        "specialist_questions": {
            "baseline": specialist_baseline,
            "routed_aflow": specialist_routed,
        },
        "non_specialist_questions": {
            "baseline": negative_baseline,
            "routed_aflow": negative_routed,
            "accuracy_delta": negative_delta,
            "regression_observations": len(negative_regressions),
            "unique_regression_problems": len(
                {str(row["problem_id"]) for row in negative_regressions}
            ),
            "routing_attributable_regressions": len(attributable_regressions),
        },
        "routing": {
            "positive_route_recall": positive_recall,
            "positive_missed_routes": len(missed_positive_routes),
            "negative_false_routes": len(false_routes),
            "negative_false_route_rate": false_route_rate,
            "negative_math_observations": len(negative_math),
            "near_miss_observations": len(near_miss),
        },
        "paired": {"wins": wins, "ties": ties, "losses": losses, "net_wins": wins - losses},
    }


async def execute_manifest(
    *, manifest_path: Path, workspace_root: Path, concurrency: int
) -> Path:
    manifest = _read_json(manifest_path)
    if manifest.get("experiment_role") != EXPERIMENT_ROLE:
        raise ValueError("manifest is not a routed AFlow comparison")
    if manifest.get("status") != "prepared_unopened":
        raise ValueError(
            "formal router experiment is single-use; create a new seed after any run"
        )
    output_dir = manifest_path.parent
    summary_path = output_dir / "comparison_summary.json"
    details_path = output_dir / "comparison_details.jsonl"
    protocol_path = output_dir / "protocol.json"
    if summary_path.exists() or details_path.exists() or protocol_path.exists():
        raise FileExistsError("router experiment output already exists")
    dataset_path = output_dir / manifest["hidden_dataset"]["path"]
    promotion_audit_path = output_dir / manifest["promotion"]["audit"]
    preparation_protocol_path = output_dir / manifest["preparation_protocol"]["path"]
    if file_sha256(dataset_path) != manifest["hidden_dataset"]["sha256"]:
        raise ValueError("mixed hidden dataset changed after preparation")
    if file_sha256(promotion_audit_path) != manifest["promotion"]["audit_sha256"]:
        raise ValueError("promotion audit changed after preparation")
    if (
        file_sha256(preparation_protocol_path)
        != manifest["preparation_protocol"]["sha256"]
    ):
        raise ValueError("preparation protocol changed after hidden generation")
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    record = library.get(OPERATOR_NAME)
    if not record or record.get("status") not in {"experimental", "admitted", "active"}:
        raise ValueError("promoted operator is no longer active")
    if (
        record.get("artifact", {}).get("artifact_sha256")
        != manifest["operator"]["artifact_sha256"]
        or _json_sha256(record) != manifest["operator"]["record_sha256"]
    ):
        raise ValueError("promoted operator record changed after preparation")
    execution = manifest["preregistered_execution"]
    protocol = {
        "schema_version": 1,
        "status": "preregistered_before_hidden_rows_load",
        "created_at": _utc_now(),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": file_sha256(manifest_path),
        "hidden_dataset_sha256": manifest["hidden_dataset"]["sha256"],
        "operator": manifest["operator"],
        "promotion": manifest["promotion"],
        "preparation_protocol": manifest["preparation_protocol"],
        "execution": execution,
        "success_gates": manifest["preregistered_success_gates"],
        "runtime_sha256": {
            "router_experiment": file_sha256(Path(__file__).resolve()),
            "contextual_router": file_sha256(
                Path(__file__).with_name("contextual_operator_router.py")
            ),
            "operator_library": file_sha256(
                Path(__file__).with_name("operator_library.py")
            ),
            "parameterized_tools": file_sha256(
                Path(__file__).with_name("parameterized_tools.py")
            ),
            "math_scorer": file_sha256(
                Path(__file__).resolve().parents[1] / "benchmarks" / "math.py"
            ),
            "installed_operator_runtime": file_sha256(
                workspace_root / "workflows" / "template" / "operator.py"
            ),
        },
    }
    _write_json(protocol_path, protocol)
    # The hidden examples are loaded only after protocol.json is durable.
    rows = _read_jsonl(dataset_path)
    runner = EndToEndRunner(
        workspace_root=workspace_root,
        model=str(execution["exec_model"]),
        timeout=float(execution["timeout_seconds"]),
        concurrency=concurrency,
    )
    details = await runner.run(rows=rows, num_runs=int(execution["num_runs"]))
    _write_jsonl(details_path, details)
    metrics = summarize(details, manifest["preregistered_success_gates"])
    summary = {
        "schema_version": 1,
        "experiment_role": EXPERIMENT_ROLE,
        "created_at": _utc_now(),
        "seed": manifest["seed"],
        "passed": metrics["passed"],
        "metrics": metrics,
        "protocol": str(protocol_path.resolve()),
        "protocol_sha256": file_sha256(protocol_path),
        "details": str(details_path.resolve()),
        "details_sha256": file_sha256(details_path),
        "scorer_version": MATHBenchmark.SCORER_VERSION,
        "operator": manifest["operator"],
        "hidden_dataset": manifest["hidden_dataset"],
        "promotion": manifest["promotion"],
        "promotion_or_install_performed_during_evaluation": False,
    }
    _write_json(summary_path, summary)
    manifest["status"] = "completed_frozen"
    manifest["completed_at"] = _utc_now()
    manifest["result"] = {
        "summary": str(summary_path.resolve()),
        "summary_sha256": file_sha256(summary_path),
        "details": str(details_path.resolve()),
        "details_sha256": file_sha256(details_path),
        "passed": metrics["passed"],
    }
    _write_json(manifest_path, manifest)
    return summary_path


def _print_summary(path: Path) -> None:
    summary = _read_json(path)
    metrics = summary["metrics"]
    overall = metrics["overall"]
    specialist = metrics["specialist_questions"]
    negative = metrics["non_specialist_questions"]
    routing = metrics["routing"]
    print(f"End-to-end router summary: {path.resolve()}")
    print(f"Passed: {metrics['passed']}")
    print(
        "Overall correct: baseline="
        f"{overall['baseline']['correct']}/{overall['baseline']['total']}, "
        f"routed={overall['routed_aflow']['correct']}/{overall['routed_aflow']['total']}"
    )
    print(
        "Specialist correct: baseline="
        f"{specialist['baseline']['correct']}/{specialist['baseline']['total']}, "
        f"routed={specialist['routed_aflow']['correct']}/{specialist['routed_aflow']['total']}"
    )
    print(
        "Non-specialist regressions: "
        f"{negative['regression_observations']} observations / "
        f"{negative['unique_regression_problems']} unique problems; "
        f"routing-attributable={negative['routing_attributable_regressions']}"
    )
    print(
        "False route rate: "
        f"{routing['negative_false_routes']}/"
        f"{routing['negative_math_observations'] + routing['near_miss_observations']} "
        f"({routing['negative_false_route_rate']:.3f})"
    )
    print(
        "Timeouts: baseline="
        f"{overall['baseline']['timeouts']}/{overall['baseline']['total']}, "
        f"routed={overall['routed_aflow']['timeouts']}/{overall['routed_aflow']['total']}"
    )
    paired = metrics["paired"]
    print(f"Paired W/T/L={paired['wins']}/{paired['ties']}/{paired['losses']}")
    print("This seed is frozen and cannot be rerun or tuned.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--workspace-root", default="workspace/MATH")
    prepare_parser.add_argument("--comparison-summary", required=True)
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--math-test", default="data/datasets/math_test.jsonl")
    prepare_parser.add_argument("--math-validate", default="data/datasets/math_validate.jsonl")
    prepare_parser.add_argument("--seed", type=int, required=True)
    prepare_parser.add_argument("--positive-count", type=int, default=12)
    prepare_parser.add_argument("--negative-count", type=int, default=24)
    prepare_parser.add_argument("--near-miss-count", type=int, default=8)
    prepare_parser.add_argument("--exec-model", default="deepseek-v4-flash")
    prepare_parser.add_argument("--num-runs", type=int, default=3)
    prepare_parser.add_argument("--timeout", type=float, default=180.0)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--workspace-root", default="workspace/MATH")
    run_parser.add_argument("--manifest", required=True)
    run_parser.add_argument("--concurrency", type=int, default=4)
    audit_parser = sub.add_parser("audit-development-routing")
    audit_parser.add_argument("--workspace-root", default="workspace/MATH")
    audit_parser.add_argument("--data", default="data/datasets/math_validate.jsonl")
    audit_parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    resolve = lambda value: Path(value) if Path(value).is_absolute() else repo_root / value
    if args.stage == "prepare":
        path = prepare(
            workspace_root=resolve(args.workspace_root),
            comparison_summary=resolve(args.comparison_summary),
            output_dir=resolve(args.output_dir),
            math_test=resolve(args.math_test),
            math_validate=resolve(args.math_validate),
            seed=args.seed,
            positive_count=args.positive_count,
            negative_count=args.negative_count,
            near_miss_count=args.near_miss_count,
            exec_model=args.exec_model,
            num_runs=args.num_runs,
            timeout=args.timeout,
        )
        print(f"Routed AFlow mixed hidden manifest: {path.resolve()}")
        print(f"Promoted and installed: {OPERATOR_NAME}")
        print("The candidate and routing rule are frozen; do not inspect or tune on this seed.")
    elif args.stage == "audit-development-routing":
        report = audit_development_routing(
            workspace_root=resolve(args.workspace_root), data_path=resolve(args.data)
        )
        output = resolve(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(output, report)
        print(f"Development-only routing audit: {output.resolve()}")
        print(f"Selected {report['selected_count']}/{report['rows']} rows")
    else:
        path = asyncio.run(
            execute_manifest(
                manifest_path=resolve(args.manifest),
                workspace_root=resolve(args.workspace_root),
                concurrency=args.concurrency,
            )
        )
        _print_summary(path)


if __name__ == "__main__":
    main()
