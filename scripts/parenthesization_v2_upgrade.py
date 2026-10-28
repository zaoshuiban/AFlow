# -*- coding: utf-8 -*-
"""Version-2 upgrade and fresh hidden confirmation for the reusable DP tool."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from benchmarks.math import MATHBenchmark
from scripts.baseline_failure_mechanisms_v2 import _unique_rows
from scripts.operator_library import OperatorLibrary, ProblemFeatureExtractor, stable_problem_id
from scripts.parameterized_tools import (
    PARENTHESIZATION_MECHANISM,
    ParameterizedToolRegistry,
)
from scripts.router_end_to_end_experiment import (
    GENERATOR_VERSION as MIXED_V1_GENERATOR,
    OPERATOR_NAME,
    EndToEndRunner,
    _json_sha256,
    _near_miss_rows,
    _occupied_mechanism_ids,
    _read_json,
    _read_jsonl,
    _sample_math_negatives,
    _utc_now,
    _write_json,
    _write_jsonl,
    summarize,
)
from scripts.trajectory_failure_expander import file_sha256


RULE_NAME_V2 = "parenthesization_distinct_value_dedup_v2"
UPGRADE_VERSION = "parenthesization_reusable_tool_v2"
CONFIRMATION_ROLE = "blind_end_to_end_candidate_router_confirmation_v2"


def _class_code(tool_runtime_sha256: str) -> str:
    return f'''class ReusableParenthesizationIntervalDPTool(Operator):
    operator_revision = 2
    tool_runtime_sha256 = "{tool_runtime_sha256}"
    _required_features = ("parenthesization_distinct_value_dedup",)
    _blocked_features = (
        "geometry_problem", "probability_problem", "continuous_probability",
        "number_theory_problem", "parametric_curve", "trajectory_trace",
        "distance_traveled_request",
    )

    def __init__(self, llm: AsyncLLM, name: str = "ReusableParenthesizationIntervalDPTool"):
        super().__init__(llm, name)

    def _preflight(self, problem: str):
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
            return {{
                "status": "declined", "response": "",
                "observation": "specialist_not_applicable:" + ";".join(reasons),
            }}
        return None

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        from scripts.parameterized_tools import (
            PARENTHESIZATION_MECHANISM, ParameterizedToolRegistry,
        )
        result = ParameterizedToolRegistry(
            [PARENTHESIZATION_MECHANISM]
        ).solve(problem)
        trace = {{
            "route": "reusable_parameterized_tool_v2",
            "operator_revision": self.operator_revision,
            "tool_runtime_sha256": self.tool_runtime_sha256,
            **result.to_dict(),
        }}
        if result.status == "success":
            return {{
                "status": "success", "response": result.response,
                "observation": "", "tool_trace": trace,
            }}
        return {{
            "status": "declined", "response": "",
            "observation": result.observation or result.status,
            "tool_trace": trace,
        }}'''


def _render_surface(row: Mapping[str, Any], variant: int, split: str) -> Dict[str, Any]:
    result = dict(row)
    metadata = dict(result["mechanism_metadata"])
    numbers = [int(value) for value in metadata["parameters"]["numbers"]]
    operators = [str(value) for value in metadata["parameters"]["operators"]]
    surface = variant % 4
    if surface == 0:
        rendered_ops = operators
        surface_name = "ascii_explicit_distinct"
    elif surface == 1:
        rendered_ops = [r"\cdot" if value == "*" else value for value in operators]
        surface_name = "latex_cdot_explicit_distinct"
    elif surface == 2:
        rendered_ops = [r"\times" if value == "*" else value for value in operators]
        surface_name = "latex_times_natural_inserting"
    else:
        rendered_ops = [
            (r"\cdot" if index % 2 == 0 else r"\times") if value == "*" else value
            for index, value in enumerate(operators)
        ]
        surface_name = "latex_mixed_natural_inserting"
    expression = " ".join(
        str(numbers[index])
        + (f" {rendered_ops[index]} " if index < len(rendered_ops) else "")
        for index in range(len(numbers))
    ).strip()
    if surface < 2:
        problem = (
            f"Consider the expression ${expression}$. Fully parenthesize it in every "
            "possible way without changing the order of numbers or operations. "
            "Parenthesizations with the same numerical result count only once. "
            "How many distinct values are possible?"
        )
    else:
        problem = (
            f"The expression ${expression}$ may be changed only by inserting "
            "parentheses; rearranging numbers or operations is not allowed. In total, "
            "how many values can be obtained?"
        )
    metadata.update(
        {
            "surface_form": surface_name,
            "split": split,
            "mixed_split": "specialist_positive",
            "expected_route": OPERATOR_NAME,
            "visible_to_operator_generator": split == "visible_v2_design",
        }
    )
    metadata["problem_id"] = stable_problem_id(problem)
    result["problem"] = problem
    result["mechanism_metadata"] = metadata
    return result


def _validate_rows(rows: list[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    scorer = MATHBenchmark("MATH", "", "")
    extractor = ProblemFeatureExtractor()
    registry = ParameterizedToolRegistry([PARENTHESIZATION_MECHANISM])
    validations = []
    for row in rows:
        context = extractor.extract(str(row["problem"]))
        tool_result = registry.solve(str(row["problem"]))
        score, extracted = scorer.calculate_score(
            str(row["solution"]), tool_result.response
        )
        validations.append(
            {
                "problem_id": row.get("mechanism_metadata", {}).get("problem_id")
                or stable_problem_id(str(row["problem"])),
                "features": sorted(context.features),
                "status": tool_result.status,
                "score": int(score),
                "extracted": extracted,
                "parameters": tool_result.parameters,
            }
        )
    return validations


def _verify_v1_frozen(v1_manifest_path: Path) -> Dict[str, Any]:
    manifest = _read_json(v1_manifest_path)
    if (
        manifest.get("status") != "completed_frozen"
        or int(manifest.get("seed", -1)) != 110003
        or manifest.get("result", {}).get("passed") is not True
    ):
        raise ValueError("v1 seed 110003 is not a completed frozen passing result")
    summary_path = Path(str(manifest["result"]["summary"])).resolve()
    details_path = Path(str(manifest["result"]["details"])).resolve()
    if (
        file_sha256(summary_path) != manifest["result"]["summary_sha256"]
        or file_sha256(details_path) != manifest["result"]["details_sha256"]
    ):
        raise ValueError("v1 frozen result files changed")
    summary = _read_json(summary_path)
    if summary.get("passed") is not True:
        raise ValueError("v1 summary is not passing")
    return {
        "manifest": str(v1_manifest_path.resolve()),
        "manifest_sha256": file_sha256(v1_manifest_path),
        "summary": str(summary_path),
        "summary_sha256": file_sha256(summary_path),
        "details": str(details_path),
        "details_sha256": file_sha256(details_path),
    }


def materialize(
    *, workspace_root: Path, v1_manifest: Path, math_validate: Path,
    visible_seed: int, output_dir: Path | None = None,
) -> Path:
    frozen_v1 = _verify_v1_frozen(v1_manifest)
    tool_runtime = Path(__file__).with_name("parameterized_tools.py")
    tool_hash = file_sha256(tool_runtime)
    rows119 = _read_jsonl(math_validate)
    if len(rows119) != 119:
        raise ValueError("development MATH validation dataset must contain 119 rows")
    original = dict(rows119[7])
    original["mechanism_metadata"] = {
        "family": PARENTHESIZATION_MECHANISM,
        "problem_id": stable_problem_id(str(original["problem"])),
        "origin": "original_119_development_case",
        "source_index": 7,
        "split": "visible_v2_design",
        "visible_to_operator_generator": True,
    }
    generated = _unique_rows(
        PARENTHESIZATION_MECHANISM,
        seed=visible_seed,
        count=8,
        split="visible_v2_design",
        occupied=set(),
        difficulty="hard",
    )
    visible_rows = [original] + [
        _render_surface(row, index, "visible_v2_design")
        for index, row in enumerate(generated)
    ]
    validations = _validate_rows(visible_rows)
    if any(
        row["status"] != "success"
        or row["score"] != 1
        or PARENTHESIZATION_MECHANISM not in row["features"]
        for row in validations
    ):
        raise RuntimeError("v2 failed visible development validation")

    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    prior = library.get(OPERATOR_NAME)
    if not prior or int(prior.get("version", 0)) != 1:
        raise ValueError("expected the deployed v1 operator before v2 materialization")
    record = library.register_evolved_candidate(
        name=OPERATOR_NAME,
        description=(
            "Version 2 reusable exact interval-DP tool with LaTeX multiplication "
            "and natural inserting-parentheses coverage."
        ),
        module="workspace.MATH.workflows.template.operator",
        skeleton_type="reusable_parameterized_tool_v2",
        applicability_features=PARENTHESIZATION_MECHANISM,
        failure_features=(
            "geometry_problem,probability_problem,continuous_probability,"
            "number_theory_problem,parametric_curve,trajectory_trace,"
            "distance_traveled_request"
        ),
        provenance={
            "kind": "bounded_parser_and_detector_upgrade",
            "dataset": "MATH",
            "mechanism": PARENTHESIZATION_MECHANISM,
            "upgrade_version": UPGRADE_VERSION,
            "prior_operator_version": 1,
            "prior_artifact_sha256": prior["artifact"]["artifact_sha256"],
            "frozen_v1_evidence": frozen_v1,
            "tool_runtime_sha256": tool_hash,
            "visible_development_index": 7,
            "hidden_confirmation_opened": False,
        },
        class_code=_class_code(tool_hash),
        prompt_code="",
    )
    if int(record["version"]) != 2 or record["status"] != "candidate":
        raise RuntimeError("v2 registration did not create an isolated candidate revision")
    artifact = dict(record["artifact"])
    artifact["runtime_dependency"] = "scripts/parameterized_tools.py"
    artifact["runtime_dependency_sha256"] = tool_hash
    library.upsert(
        {"name": OPERATOR_NAME, "artifact": artifact},
        audit_event="v2_runtime_dependency_bound",
    )
    record = library.get(OPERATOR_NAME)
    forbidden = record["applicability_rules"][0].get("forbidden_features", [])
    library.upsert(
        {
            "name": OPERATOR_NAME,
            "applicability_rules": [
                {
                    "name": RULE_NAME_V2,
                    "description": (
                        "Exact distinct-value counting over order-preserving full "
                        "parenthesizations, including ASCII and LaTeX multiplication."
                    ),
                    "required_features": [PARENTHESIZATION_MECHANISM],
                    "forbidden_features": forbidden,
                    "weight": 0.84,
                    "min_observations": 6,
                    "min_net_wins": 2,
                    "min_unique_problems": 2,
                    "min_unique_winning_problems": 2,
                }
            ],
        },
        audit_event="v2_routing_scope_frozen_before_hidden_confirmation",
    )
    record = library.get(OPERATOR_NAME)
    if output_dir is None:
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output_dir = workspace_root / "operator_evolution_batches" / batch_id
    else:
        batch_id = output_dir.name
    if output_dir.exists():
        raise FileExistsError(f"v2 candidate batch already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    validation_path = output_dir / "visible_v2_validation.json"
    _write_json(
        validation_path,
        {
            "scientific_evidence": False,
            "split": "visible_v2_design",
            "original_119_index_7_included": True,
            "correct": sum(row["score"] for row in validations),
            "total": len(validations),
            "rows": validations,
        },
    )
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "created_at": _utc_now(),
        "status": "pending_v2_hidden_confirmation",
        "candidate_names": [OPERATOR_NAME],
        "operator_version": 2,
        "candidate_artifact_sha256": record["artifact"]["artifact_sha256"],
        "operator_record_sha256": _json_sha256(record),
        "tool_runtime_sha256": tool_hash,
        "visible_validation": {
            "path": str(validation_path.resolve()),
            "sha256": file_sha256(validation_path),
            "correct": len(validations),
            "total": len(validations),
        },
        "frozen_v1": frozen_v1,
        "promotion_allowed": False,
        "installed": False,
        "routed": False,
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def _occupied_with_prior_router(workspace_root: Path) -> set[str]:
    occupied = _occupied_mechanism_ids(workspace_root)
    root = workspace_root / "router_end_to_end"
    if root.exists():
        for path in root.rglob("mixed_hidden.jsonl"):
            for row in _read_jsonl(path):
                problem_id = row.get("mechanism_metadata", {}).get("problem_id")
                if problem_id:
                    occupied.add(str(problem_id))
    return occupied


def prepare_confirmation(
    *, workspace_root: Path, batch_manifest_path: Path, output_dir: Path,
    math_test: Path, math_validate: Path, seed: int, positive_count: int,
    negative_count: int, near_miss_count: int, exec_model: str,
    num_runs: int, timeout: float,
) -> Path:
    if seed == 110003:
        raise ValueError("v2 confirmation must not reuse frozen v1 seed 110003")
    if output_dir.exists():
        raise FileExistsError(f"v2 confirmation already exists: {output_dir}")
    batch = _read_json(batch_manifest_path)
    if batch.get("status") != "pending_v2_hidden_confirmation":
        raise ValueError("v2 batch is not pending hidden confirmation")
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    record = library.get(OPERATOR_NAME)
    if (
        not record
        or record.get("status") != "candidate"
        or int(record.get("version", 0)) != 2
        or record.get("artifact", {}).get("artifact_sha256")
        != batch["candidate_artifact_sha256"]
        or _json_sha256(record) != batch["operator_record_sha256"]
    ):
        raise ValueError("v2 candidate changed after materialization")
    if file_sha256(Path(__file__).with_name("parameterized_tools.py")) != batch[
        "tool_runtime_sha256"
    ]:
        raise ValueError("v2 parameterized tool runtime changed")
    gates = {
        "overall_accuracy_strictly_above_baseline": True,
        "specialist_accuracy_at_least": 0.95,
        "positive_route_recall": 1.0,
        "negative_false_route_rate": 0.0,
        "negative_accuracy_delta_at_least": -0.05,
        "routing_attributable_negative_regressions": 0,
    }
    execution = {
        "primary_metric": "correct_within_timeout",
        "methods": ["generic_two_stage_aflow", "candidate_v2_routed_aflow"],
        "exec_model": exec_model,
        "num_runs": num_runs,
        "timeout_seconds": timeout,
        "timeout_counts_as_failure": True,
        "formal_reruns_allowed": 1,
    }
    output_dir.mkdir(parents=True)
    preparation_path = output_dir / "preparation_protocol.json"
    _write_json(
        preparation_path,
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
            "batch_manifest": str(batch_manifest_path.resolve()),
            "batch_manifest_sha256": file_sha256(batch_manifest_path),
            "candidate_artifact_sha256": batch["candidate_artifact_sha256"],
            "operator_record_sha256": batch["operator_record_sha256"],
            "tool_runtime_sha256": batch["tool_runtime_sha256"],
            "execution": execution,
            "success_gates": gates,
            "generator_runtime_sha256": file_sha256(Path(__file__).resolve()),
            "prior_seed_forbidden": 110003,
        },
    )
    occupied = _occupied_with_prior_router(workspace_root)
    prior_occupied = set(occupied)
    base_rows = _unique_rows(
        PARENTHESIZATION_MECHANISM,
        seed=seed + 100003,
        count=positive_count,
        split="v2_hidden_positive",
        occupied=occupied,
        difficulty="hard",
    )
    positives = [
        _render_surface(row, index, "v2_hidden_positive")
        for index, row in enumerate(base_rows)
    ]
    positive_ids = {
        str(row["mechanism_metadata"]["problem_id"]) for row in positives
    }
    if positive_ids & prior_occupied:
        raise ValueError("v2 hidden positives overlap prior mechanism data")
    validations = _validate_rows(positives)
    if any(
        row["status"] != "success"
        or row["score"] != 1
        or PARENTHESIZATION_MECHANISM not in row["features"]
        for row in validations
    ):
        raise RuntimeError("v2 hidden generator/tool consistency failed")
    validate_ids = {
        stable_problem_id(str(row["problem"])) for row in _read_jsonl(math_validate)
    }
    negatives = _sample_math_negatives(
        path=math_test,
        seed=seed + 200003,
        count=negative_count,
        excluded_ids=validate_ids | positive_ids,
    )
    near_misses = _near_miss_rows(seed=seed + 300007, count=near_miss_count)
    extractor = ProblemFeatureExtractor()
    for row in [*negatives, *near_misses]:
        if PARENTHESIZATION_MECHANISM in extractor.extract(str(row["problem"])).features:
            raise RuntimeError("v2 negative would activate candidate route")
    rows = [*positives, *negatives, *near_misses]
    ids = [str(row["mechanism_metadata"]["problem_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise RuntimeError("v2 hidden mixture contains duplicate problem ids")
    dataset_path = output_dir / "mixed_hidden_v2.jsonl"
    _write_jsonl(dataset_path, rows)
    manifest = {
        "schema_version": 1,
        "experiment_role": CONFIRMATION_ROLE,
        "status": "prepared_unopened",
        "created_at": _utc_now(),
        "seed": seed,
        "candidate": {
            "name": OPERATOR_NAME,
            "version": 2,
            "artifact_sha256": batch["candidate_artifact_sha256"],
            "record_sha256": batch["operator_record_sha256"],
            "tool_runtime_sha256": batch["tool_runtime_sha256"],
            "routing_rule": RULE_NAME_V2,
            "production_routable_during_confirmation": False,
        },
        "batch_manifest": str(batch_manifest_path.resolve()),
        "batch_manifest_sha256": file_sha256(batch_manifest_path),
        "preparation_protocol": {
            "path": preparation_path.name,
            "sha256": file_sha256(preparation_path),
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
        "execution": execution,
        "success_gates": gates,
        "freeze_policy": {
            "do_not_tune_on_this_hidden_set": True,
            "a_failed_run_requires_a_new_seed": True,
            "seed_110003_forbidden": True,
        },
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


async def run_confirmation(
    *, workspace_root: Path, manifest_path: Path, concurrency: int
) -> Path:
    manifest = _read_json(manifest_path)
    if manifest.get("experiment_role") != CONFIRMATION_ROLE:
        raise ValueError("not a v2 hidden confirmation manifest")
    if manifest.get("status") != "prepared_unopened":
        raise ValueError("v2 hidden seed is single-use and already opened")
    output_dir = manifest_path.parent
    protocol_path = output_dir / "protocol.json"
    details_path = output_dir / "comparison_details.jsonl"
    summary_path = output_dir / "comparison_summary.json"
    if any(path.exists() for path in (protocol_path, details_path, summary_path)):
        raise FileExistsError("v2 confirmation output already exists")
    preparation_path = output_dir / manifest["preparation_protocol"]["path"]
    dataset_path = output_dir / manifest["hidden_dataset"]["path"]
    if file_sha256(preparation_path) != manifest["preparation_protocol"]["sha256"]:
        raise ValueError("v2 preparation protocol changed")
    if file_sha256(dataset_path) != manifest["hidden_dataset"]["sha256"]:
        raise ValueError("v2 hidden dataset changed")
    batch_path = Path(str(manifest["batch_manifest"])).resolve()
    if file_sha256(batch_path) != manifest["batch_manifest_sha256"]:
        raise ValueError("v2 batch manifest changed before confirmation")
    batch = _read_json(batch_path)
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    record = library.get(OPERATOR_NAME)
    if (
        not record
        or record.get("status") != "candidate"
        or int(record.get("version", 0)) != 2
        or record.get("artifact", {}).get("artifact_sha256")
        != manifest["candidate"]["artifact_sha256"]
        or _json_sha256(record) != manifest["candidate"]["record_sha256"]
    ):
        raise ValueError("v2 candidate changed before confirmation")
    if file_sha256(Path(__file__).with_name("parameterized_tools.py")) != manifest[
        "candidate"
    ]["tool_runtime_sha256"]:
        raise ValueError("v2 tool runtime changed before confirmation")
    protocol = {
        "schema_version": 1,
        "status": "preregistered_before_hidden_rows_load",
        "created_at": _utc_now(),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": file_sha256(manifest_path),
        "preparation_protocol_sha256": manifest["preparation_protocol"]["sha256"],
        "hidden_dataset_sha256": manifest["hidden_dataset"]["sha256"],
        "candidate": manifest["candidate"],
        "execution": manifest["execution"],
        "success_gates": manifest["success_gates"],
        "candidate_route_mode": "evaluation_only_not_production_routable",
        "runtime_sha256": {
            "v2_upgrade": file_sha256(Path(__file__).resolve()),
            "router_experiment": file_sha256(
                Path(__file__).with_name("router_end_to_end_experiment.py")
            ),
            "operator_library": file_sha256(
                Path(__file__).with_name("operator_library.py")
            ),
            "parameterized_tools": file_sha256(
                Path(__file__).with_name("parameterized_tools.py")
            ),
        },
    }
    _write_json(protocol_path, protocol)
    rows = _read_jsonl(dataset_path)
    execution = manifest["execution"]
    runner = EndToEndRunner(
        workspace_root=workspace_root,
        model=str(execution["exec_model"]),
        timeout=float(execution["timeout_seconds"]),
        concurrency=concurrency,
        candidate_name=OPERATOR_NAME,
    )
    details = await runner.run(rows=rows, num_runs=int(execution["num_runs"]))
    _write_jsonl(details_path, details)
    metrics = summarize(details, manifest["success_gates"])
    summary = {
        "schema_version": 1,
        "experiment_role": CONFIRMATION_ROLE,
        "created_at": _utc_now(),
        "seed": manifest["seed"],
        "passed": metrics["passed"],
        "metrics": metrics,
        "protocol": str(protocol_path.resolve()),
        "protocol_sha256": file_sha256(protocol_path),
        "details": str(details_path.resolve()),
        "details_sha256": file_sha256(details_path),
        "scorer_version": MATHBenchmark.SCORER_VERSION,
        "candidate": manifest["candidate"],
        "hidden_dataset": manifest["hidden_dataset"],
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
    batch["status"] = (
        "v2_hidden_confirmation_passed_not_promoted"
        if metrics["passed"] else "v2_hidden_confirmation_failed_frozen"
    )
    batch["confirmation_summary"] = str(summary_path.resolve())
    batch["confirmation_summary_sha256"] = file_sha256(summary_path)
    _write_json(batch_path, batch)
    return summary_path


def promote(
    *, workspace_root: Path, confirmation_summary_path: Path,
    output_path: Path,
) -> Path:
    if output_path.exists():
        raise FileExistsError(f"v2 promotion audit already exists: {output_path}")
    summary = _read_json(confirmation_summary_path)
    if (
        summary.get("experiment_role") != CONFIRMATION_ROLE
        or summary.get("passed") is not True
        or int(summary.get("seed", -1)) == 110003
        or summary.get("promotion_or_install_performed_during_evaluation") is not False
    ):
        raise ValueError("v2 confirmation is not promotion eligible")
    details_path = Path(str(summary["details"])).resolve()
    protocol_path = Path(str(summary["protocol"])).resolve()
    if (
        file_sha256(details_path) != summary["details_sha256"]
        or file_sha256(protocol_path) != summary["protocol_sha256"]
    ):
        raise ValueError("v2 confirmation evidence changed")
    details = _read_jsonl(details_path)
    positive = [row for row in details if row["split"] == "specialist_positive"]
    negative = [row for row in details if row["split"] != "specialist_positive"]
    if (
        not positive
        or any(
            int(row["routed_score"]) != 1
            or row.get("routed_route_selected_operator") != OPERATOR_NAME
            for row in positive
        )
        or any(row.get("routed_route_selected_operator") for row in negative)
    ):
        raise ValueError("v2 saved routes fail promotion audit")
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    record = library.get(OPERATOR_NAME)
    candidate = summary["candidate"]
    if (
        not record
        or record.get("status") != "candidate"
        or int(record.get("version", 0)) != 2
        or record.get("artifact", {}).get("artifact_sha256")
        != candidate["artifact_sha256"]
        or _json_sha256(record) != candidate["record_sha256"]
    ):
        raise ValueError("v2 candidate revision changed before promotion")
    wins = sum(
        int(row["routed_score"]) > int(row["baseline_score"]) for row in positive
    )
    ties = sum(
        int(row["routed_score"]) == int(row["baseline_score"]) for row in positive
    )
    losses = sum(
        int(row["routed_score"]) < int(row["baseline_score"]) for row in positive
    )
    winning_ids = {
        str(row["problem_id"]) for row in positive
        if int(row["routed_score"]) > int(row["baseline_score"])
    }
    library.record_evidence(
        OPERATOR_NAME,
        {
            "id": f"v2-hidden-cwit-{summary['seed']}",
            "source": str(details_path),
            "source_summary": str(confirmation_summary_path.resolve()),
            "scope": RULE_NAME_V2,
            "dataset": "MATH",
            "baseline": "generic_two_stage",
            "protocol": "paired_correct_within_timeout_v2_hidden_confirmation",
            "routing_eligible": True,
            "wins": wins,
            "ties": ties,
            "losses": losses,
            "unique_problems": len({str(row["problem_id"]) for row in positive}),
            "unique_winning_problems": len(winning_ids),
            "confirmation_summary_sha256": file_sha256(confirmation_summary_path),
            "confirmation_details_sha256": file_sha256(details_path),
        },
    )
    library.set_status(
        OPERATOR_NAME,
        "experimental",
        "Version 2 passed fresh-seed routed hidden confirmation.",
    )
    library.install_artifact(
        OPERATOR_NAME, workspace_root / "workflows" / "template"
    )
    promoted = library.get(OPERATOR_NAME)
    audit = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "operator_name": OPERATOR_NAME,
        "operator_version": 2,
        "status": promoted["status"],
        "artifact_sha256": promoted["artifact"]["artifact_sha256"],
        "tool_runtime_sha256": candidate["tool_runtime_sha256"],
        "confirmation_seed": summary["seed"],
        "confirmation_summary": str(confirmation_summary_path.resolve()),
        "confirmation_summary_sha256": file_sha256(confirmation_summary_path),
        "confirmation_details_sha256": file_sha256(details_path),
        "paired_positive_wtl": {"wins": wins, "ties": ties, "losses": losses},
        "installed": True,
        "production_routable": True,
        "frozen_v1_seed_110003_modified": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output_path, audit)
    protocol = _read_json(protocol_path)
    confirmation_manifest = _read_json(Path(str(protocol["manifest"])).resolve())
    batch_path = Path(str(confirmation_manifest["batch_manifest"])).resolve()
    batch = _read_json(batch_path)
    batch["status"] = "v2_promoted_and_installed"
    batch["promotion_audit"] = str(output_path.resolve())
    batch["promotion_audit_sha256"] = file_sha256(output_path)
    _write_json(batch_path, batch)
    return output_path


def _print_summary(path: Path) -> None:
    summary = _read_json(path)
    metrics = summary["metrics"]
    overall = metrics["overall"]
    specialist = metrics["specialist_questions"]
    negative = metrics["non_specialist_questions"]
    routing = metrics["routing"]
    print(f"V2 confirmation summary: {path.resolve()}")
    print(f"Passed: {metrics['passed']}")
    print(
        f"Overall: baseline={overall['baseline']['correct']}/{overall['baseline']['total']}, "
        f"routed-v2={overall['routed_aflow']['correct']}/{overall['routed_aflow']['total']}"
    )
    print(
        f"Specialist: baseline={specialist['baseline']['correct']}/{specialist['baseline']['total']}, "
        f"routed-v2={specialist['routed_aflow']['correct']}/{specialist['routed_aflow']['total']}"
    )
    print(
        f"Non-specialist regressions={negative['regression_observations']}; "
        f"routing-attributable={negative['routing_attributable_regressions']}"
    )
    print(
        f"False routes={routing['negative_false_routes']}; "
        f"positive route recall={routing['positive_route_recall']:.3f}"
    )
    print(
        f"Timeouts: baseline={overall['baseline']['timeouts']}, "
        f"routed-v2={overall['routed_aflow']['timeouts']}"
    )
    paired = metrics["paired"]
    print(f"Paired W/T/L={paired['wins']}/{paired['ties']}/{paired['losses']}")
    print("V2 confirmation never promotes or installs the candidate.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    materialize_parser = sub.add_parser("materialize")
    materialize_parser.add_argument("--workspace-root", default="workspace/MATH")
    materialize_parser.add_argument("--v1-manifest", required=True)
    materialize_parser.add_argument("--math-validate", default="data/datasets/math_validate.jsonl")
    materialize_parser.add_argument("--visible-seed", type=int, default=119003)
    materialize_parser.add_argument("--output-dir", default="")
    prepare_parser = sub.add_parser("prepare-confirmation")
    prepare_parser.add_argument("--workspace-root", default="workspace/MATH")
    prepare_parser.add_argument("--batch-manifest", required=True)
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
    run_parser = sub.add_parser("run-confirmation")
    run_parser.add_argument("--workspace-root", default="workspace/MATH")
    run_parser.add_argument("--manifest", required=True)
    run_parser.add_argument("--concurrency", type=int, default=4)
    promote_parser = sub.add_parser("promote")
    promote_parser.add_argument("--workspace-root", default="workspace/MATH")
    promote_parser.add_argument("--confirmation-summary", required=True)
    promote_parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    resolve = lambda value: Path(value) if Path(value).is_absolute() else repo_root / value
    if args.stage == "materialize":
        path = materialize(
            workspace_root=resolve(args.workspace_root),
            v1_manifest=resolve(args.v1_manifest),
            math_validate=resolve(args.math_validate),
            visible_seed=args.visible_seed,
            output_dir=resolve(args.output_dir) if args.output_dir else None,
        )
        print(f"V2 candidate batch manifest: {path.resolve()}")
        print("Original MATH index 7 and LaTeX/natural-language visible cases passed.")
        print("V2 remains candidate-only; it is not installed or production-routable.")
    elif args.stage == "prepare-confirmation":
        path = prepare_confirmation(
            workspace_root=resolve(args.workspace_root),
            batch_manifest_path=resolve(args.batch_manifest),
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
        print(f"V2 fresh hidden manifest: {path.resolve()}")
        print("Seed 110003 was not read, regenerated, or modified.")
    elif args.stage == "run-confirmation":
        path = asyncio.run(
            run_confirmation(
                workspace_root=resolve(args.workspace_root),
                manifest_path=resolve(args.manifest),
                concurrency=args.concurrency,
            )
        )
        _print_summary(path)
    else:
        path = promote(
            workspace_root=resolve(args.workspace_root),
            confirmation_summary_path=resolve(args.confirmation_summary),
            output_path=resolve(args.output),
        )
        print(f"V2 promotion audit: {path.resolve()}")
        print(f"Promoted and installed: {OPERATOR_NAME} version 2")


if __name__ == "__main__":
    main()
