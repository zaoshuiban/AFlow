# -*- coding: utf-8 -*-
"""Staged operator evolution: diagnosis -> specs -> tournament -> promotion."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Sequence

from benchmarks.math import MATHBenchmark
from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.operator_tournament import OperatorTournament, load_jsonl
from scripts.optimizer_utils.operator_evolution import OperatorEvolver
from scripts.trajectory_failure_expander import (
    ensure_disjoint_splits,
    file_sha256,
    load_bundle_manifest,
    load_bundle_split,
    render_visible_design_cases,
)


REPO_ROOT = Path(__file__).resolve().parent

PROMPT_ONLY_POLICY = {
    "execution_mode": "prompt_only",
    "programmer_allowed": False,
    "allowed_skeleton_types": ["single_step", "two_stage", "verify_fix"],
    "max_llm_calls_per_problem": 2,
    "policy_violation_action": "reject_candidate_before_tournament",
}

TOOL_AUGMENTED_POLICY = {
    "execution_mode": "tool_augmented",
    "programmer_allowed": True,
    "allowed_skeleton_types": ["programmer_step"],
    "max_llm_calls_per_problem": 1,
    "tool": "python_programmer",
    "tool_result_and_code_must_be_saved": True,
    "python_contract": {
        "entrypoint": "zero-argument solve() returning only the final answer",
        "filesystem_access": False,
        "network_access": False,
        "process_access": False,
        "allowed_import_roots": [
            "collections",
            "decimal",
            "fractions",
            "functools",
            "itertools",
            "math",
            "statistics",
        ],
    },
    "policy_violation_action": "reject_candidate_before_tournament",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate constrained operator candidates and run a direct tournament."
    )
    parser.add_argument(
        "stage",
        choices=("generate", "tournament", "confirm", "compare", "smoke", "all"),
    )
    parser.add_argument("--workspace-root", default="workspace/MATH")
    parser.add_argument("--data", default="data/datasets/math_validate.jsonl")
    parser.add_argument("--opt-model", default="deepseek-v4-pro")
    parser.add_argument("--exec-model", default="deepseek-v4-flash")
    parser.add_argument("--candidate-count", type=int, default=4)
    parser.add_argument(
        "--operator-mode",
        choices=("family", "prompt_only", "tool_augmented"),
        default="family",
        help=(
            "Candidate execution track. 'family' uses the mechanism manifest's "
            "policy; the other values preregister an explicit comparison track."
        ),
    )
    parser.add_argument("--failure-cases", type=int, default=8)
    parser.add_argument("--smoke-count", type=int, default=2)
    parser.add_argument("--bottleneck", default="Persistent MATH reasoning failures")
    parser.add_argument("--batch-manifest", default="")
    parser.add_argument("--prompt-batch-manifest", default="")
    parser.add_argument("--tool-batch-manifest", default="")
    parser.add_argument(
        "--invalid-comparison",
        default="",
        help=(
            "Infrastructure audit from a failed blind comparison. When set, "
            "the frozen candidates may be re-evaluated only on a fresh family."
        ),
    )
    parser.add_argument(
        "--mechanism-manifest",
        default="",
        help=(
            "Failure-family manifest. Generation reads only its design split; "
            "tournament reads only hidden gate/regression splits."
        ),
    )
    parser.add_argument(
        "--design-indices",
        nargs="+",
        type=int,
        help="Visible optimization failures used by diagnosis and design.",
    )
    parser.add_argument("--target-indices", nargs="+", type=int)
    parser.add_argument("--regression-indices", nargs="*", type=int, default=[])
    parser.add_argument("--num-runs", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--no-promote",
        action="store_true",
        help="Rank candidates but do not change lifecycle state or install a winner.",
    )
    return parser.parse_args()


def _generation_policy(
    requested_mode: str, family_policy: Dict[str, Any] | None = None
) -> tuple[Dict[str, Any], str]:
    if requested_mode == "prompt_only":
        return dict(PROMPT_ONLY_POLICY), "explicit_prompt_only_track"
    if requested_mode == "tool_augmented":
        return dict(TOOL_AUGMENTED_POLICY), "explicit_tool_augmented_track"
    return dict(family_policy or {}), "mechanism_family"


def _resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def _model_config(name: str) -> Any:
    config = LLMsConfig.default().get(name)
    if config is None:
        raise SystemExit(f"Unknown model configuration: {name}")
    return config


def _validate_operational_execution(args: argparse.Namespace, family: Dict[str, Any]) -> None:
    protocol = family.get("operational_evaluation")
    if not protocol:
        return
    if args.exec_model != protocol.get("exec_model"):
        raise SystemExit(
            f"operational protocol requires exec_model={protocol.get('exec_model')}; "
            f"received {args.exec_model}"
        )
    if float(args.timeout) != float(protocol.get("timeout_seconds")):
        raise SystemExit(
            f"operational protocol requires timeout={protocol.get('timeout_seconds')}; "
            f"received {args.timeout}"
        )
    if int(args.num_runs) != int(protocol.get("num_runs")):
        raise SystemExit(
            f"operational protocol requires num_runs={protocol.get('num_runs')}; "
            f"received {args.num_runs}"
        )


def _latest_manifest(workspace_root: Path) -> Path:
    manifests = sorted(
        workspace_root.glob("operator_evolution_batches/*/manifest.json"),
        reverse=True,
    )
    pending = []
    for path in manifests:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("status") in {"pending_tournament", "ranked_not_promoted"}:
            pending.append(path)
    if not pending:
        raise SystemExit("No pending tournament batch found; run stage=generate first.")
    return pending[0]


def _write_detailed_rows(
    result: Dict[str, Any],
    data: Sequence[Dict[str, Any]],
    output_path: Path,
) -> None:
    """Persist standalone diagnostic rows including prompts and full outputs."""
    with output_path.open("w", encoding="utf-8") as handle:
        for candidate in result["ranking"]:
            for row in candidate["rows"]:
                item = data[int(row["index"])]
                detail = {
                    "operator_name": candidate["operator_name"],
                    "problem": item["problem"],
                    "reference_solution": item["solution"],
                    "mechanism_metadata": item.get("mechanism_metadata", {}),
                    **row,
                }
                handle.write(json.dumps(detail, ensure_ascii=False) + "\n")


def _failure_cases_for_indices(
    workspace_root: Path,
    data_path: Path,
    indices: Sequence[int],
) -> str:
    """Build a coherent, user-selected failure cluster from optimization logs."""
    data = load_jsonl(data_path)
    requested = sorted(set(indices))
    if any(index < 0 or index >= len(data) for index in requested):
        raise SystemExit("--target-indices contains an index outside the dataset")
    latest_by_question: Dict[str, Dict[str, Any]] = {}
    failure_counts: Dict[str, int] = {}
    for log_path in sorted((workspace_root / "workflows").glob("round_*/log.json")):
        with log_path.open(encoding="utf-8") as handle:
            entries = json.load(handle)
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries:
            question = str(entry.get("question", ""))
            if not question:
                continue
            failure_counts[question] = failure_counts.get(question, 0) + 1
            latest_by_question[question] = entry
    lines = []
    for index in requested:
        item = data[index]
        question = str(item["problem"])
        entry = latest_by_question.get(question)
        if entry is None:
            raise SystemExit(
                f"target index {index} has no recorded failure; choose persistent failures"
            )
        model_output = str(entry.get("model_output", ""))
        if len(model_output) > 1200:
            model_output = model_output[:1200] + "...(truncated)"
        lines.append(
            f"### Optimization failure index {index} "
            f"(failed in {failure_counts.get(question, 1)} logged rounds)\n"
            f"**Question**: {question}\n"
            f"**Failed output**: {model_output}\n"
            f"**Extracted answer**: {entry.get('extracted_output', '')}\n"
            f"**Reference answer**: {entry.get('right_answer', item['solution'])}\n"
        )
    return "\n".join(lines)


async def generate(args: argparse.Namespace, workspace_root: Path) -> Dict[str, Any]:
    evolver = OperatorEvolver(
        root_path=str(workspace_root),
        optimize_llm=create_llm_instance(_model_config(args.opt_model)),
        dataset="MATH",
    )
    operator_json = workspace_root / "workflows" / "template" / "operator.json"
    with operator_json.open(encoding="utf-8") as handle:
        operator_names = list(json.load(handle))
    data_path = _resolve(args.data)
    batch_context: Dict[str, Any] = {}
    bottleneck = args.bottleneck
    if args.mechanism_manifest:
        if args.design_indices:
            raise SystemExit(
                "--mechanism-manifest cannot be combined with --design-indices"
            )
        family = load_bundle_manifest(_resolve(args.mechanism_manifest))
        # Visibility boundary: candidate diagnosis/design receives only these
        # rows. Gate and regression files are not opened in this stage.
        design_rows = load_bundle_split(family, "design")
        failure_cases = render_visible_design_cases(design_rows)
        mechanism = family["mechanism"]
        bottleneck = (
            f"{bottleneck}\nMechanism family: {mechanism['name']}. "
            f"Capability gap: {mechanism['capability_gap']}"
        )
        generation_policy, policy_source = _generation_policy(
            args.operator_mode, family.get("operator_generation_policy", {})
        )
        batch_context = {
            "evaluation_protocol": "hidden_mechanism_gate_v1",
            "primary_metric": family.get("operational_evaluation", {}).get(
                "primary_metric", "mathematical_correctness"
            ),
            "operator_generation_policy": generation_policy,
            "operator_generation_policy_source": policy_source,
            "operator_track": generation_policy.get("execution_mode", "unspecified"),
            "mechanism_family": {
                "family_id": family["family_id"],
                "manifest_path": family["_manifest_path"],
                "manifest_sha256": family["_manifest_sha256"],
                "generator_version": family["generator_version"],
                "visible_split": "design",
                "design_sha256": family["splits"]["design"]["sha256"],
                "design_count": family["splits"]["design"]["count"],
                "hidden_gate_count": family["splits"]["gate"]["count"],
                "hidden_regression_count": family["splits"]["regression"]["count"],
            },
        }
    else:
        generation_policy, policy_source = _generation_policy(args.operator_mode)
        batch_context = {
            "operator_generation_policy": generation_policy,
            "operator_generation_policy_source": policy_source,
            "operator_track": generation_policy.get("execution_mode", "unspecified"),
        }
        failure_cases = (
            _failure_cases_for_indices(
                workspace_root, data_path, args.design_indices
            )
            if args.design_indices
            else evolver.collect_failure_cases(max_cases=args.failure_cases)
        )
    if failure_cases in {"无评估结果", "无法读取结果", "无结果数据"}:
        raise SystemExit(f"Cannot generate candidates: {failure_cases}")
    batch = await evolver.evolve_candidates(
        bottleneck_info=bottleneck,
        failure_cases=failure_cases,
        graph_code=evolver.get_current_graph_code(),
        operator_descriptions=evolver.get_operator_descriptions(operator_names),
        candidate_count=args.candidate_count,
        batch_context=batch_context,
    )
    print(f"Generated batch: {batch['batch_id']}")
    print(f"Manifest: {batch['manifest_path']}")
    print("Candidates: " + ", ".join(batch["candidate_names"]))
    print("Candidates are archived only; none has been installed or routed.")
    return batch


async def tournament(
    args: argparse.Namespace,
    workspace_root: Path,
    manifest_path: Path,
) -> Dict[str, Any]:
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("experiment_role") == "pilot":
        raise SystemExit(
            "This pilot batch is frozen and cannot be rerun or promoted; "
            "use stage=confirm with a fresh confirmatory family."
        )
    if manifest.get("tournament_allowed") is False:
        raise SystemExit(
            "This batch is frozen by protocol audit and cannot enter a tournament."
        )
    if manifest.get("status") not in {"pending_tournament", "ranked_not_promoted"}:
        raise SystemExit(f"Batch is not pending: {manifest.get('status')}")
    family_provenance: Dict[str, Any] = {}
    if args.mechanism_manifest:
        if args.target_indices or args.regression_indices:
            raise SystemExit(
                "--mechanism-manifest supplies hidden target/regression splits; "
                "do not pass explicit indices"
            )
        family = load_bundle_manifest(_resolve(args.mechanism_manifest))
        _validate_operational_execution(args, family)
        expected_family = manifest.get("batch_context", {}).get(
            "mechanism_family", {}
        )
        if not expected_family:
            raise SystemExit(
                "batch was not generated from a mechanism-family manifest"
            )
        if family["_manifest_sha256"] != expected_family.get("manifest_sha256"):
            raise SystemExit(
                "mechanism manifest differs from the one used for candidate generation"
            )
        gate_rows = load_bundle_split(family, "gate")
        regression_rows = load_bundle_split(family, "regression")
        ensure_disjoint_splits({"gate": gate_rows, "regression": regression_rows})
        data = [*gate_rows, *regression_rows]
        target_indices = list(range(len(gate_rows)))
        regression_indices = list(range(len(gate_rows), len(data)))
        family_provenance = {
            "evaluation_protocol": "hidden_mechanism_gate_v1",
            "family_id": family["family_id"],
            "manifest_sha256": family["_manifest_sha256"],
            "gate_sha256": family["splits"]["gate"]["sha256"],
            "regression_sha256": family["splits"]["regression"]["sha256"],
            "gate_count": len(gate_rows),
            "regression_count": len(regression_rows),
        }
    else:
        if not args.target_indices:
            raise SystemExit("tournament/all requires --target-indices")
        data = load_jsonl(_resolve(args.data))
        target_indices = args.target_indices
        regression_indices = args.regression_indices
    runner = OperatorTournament(
        library_path=workspace_root / "operator_library" / "operators.json",
        template_dir=workspace_root / "workflows" / "template",
        llm_config=_model_config(args.exec_model),
        timeout=args.timeout,
        concurrency=args.concurrency,
    )
    result = await runner.evaluate(
        candidate_names=manifest["candidate_names"],
        data=data,
        target_indices=target_indices,
        regression_indices=regression_indices,
        num_runs=args.num_runs,
        promote=not args.no_promote,
    )
    if family_provenance:
        result["mechanism_family"] = family_provenance
    result["scorer_version"] = MATHBenchmark.SCORER_VERSION
    details_path = manifest_path.with_name("tournament_details.jsonl")
    _write_detailed_rows(result, data, details_path)
    result["details_path"] = str(details_path)
    output_path = manifest_path.with_name("tournament_summary.json")
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    manifest["status"] = "completed" if not args.no_promote else "ranked_not_promoted"
    manifest["winner"] = result["winner"]
    manifest["tournament_summary"] = str(output_path)
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"Tournament summary: {output_path}")
    for rank, item in enumerate(result["ranking"], 1):
        metrics = item["metrics"]
        print(
            f"{rank}. {item['operator_name']}: quality={metrics['quality_score']:.4f}, "
            f"W/T/L={metrics['wins']}/{metrics['ties']}/{metrics['losses']}, "
            f"regression_loss={metrics['regression_loss_rate']:.3f}, "
            f"passed={metrics['passed']}"
        )
    if result["winner"]:
        action = "promoted and installed" if result["promoted"] else "ranked only"
        print(f"Winner: {result['winner']} ({action})")
    else:
        print("No candidate passed. Nothing was installed or exposed to routing.")
    return result


def _load_unopened_comparison_batch(
    path: Path, expected_track: str, *, allow_invalidated: bool = False
) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    allowed_statuses = {"pending_tournament"}
    if allow_invalidated:
        allowed_statuses.add("invalidated_infrastructure")
    if manifest.get("status") not in allowed_statuses:
        raise SystemExit(
            f"comparison batch must be unopened and pending: {path} "
            f"(status={manifest.get('status')})"
        )
    if not allow_invalidated and (manifest.get("tournament_summary") or path.with_name(
        "tournament_summary.json"
    ).exists()):
        raise SystemExit(f"comparison batch already has hidden evaluation results: {path}")
    policy = manifest.get("batch_context", {}).get(
        "operator_generation_policy", {}
    )
    observed_track = policy.get("execution_mode")
    if observed_track != expected_track:
        raise SystemExit(
            f"expected {expected_track} batch, got {observed_track}: {path}"
        )
    return manifest


def _track_statistics(rows: Sequence[Dict[str, Any]], score_key: str) -> Dict[str, Any]:
    target = [row for row in rows if row["split"] == "target"]
    regression = [row for row in rows if row["split"] == "regression"]
    target_correct = sum(int(row[score_key]) for row in target)
    regression_correct = sum(int(row[score_key]) for row in regression)
    return {
        "target_correct": target_correct,
        "target_total": len(target),
        "target_accuracy": target_correct / len(target) if target else 0.0,
        "regression_correct": regression_correct,
        "regression_total": len(regression),
        "regression_accuracy": (
            regression_correct / len(regression) if regression else 0.0
        ),
    }


async def compare_tracks(args: argparse.Namespace, workspace_root: Path) -> Dict[str, Any]:
    """Blindly evaluate prompt-only and tool-augmented tracks together."""
    if not args.mechanism_manifest:
        raise SystemExit("stage=compare requires --mechanism-manifest")
    if not args.prompt_batch_manifest or not args.tool_batch_manifest:
        raise SystemExit(
            "stage=compare requires --prompt-batch-manifest and "
            "--tool-batch-manifest"
        )
    prompt_path = _resolve(args.prompt_batch_manifest)
    tool_path = _resolve(args.tool_batch_manifest)
    rerun_audit: Dict[str, Any] | None = None
    rerun_audit_path: Path | None = None
    if args.invalid_comparison:
        rerun_audit_path = _resolve(args.invalid_comparison)
        with rerun_audit_path.open(encoding="utf-8") as handle:
            rerun_audit = json.load(handle)
        if (
            rerun_audit.get("validity_status") != "invalid_infrastructure"
            or rerun_audit.get("excluded_from_scientific_evidence") is not True
            or rerun_audit.get("rerun_policy", {}).get("fresh_random_seed_required")
            is not True
        ):
            raise SystemExit("invalid comparison audit does not authorize a fresh rerun")
    prompt_batch = _load_unopened_comparison_batch(
        prompt_path, "prompt_only", allow_invalidated=rerun_audit is not None
    )
    tool_batch = _load_unopened_comparison_batch(
        tool_path, "tool_augmented", allow_invalidated=rerun_audit is not None
    )
    prompt_family = prompt_batch.get("batch_context", {}).get("mechanism_family", {})
    tool_family = tool_batch.get("batch_context", {}).get("mechanism_family", {})
    if not prompt_family or not tool_family:
        raise SystemExit("both comparison batches must be generated from a mechanism family")
    if (
        prompt_family.get("family_id") != tool_family.get("family_id")
        or prompt_family.get("manifest_sha256") != tool_family.get("manifest_sha256")
    ):
        raise SystemExit("comparison batches are bound to different mechanism families")
    family = load_bundle_manifest(_resolve(args.mechanism_manifest))
    _validate_operational_execution(args, family)
    if rerun_audit is None:
        if family["_manifest_sha256"] != prompt_family.get("manifest_sha256"):
            raise SystemExit("mechanism manifest differs from the one used for generation")
    else:
        if family["_manifest_sha256"] == prompt_family.get("manifest_sha256"):
            raise SystemExit("infrastructure rerun requires a fresh mechanism family")
        source_family = load_bundle_manifest(Path(prompt_family["manifest_path"]))
        if family.get("mechanism", {}).get("name") != source_family.get(
            "mechanism", {}
        ).get("name"):
            raise SystemExit("fresh rerun family uses a different failure mechanism")

    prompt_names = list(prompt_batch.get("candidate_names", []))
    tool_names = list(tool_batch.get("candidate_names", []))
    if not prompt_names or not tool_names:
        raise SystemExit("each comparison track requires at least one candidate")
    if set(prompt_names) & set(tool_names):
        raise SystemExit("prompt-only and tool-augmented candidate names overlap")

    runner = OperatorTournament(
        library_path=workspace_root / "operator_library" / "operators.json",
        template_dir=workspace_root / "workflows" / "template",
        llm_config=_model_config(args.exec_model),
        timeout=args.timeout,
        concurrency=args.concurrency,
    )
    artifact_hashes: Dict[str, str] = {}
    for name in [*prompt_names, *tool_names]:
        record = runner.library.get(name) or {}
        if record.get("status") != "candidate":
            raise SystemExit(f"comparison entrant is not an archived candidate: {name}")
        artifact_hash = record.get("artifact", {}).get("artifact_sha256", "")
        if not artifact_hash:
            raise SystemExit(f"comparison entrant has no immutable artifact hash: {name}")
        artifact_hashes[name] = artifact_hash

    comparison_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = workspace_root / "operator_comparisons" / comparison_id
    output_dir.mkdir(parents=True, exist_ok=False)
    protocol_path = output_dir / "protocol.json"
    protocol = {
        "schema_version": 1,
        "comparison_id": comparison_id,
        "status": "preregistered_before_hidden_split_load",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "primary_metric": "correct_within_timeout",
        "exec_model": args.exec_model,
        "timeout_seconds": args.timeout,
        "num_runs": args.num_runs,
        "family_id": family["family_id"],
        "family_manifest_sha256": family["_manifest_sha256"],
        "gate_sha256": family["splits"]["gate"]["sha256"],
        "regression_sha256": family["splits"]["regression"]["sha256"],
        "prompt_batch": {
            "batch_id": prompt_batch["batch_id"],
            "manifest": str(prompt_path),
            "manifest_sha256": file_sha256(prompt_path),
            "candidates": prompt_names,
        },
        "tool_batch": {
            "batch_id": tool_batch["batch_id"],
            "manifest": str(tool_path),
            "manifest_sha256": file_sha256(tool_path),
            "candidates": tool_names,
        },
        "candidate_artifact_sha256": artifact_hashes,
        "controls": ["generic_two_stage_baseline", "GenericProgrammerControl"],
        "promotion_allowed": False,
        "runtime_component_sha256": {
            "formatter": file_sha256(REPO_ROOT / "scripts" / "formatter.py"),
            "feature_extractor": file_sha256(
                REPO_ROOT / "scripts" / "operator_library.py"
            ),
            "tournament": file_sha256(
                REPO_ROOT / "scripts" / "operator_tournament.py"
            ),
            "restricted_code_execution": file_sha256(
                REPO_ROOT / "scripts" / "restricted_code_execution.py"
            ),
            "parameterized_tools": file_sha256(
                REPO_ROOT / "scripts" / "parameterized_tools.py"
            ),
            "math_operator_runtime": file_sha256(
                workspace_root / "workflows" / "template" / "operator.py"
            ),
        },
    }
    if rerun_audit is not None and rerun_audit_path is not None:
        protocol["rerun_of_invalid_comparison"] = {
            "comparison_id": rerun_audit["comparison_id"],
            "audit": str(rerun_audit_path),
            "audit_sha256": file_sha256(rerun_audit_path),
            "candidate_generation_family_id": prompt_family["family_id"],
            "candidate_generation_family_sha256": prompt_family["manifest_sha256"],
            "fresh_evaluation_family_id": family["family_id"],
            "fresh_evaluation_family_sha256": family["_manifest_sha256"],
        }
    with protocol_path.open("w", encoding="utf-8") as handle:
        json.dump(protocol, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    # Hidden rows are first opened only after every entrant and artifact hash has
    # been frozen in protocol.json above.
    gate_rows = load_bundle_split(family, "gate")
    regression_rows = load_bundle_split(family, "regression")
    ensure_disjoint_splits({"gate": gate_rows, "regression": regression_rows})
    if rerun_audit is not None:
        details_path_value = rerun_audit.get("frozen_raw_files", {}).get(
            "details", {}
        ).get("path", "")
        prior_details_path = Path(str(details_path_value))
        if (
            not prior_details_path.is_file()
            or file_sha256(prior_details_path)
            != rerun_audit["frozen_raw_files"]["details"].get("sha256")
        ):
            raise SystemExit("invalid comparison details are missing or changed")
        prior_problem_ids = set()
        with prior_details_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    prior_problem_ids.add(json.loads(line)["problem_id"])
        fresh_problem_ids = {
            row.get("mechanism_metadata", {}).get("problem_id")
            for row in [*gate_rows, *regression_rows]
        }
        overlap = prior_problem_ids & fresh_problem_ids
        if overlap:
            raise SystemExit(
                "fresh rerun family overlaps the invalid hidden set: "
                + ", ".join(sorted(overlap))
            )
    data = [*gate_rows, *regression_rows]
    target_indices = list(range(len(gate_rows)))
    regression_indices = list(range(len(gate_rows), len(data)))
    result = await runner.evaluate(
        candidate_names=[*prompt_names, *tool_names],
        data=data,
        target_indices=target_indices,
        regression_indices=regression_indices,
        num_runs=args.num_runs,
        promote=False,
        include_programmer_control=True,
    )
    track_by_name = {
        **{name: "prompt_only" for name in prompt_names},
        **{name: "tool_augmented" for name in tool_names},
        "GenericProgrammerControl": "generic_programmer_control",
    }
    for entrant in result["ranking"]:
        entrant["track"] = track_by_name[entrant["operator_name"]]
        entrant["accuracy"] = _track_statistics(
            entrant["rows"], "candidate_score"
        )
    representative_rows = result["ranking"][0]["rows"]
    result.update(
        {
            "comparison_id": comparison_id,
            "experiment_role": "blind_track_comparison",
            "promotion_eligible": False,
            "primary_metric": "correct_within_timeout",
            "baseline": _track_statistics(representative_rows, "baseline_score"),
            "protocol": str(protocol_path),
            "protocol_sha256": file_sha256(protocol_path),
            "scorer_version": MATHBenchmark.SCORER_VERSION,
            "mechanism_family": {
                "family_id": family["family_id"],
                "manifest_sha256": family["_manifest_sha256"],
                "gate_sha256": family["splits"]["gate"]["sha256"],
                "regression_sha256": family["splits"]["regression"]["sha256"],
            },
        }
    )
    details_path = output_dir / "comparison_details.jsonl"
    summary_path = output_dir / "comparison_summary.json"
    _write_detailed_rows(result, data, details_path)
    result["details_path"] = str(details_path)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    for path, manifest in ((prompt_path, prompt_batch), (tool_path, tool_batch)):
        manifest["status"] = (
            "infrastructure_rerun_completed"
            if rerun_audit is not None
            else "blind_comparison_completed"
        )
        manifest["comparison_id"] = comparison_id
        manifest["comparison_summary"] = str(summary_path)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    print(f"Blind comparison summary: {summary_path}")
    print(f"Blind comparison details: {details_path}")
    baseline = result["baseline"]
    print(
        "Baseline generic two-stage: "
        f"target={baseline['target_correct']}/{baseline['target_total']}, "
        f"regression={baseline['regression_correct']}/{baseline['regression_total']}"
    )
    for entrant in result["ranking"]:
        acc = entrant["accuracy"]
        metrics = entrant["metrics"]
        print(
            f"{entrant['operator_name']} [{entrant['track']}]: "
            f"target={acc['target_correct']}/{acc['target_total']}, "
            f"W/T/L={metrics['wins']}/{metrics['ties']}/{metrics['losses']}, "
            f"passed={metrics['passed']}"
        )
    print("Blind comparison never promotes or installs artifacts.")
    return result


async def smoke_tracks(args: argparse.Namespace, workspace_root: Path) -> Dict[str, Any]:
    """Check execution plumbing on visible design rows; never open hidden splits."""
    if not args.mechanism_manifest:
        raise SystemExit("stage=smoke requires --mechanism-manifest")
    if not args.prompt_batch_manifest or not args.tool_batch_manifest:
        raise SystemExit(
            "stage=smoke requires --prompt-batch-manifest and --tool-batch-manifest"
        )
    if args.smoke_count <= 0:
        raise SystemExit("--smoke-count must be positive")
    prompt_path = _resolve(args.prompt_batch_manifest)
    tool_path = _resolve(args.tool_batch_manifest)
    prompt_batch = _load_unopened_comparison_batch(
        prompt_path, "prompt_only", allow_invalidated=True
    )
    tool_batch = _load_unopened_comparison_batch(
        tool_path, "tool_augmented", allow_invalidated=True
    )
    family = load_bundle_manifest(_resolve(args.mechanism_manifest))
    expected = prompt_batch.get("batch_context", {}).get("mechanism_family", {})
    if family["_manifest_sha256"] != expected.get("manifest_sha256"):
        raise SystemExit("smoke test must use the original visible design family")
    if tool_batch.get("batch_context", {}).get("mechanism_family", {}).get(
        "manifest_sha256"
    ) != family["_manifest_sha256"]:
        raise SystemExit("smoke batches do not share the same design family")
    design_rows = load_bundle_split(family, "design")[: args.smoke_count]
    if len(design_rows) < args.smoke_count:
        raise SystemExit("smoke count exceeds visible design rows")
    runner = OperatorTournament(
        library_path=workspace_root / "operator_library" / "operators.json",
        template_dir=workspace_root / "workflows" / "template",
        llm_config=_model_config(args.exec_model),
        timeout=args.timeout,
        concurrency=args.concurrency,
    )
    names = [
        *prompt_batch["candidate_names"],
        *tool_batch["candidate_names"],
    ]
    result = await runner.evaluate(
        candidate_names=names,
        data=design_rows,
        target_indices=list(range(len(design_rows))),
        regression_indices=[],
        num_runs=1,
        promote=False,
        include_programmer_control=True,
    )
    track_by_name = {
        **{name: "prompt_only" for name in prompt_batch["candidate_names"]},
        **{name: "tool_augmented" for name in tool_batch["candidate_names"]},
        "GenericProgrammerControl": "generic_programmer_control",
    }
    for entrant in result["ranking"]:
        entrant["track"] = track_by_name[entrant["operator_name"]]
        entrant["accuracy"] = _track_statistics(
            entrant["rows"], "candidate_score"
        )
    result.update(
        {
            "experiment_role": "visible_design_execution_smoke",
            "scientific_evidence": False,
            "hidden_splits_opened": False,
            "scorer_version": MATHBenchmark.SCORER_VERSION,
        }
    )
    smoke_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = workspace_root / "operator_smoke" / smoke_id
    output_dir.mkdir(parents=True, exist_ok=False)
    details_path = output_dir / "smoke_details.jsonl"
    summary_path = output_dir / "smoke_summary.json"
    _write_detailed_rows(result, design_rows, details_path)
    result["details_path"] = str(details_path)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"Visible-design smoke summary: {summary_path}")
    print(f"Visible-design smoke details: {details_path}")
    for entrant in result["ranking"]:
        rows = entrant["rows"]
        correct = sum(int(row["candidate_score"]) for row in rows)
        declines = sum(bool(row["declined"]) for row in rows)
        errors = sum(bool(row["candidate_error"]) for row in rows)
        print(
            f"{entrant['operator_name']} [{entrant['track']}]: "
            f"correct={correct}/{len(rows)}, declines={declines}, errors={errors}"
        )
    print("Smoke results are plumbing checks only and are not scientific evidence.")
    return result


async def confirm(
    args: argparse.Namespace,
    workspace_root: Path,
    pilot_manifest_path: Path,
) -> Dict[str, Any]:
    """Evaluate frozen pilot artifacts on a fresh preregistered family."""
    if not args.mechanism_manifest:
        raise SystemExit("stage=confirm requires --mechanism-manifest")
    with pilot_manifest_path.open(encoding="utf-8") as handle:
        pilot = json.load(handle)
    if pilot.get("experiment_role") != "pilot":
        raise SystemExit("stage=confirm requires a frozen pilot batch manifest")
    if pilot.get("promotion_eligible") is not False:
        raise SystemExit("pilot manifest must explicitly disable promotion")

    pilot_record_path = Path(str(pilot.get("pilot_record", "")))
    if not pilot_record_path.is_file():
        raise SystemExit("pilot record is missing")
    with pilot_record_path.open(encoding="utf-8") as handle:
        pilot_record = json.load(handle)

    family = load_bundle_manifest(_resolve(args.mechanism_manifest))
    if family.get("experiment_role") != "confirmatory":
        raise SystemExit("confirmation requires a confirmatory mechanism family")
    protocol = family.get("confirmation_protocol", {})
    if protocol.get("parent_pilot_batch_id") != pilot.get("batch_id"):
        raise SystemExit("confirmation family is bound to a different pilot batch")
    if file_sha256(pilot_record_path) != protocol.get("pilot_record_sha256"):
        raise SystemExit("frozen pilot record hash does not match confirmation protocol")
    primary = str(protocol.get("primary_candidate", ""))
    if primary != pilot_record.get("primary_confirmation_candidate"):
        raise SystemExit("primary confirmation candidate differs from frozen pilot record")
    if primary not in pilot.get("candidate_names", []):
        raise SystemExit("primary confirmation candidate is absent from pilot artifacts")
    fixed_runs = int(protocol.get("num_runs", 0) or 0)
    if args.num_runs != fixed_runs:
        raise SystemExit(
            f"confirmation run count is preregistered as {fixed_runs}; "
            f"received {args.num_runs}"
        )
    if protocol.get("promotion_allowed") is not False:
        raise SystemExit("confirmatory protocol must disable promotion")

    prior_confirmations = pilot.get("confirmations", [])
    if any(
        item.get("family_id") == family["family_id"]
        for item in prior_confirmations
    ):
        raise SystemExit("this confirmatory family has already been evaluated")

    gate_rows = load_bundle_split(family, "gate")
    regression_rows = load_bundle_split(family, "regression")
    ensure_disjoint_splits({"gate": gate_rows, "regression": regression_rows})
    data = [*gate_rows, *regression_rows]
    target_indices = list(range(len(gate_rows)))
    regression_indices = list(range(len(gate_rows), len(data)))
    runner = OperatorTournament(
        library_path=workspace_root / "operator_library" / "operators.json",
        template_dir=workspace_root / "workflows" / "template",
        llm_config=_model_config(args.exec_model),
        timeout=args.timeout,
        concurrency=args.concurrency,
    )
    frozen_artifacts = pilot_record.get("candidate_artifact_sha256", {})
    if set(frozen_artifacts) != set(pilot["candidate_names"]):
        raise SystemExit("pilot record does not pin every candidate artifact")
    for name in pilot["candidate_names"]:
        record = runner.library.get(name)
        observed_sha = (record or {}).get("artifact", {}).get(
            "artifact_sha256", ""
        )
        if observed_sha != frozen_artifacts[name]:
            raise SystemExit(f"frozen candidate artifact changed: {name}")
    result = await runner.evaluate(
        candidate_names=pilot["candidate_names"],
        data=data,
        target_indices=target_indices,
        regression_indices=regression_indices,
        num_runs=fixed_runs,
        promote=False,
    )
    primary_result = next(
        item for item in result["ranking"] if item["operator_name"] == primary
    )
    result.update(
        {
            "experiment_role": "confirmatory",
            "promotion_eligible": False,
            "parent_pilot_batch_id": pilot["batch_id"],
            "primary_candidate": primary,
            "primary_candidate_passed": primary_result["metrics"]["passed"],
            "scorer_version": MATHBenchmark.SCORER_VERSION,
            "mechanism_family": {
                "family_id": family["family_id"],
                "generator_version": family["generator_version"],
                "manifest_sha256": family["_manifest_sha256"],
                "gate_sha256": family["splits"]["gate"]["sha256"],
                "regression_sha256": family["splits"]["regression"]["sha256"],
                "gate_count": len(gate_rows),
                "regression_count": len(regression_rows),
                "preregistered_num_runs": fixed_runs,
            },
        }
    )
    prefix = f"confirmation_{family['family_id']}"
    details_path = pilot_manifest_path.with_name(f"{prefix}_details.jsonl")
    summary_path = pilot_manifest_path.with_name(f"{prefix}_summary.json")
    _write_detailed_rows(result, data, details_path)
    result["details_path"] = str(details_path)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    pilot.setdefault("confirmations", []).append(
        {
            "family_id": family["family_id"],
            "manifest_sha256": family["_manifest_sha256"],
            "num_runs": fixed_runs,
            "primary_candidate": primary,
            "primary_candidate_passed": primary_result["metrics"]["passed"],
            "summary": str(summary_path),
            "details": str(details_path),
        }
    )
    with pilot_manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(pilot, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"Confirmation summary: {summary_path}")
    print(f"Confirmation details: {details_path}")
    for rank, item in enumerate(result["ranking"], 1):
        metrics = item["metrics"]
        marker = " [PRIMARY]" if item["operator_name"] == primary else ""
        print(
            f"{rank}. {item['operator_name']}{marker}: "
            f"W/T/L={metrics['wins']}/{metrics['ties']}/{metrics['losses']}, "
            f"regression_loss={metrics['regression_loss_rate']:.3f}, "
            f"passed={metrics['passed']}"
        )
    print("Confirmation never promotes or installs artifacts.")
    return result


async def main() -> None:
    args = parse_args()
    workspace_root = _resolve(args.workspace_root)
    if args.stage == "smoke":
        await smoke_tracks(args, workspace_root)
        return
    if args.stage == "compare":
        await compare_tracks(args, workspace_root)
        return
    if args.stage == "confirm":
        if not args.batch_manifest:
            raise SystemExit("stage=confirm requires --batch-manifest")
        await confirm(args, workspace_root, _resolve(args.batch_manifest))
        return
    if args.stage == "all":
        if args.mechanism_manifest:
            if args.design_indices or args.target_indices or args.regression_indices:
                raise SystemExit(
                    "mechanism-family stage=all uses manifest splits; do not pass indices"
                )
        else:
            if not args.design_indices or not args.target_indices:
                raise SystemExit(
                    "stage=all requires disjoint --design-indices and --target-indices"
                )
            overlap = set(args.design_indices) & set(args.target_indices)
            if overlap:
                raise SystemExit(
                    f"design and tournament target indices overlap: {sorted(overlap)}"
                )
    batch = None
    if args.stage in {"generate", "all"}:
        batch = await generate(args, workspace_root)
    if args.stage in {"tournament", "all"}:
        if batch:
            manifest_path = Path(batch["manifest_path"])
        elif args.batch_manifest:
            manifest_path = _resolve(args.batch_manifest)
        else:
            manifest_path = _latest_manifest(workspace_root)
        await tournament(args, workspace_root, manifest_path)


if __name__ == "__main__":
    asyncio.run(main())
