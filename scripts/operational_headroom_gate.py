# -*- coding: utf-8 -*-
"""Operational headroom confirmation and promotion for calibrated mechanisms.

This protocol treats a task as successful only when the saved response is both
mathematically correct and returned within the fixed timeout.  It does not
rewrite the prior correctness-only calibration: aggregate stable-timeout
evidence merely authorizes a fresh-seed baseline-only confirmation.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from scripts.baseline_failure_mechanisms import file_sha256, load_bundle
from scripts.baseline_failure_mechanisms_v2 import (
    CAPABILITY_GAPS,
    GENERATOR_VERSION,
    SOURCE_INDICES,
    _json_hash,
    _unique_rows,
    _write_jsonl,
)


PROTOCOL_VERSION = "correct_within_timeout_operational_v1"
# Backward-compatible defaults used by the first (parenthesization) study.
# New studies must pass an explicit mechanism on the command line so the
# frozen protocol can be reused without copying or weakening it.
MECHANISM = "parenthesization_distinct_value_dedup"
DIFFICULTY = "hard"
ALLOWED_AUDIT_LABELS = {
    "true_model_error",
    "scorer_false_negative",
    "invalid_generated_problem",
    "execution_error",
}


def _read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _audit_index(
    *, observations: list[Mapping[str, Any]], audits: list[Mapping[str, Any]],
) -> Dict[tuple[str, int], Mapping[str, Any]]:
    pending = [row.get("audit_id") for row in audits if row.get("audit_label") is None]
    if pending:
        raise ValueError(f"manual audit incomplete: {len(pending)} pending rows")
    invalid = {
        str(row.get("audit_label")) for row in audits
        if row.get("audit_label") not in ALLOWED_AUDIT_LABELS
    }
    if invalid:
        raise ValueError(f"unsupported audit labels: {sorted(invalid)}")
    by_key = {
        (str(row["problem_id"]), int(row["run"])): row for row in audits
    }
    expected = {
        (str(row["problem_id"]), int(row["run"]))
        for row in observations if int(row["score"]) == 0
    }
    if set(by_key) != expected:
        raise ValueError("audit queue does not cover every raw zero exactly once")
    return by_key


def _is_timeout(audit: Mapping[str, Any]) -> bool:
    return "timeouterror" in str(audit.get("execution_error", "")).lower()


def calibration_operational_evidence(
    *, calibration_manifest: Path, raw_summary: Path, audit_queue: Path,
    mechanism: str = MECHANISM, difficulty: str = DIFFICULTY,
) -> Dict[str, Any]:
    if mechanism not in CAPABILITY_GAPS:
        raise ValueError(f"unsupported mechanism: {mechanism}")
    manifest, rows = load_bundle(calibration_manifest)
    if manifest.get("experiment_role") != "baseline_only_parameter_calibration":
        raise ValueError("source is not the frozen difficulty calibration pool")
    raw = _read_json(raw_summary)
    if raw.get("manifest_sha256") != manifest["_manifest_sha256"]:
        raise ValueError("calibration baseline run does not match its manifest")
    observations_path = Path(str(raw["observations"])).resolve()
    if file_sha256(observations_path) != raw.get("observations_sha256"):
        raise ValueError("calibration observations changed after execution")
    observations = _read_jsonl(observations_path)
    audits = _read_jsonl(audit_queue)
    audit_by_key = _audit_index(observations=observations, audits=audits)
    metadata = {
        str(row["mechanism_metadata"]["problem_id"]): row["mechanism_metadata"]
        for row in rows
    }
    invalid_problem_ids = {
        str(row["problem_id"]) for row in audits
        if row["audit_label"] == "invalid_generated_problem"
    }
    target = [
        row for row in observations
        if str(row["problem_id"]) not in invalid_problem_ids
        and metadata[str(row["problem_id"])]["family"] == mechanism
        and metadata[str(row["problem_id"])].get("difficulty_bin") == difficulty
    ]
    correct = 0
    incorrect = 0
    timeouts = 0
    infrastructure_excluded = 0
    timeout_runs: Dict[str, int] = defaultdict(int)
    operational_failure_runs: Dict[str, int] = defaultdict(int)
    for row in target:
        if int(row["score"]) == 1:
            correct += 1
            continue
        audit = audit_by_key[(str(row["problem_id"]), int(row["run"]))]
        label = audit["audit_label"]
        if label == "scorer_false_negative":
            correct += 1
        elif label == "true_model_error":
            incorrect += 1
            operational_failure_runs[str(row["problem_id"])] += 1
        elif label == "execution_error" and _is_timeout(audit):
            timeouts += 1
            timeout_runs[str(row["problem_id"])] += 1
            operational_failure_runs[str(row["problem_id"])] += 1
        elif label == "execution_error":
            infrastructure_excluded += 1
    eligible = correct + incorrect + timeouts
    min_failed_runs = 2
    stable_timeout = sum(count >= min_failed_runs for count in timeout_runs.values())
    stable_operational = sum(
        count >= min_failed_runs for count in operational_failure_runs.values()
    )
    failure_rate = (incorrect + timeouts) / eligible if eligible else 0.0
    timeout_rate = timeouts / eligible if eligible else 0.0
    return {
        "eligible_observations": eligible,
        "correct_within_timeout": correct,
        "incorrect_completed": incorrect,
        "timeouts": timeouts,
        "infrastructure_errors_excluded": infrastructure_excluded,
        "operational_failure_rate": failure_rate,
        "timeout_rate": timeout_rate,
        "stable_operational_failure_problems": stable_operational,
        "stable_timeout_problems": stable_timeout,
        "qualifies_for_fresh_confirmation": (
            failure_rate >= 0.20 and stable_operational >= 3 and stable_timeout >= 3
        ),
    }


def generate_confirmation(
    *, output_dir: Path, calibration_manifest: Path, raw_summary: Path,
    audit_queue: Path, seed: int, cases: int = 20,
    mechanism: str = MECHANISM, difficulty: str = DIFFICULTY,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"operational confirmation already exists: {output_dir}")
    if not 12 <= cases <= 20:
        raise ValueError("confirmation cases must be between 12 and 20")
    evidence = calibration_operational_evidence(
        calibration_manifest=calibration_manifest,
        raw_summary=raw_summary,
        audit_queue=audit_queue,
        mechanism=mechanism,
        difficulty=difficulty,
    )
    if not evidence["qualifies_for_fresh_confirmation"]:
        raise ValueError("calibration has insufficient stable operational headroom")
    calibration, calibration_rows = load_bundle(calibration_manifest)
    if seed == int(calibration["seed"]):
        raise ValueError("operational confirmation requires a fresh seed")
    occupied = {
        str(row["mechanism_metadata"]["problem_id"]) for row in calibration_rows
    }
    rows = _unique_rows(
        mechanism, seed=seed, count=cases, occupied=occupied,
        difficulty=difficulty,
    )
    output_dir.mkdir(parents=True)
    data_path = output_dir / "operational_confirmation.jsonl"
    _write_jsonl(data_path, rows)
    manifest = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "operational_protocol_version": PROTOCOL_VERSION,
        "family_id": _json_hash(
            {
                "role": "operational_confirmation", "mechanism": mechanism,
                "difficulty": difficulty, "seed": seed, "cases": cases,
            }
        )[:16],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "experiment_role": "baseline_only_operational_confirmation",
        "mechanism": {
            "name": mechanism,
            "difficulty_bin": difficulty,
            "capability_gap": CAPABILITY_GAPS[mechanism],
        },
        "source": {
            "calibration_manifest": str(calibration_manifest.resolve()),
            "calibration_manifest_sha256": calibration["_manifest_sha256"],
            "calibration_raw_summary": str(raw_summary.resolve()),
            "calibration_raw_summary_sha256": file_sha256(raw_summary),
            "calibration_audit_queue": str(audit_queue.resolve()),
            "calibration_audit_queue_sha256": file_sha256(audit_queue),
            "aggregate_operational_evidence": evidence,
            "individual_failure_ids_exported": 0,
            "problem_overlap_with_calibration": 0,
        },
        "baseline_only_contract": {
            "candidate_generation_allowed": False,
            "candidate_execution_allowed": False,
            "operator_library_access_allowed": False,
            "routing_allowed": False,
            "solver": "generic_two_stage_baseline",
        },
        "hidden_from_operator_generator": True,
        "screening_protocol": {
            "primary_metric": "correct_within_timeout",
            "baseline_model": "deepseek-v4-flash",
            "timeout_seconds": 180,
            "num_runs": 3,
            "cases_per_mechanism": cases,
            "min_operational_failure_rate": 0.20,
            "min_stable_failed_problems": 3,
            "min_stable_timeout_problems": 3,
            "stable_failure_min_failed_runs": 2,
            "manual_audit_required": True,
            "timeout_counts_as_operational_failure": True,
            "non_timeout_infrastructure_errors_excluded": True,
        },
        "mechanisms": [
            {
                "mechanism_id": mechanism,
                "difficulty_bin": difficulty,
                "source_failure_indices": SOURCE_INDICES[mechanism],
                "count": cases,
            }
        ],
        "screening_data": {
            "path": data_path.name,
            "sha256": file_sha256(data_path),
            "count": len(rows),
            "visible_to_operator_generator": False,
        },
    }
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def finalize_confirmation(
    *, confirmation_manifest: Path, raw_summary: Path, audit_queue: Path,
    output_path: Path,
) -> Dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(f"operational audit already exists: {output_path}")
    manifest, rows = load_bundle(confirmation_manifest)
    if manifest.get("experiment_role") != "baseline_only_operational_confirmation":
        raise ValueError("manifest is not an operational confirmation")
    mechanism = str(manifest.get("mechanism", {}).get("name", ""))
    difficulty = str(manifest.get("mechanism", {}).get("difficulty_bin", ""))
    if mechanism not in CAPABILITY_GAPS:
        raise ValueError(f"unsupported mechanism in confirmation: {mechanism}")
    raw = _read_json(raw_summary)
    if raw.get("manifest_sha256") != manifest["_manifest_sha256"]:
        raise ValueError("raw baseline result does not match confirmation manifest")
    observations_path = Path(str(raw["observations"])).resolve()
    if file_sha256(observations_path) != raw.get("observations_sha256"):
        raise ValueError("confirmation observations changed after execution")
    observations = _read_jsonl(observations_path)
    audits = _read_jsonl(audit_queue)
    audit_by_key = _audit_index(observations=observations, audits=audits)
    invalid_problem_ids = {
        str(row["problem_id"]) for row in audits
        if row["audit_label"] == "invalid_generated_problem"
    }
    correct = 0
    incorrect = 0
    timeouts = 0
    scorer_false_negatives = 0
    infrastructure_excluded = 0
    failure_runs: Dict[str, int] = defaultdict(int)
    timeout_runs: Dict[str, int] = defaultdict(int)
    for row in observations:
        problem_id = str(row["problem_id"])
        if problem_id in invalid_problem_ids:
            continue
        if int(row["score"]) == 1:
            correct += 1
            continue
        audit = audit_by_key[(problem_id, int(row["run"]))]
        label = audit["audit_label"]
        if label == "scorer_false_negative":
            correct += 1
            scorer_false_negatives += 1
        elif label == "true_model_error":
            incorrect += 1
            failure_runs[problem_id] += 1
        elif label == "execution_error" and _is_timeout(audit):
            timeouts += 1
            failure_runs[problem_id] += 1
            timeout_runs[problem_id] += 1
        elif label == "execution_error":
            infrastructure_excluded += 1
    eligible = correct + incorrect + timeouts
    protocol = manifest["screening_protocol"]
    stable_failures = sum(
        count >= int(protocol["stable_failure_min_failed_runs"])
        for count in failure_runs.values()
    )
    stable_timeouts = sum(
        count >= int(protocol["stable_failure_min_failed_runs"])
        for count in timeout_runs.values()
    )
    failure_rate = (incorrect + timeouts) / eligible if eligible else 0.0
    success_rate = correct / eligible if eligible else 0.0
    passed = (
        failure_rate >= float(protocol["min_operational_failure_rate"])
        and stable_failures >= int(protocol["min_stable_failed_problems"])
        and stable_timeouts >= int(protocol["min_stable_timeout_problems"])
    )
    report = {
        "schema_version": 1,
        "report_type": "audited_baseline_operational_headroom_confirmation",
        "operational_protocol_version": PROTOCOL_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "primary_metric": "correct_within_timeout",
        "confirmation_manifest": str(confirmation_manifest.resolve()),
        "confirmation_manifest_sha256": manifest["_manifest_sha256"],
        "confirmation_seed": manifest["seed"],
        "raw_summary": str(raw_summary.resolve()),
        "raw_summary_sha256": file_sha256(raw_summary),
        "observations_sha256": file_sha256(observations_path),
        "audit_queue": str(audit_queue.resolve()),
        "audit_queue_sha256": file_sha256(audit_queue),
        "mechanism": mechanism,
        "difficulty_bin": difficulty,
        "protocol": protocol,
        "metrics": {
            "eligible_observations": eligible,
            "correct_within_timeout": correct,
            "correct_within_timeout_rate": success_rate,
            "operational_failures": incorrect + timeouts,
            "operational_failure_rate": failure_rate,
            "incorrect_completed": incorrect,
            "timeouts": timeouts,
            "stable_operational_failure_problems": stable_failures,
            "stable_timeout_problems": stable_timeouts,
            "scorer_false_negatives": scorer_false_negatives,
            "invalid_problems_excluded": len(invalid_problem_ids),
            "non_timeout_infrastructure_errors_excluded": infrastructure_excluded,
        },
        "headroom_eligible": passed,
        "accepted_mechanisms": [mechanism] if passed else [],
        "promotion_allowed": passed,
        "candidate_generation_invocations": 0,
        "candidate_execution_invocations": 0,
        "operator_library_accesses": 0,
        "routing_invocations": 0,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def promote_operational_family(
    *, output_dir: Path, audited_confirmation: Path, seed: int,
    design_count: int = 8, gate_count: int = 12, regression_count: int = 8,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"operational family already exists: {output_dir}")
    audit = _read_json(audited_confirmation)
    mechanism = str(audit.get("mechanism", ""))
    target_difficulty = str(audit.get("difficulty_bin", ""))
    if (
        audit.get("status") != "complete"
        or not audit.get("promotion_allowed")
        or mechanism not in audit.get("accepted_mechanisms", [])
        or mechanism not in CAPABILITY_GAPS
    ):
        raise ValueError("operational confirmation did not pass promotion gating")
    confirmation_manifest = Path(str(audit["confirmation_manifest"])).resolve()
    if file_sha256(confirmation_manifest) != audit["confirmation_manifest_sha256"]:
        raise ValueError("confirmation manifest changed after operational audit")
    confirmation, confirmation_rows = load_bundle(confirmation_manifest)
    if seed == int(confirmation["seed"]):
        raise ValueError("operator family requires a seed fresh from confirmation")
    calibration_manifest = Path(confirmation["source"]["calibration_manifest"])
    calibration, calibration_rows = load_bundle(calibration_manifest)
    occupied = {
        str(row["mechanism_metadata"]["problem_id"])
        for row in [*calibration_rows, *confirmation_rows]
    }
    output_dir.mkdir(parents=True)
    split_specs = {
        "design": (design_count, DIFFICULTY),
        "gate": (gate_count, DIFFICULTY),
        "regression": (
            regression_count,
            "medium" if target_difficulty == "hard" else "easy",
        ),
    }
    splits = {}
    for offset, (split, (count, split_difficulty)) in enumerate(
        split_specs.items(), 1
    ):
        generated = _unique_rows(
            mechanism, seed=seed + offset * 100003, count=count,
            split=split, occupied=occupied, difficulty=split_difficulty,
        )
        path = output_dir / f"{split}.jsonl"
        _write_jsonl(path, generated)
        splits[split] = {
            "path": path.name,
            "sha256": file_sha256(path),
            "count": len(generated),
            "difficulty_bin": split_difficulty,
            "visible_to_operator_generator": split == "design",
        }
    manifest = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "operational_protocol_version": PROTOCOL_VERSION,
        "family_id": _json_hash(
            {"role": "operational_operator_family", "seed": seed, "splits": split_specs}
        )[:16],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "experiment_role": "development",
        "mechanism": {
            "name": mechanism,
            "capability_gap": CAPABILITY_GAPS[mechanism],
            "difficulty_bin": target_difficulty,
            "source_indices": SOURCE_INDICES[mechanism],
        },
        "headroom_gate": {
            "status": "passed",
            "primary_metric": "correct_within_timeout",
            "audited_summary": str(audited_confirmation.resolve()),
            "audited_summary_sha256": file_sha256(audited_confirmation),
            "thresholds_frozen": True,
        },
        "operational_evaluation": {
            "primary_metric": "correct_within_timeout",
            "exec_model": "deepseek-v4-flash",
            "timeout_seconds": 180,
            "num_runs": 3,
            "timeout_counts_as_failure": True,
        },
        "operator_generation_policy": {
            "execution_mode": "prompt_only",
            "programmer_allowed": False,
            "allowed_skeleton_types": ["single_step", "two_stage", "verify_fix"],
            "max_llm_calls_per_problem": 2,
            "policy_violation_action": "reject_candidate_before_tournament",
        },
        "source": {
            "confirmation_seed": confirmation["seed"],
            "calibration_seed": calibration["seed"],
            "fresh_promotion_seed_verified": True,
            "problem_overlap_with_calibration_or_confirmation": 0,
        },
        "split_policy": {
            "design": "Fresh hard cases visible only after operational headroom confirmation.",
            "gate": "Fresh hidden hard cases evaluated by correct_within_timeout.",
            "regression": "Fresh hidden medium cases; candidate must not reduce task success.",
        },
        "splits": splits,
    }
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Operational correct-within-timeout gating.")
    sub = parser.add_subparsers(dest="stage", required=True)
    generate = sub.add_parser("generate-confirmation")
    generate.add_argument("--output-dir", required=True)
    generate.add_argument("--calibration-manifest", required=True)
    generate.add_argument("--calibration-raw-summary", required=True)
    generate.add_argument("--calibration-audit-queue", required=True)
    generate.add_argument("--seed", type=int, default=99007)
    generate.add_argument("--cases", type=int, default=20)
    generate.add_argument("--mechanism", default=MECHANISM)
    generate.add_argument("--difficulty", default=DIFFICULTY)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--confirmation-manifest", required=True)
    finalize.add_argument("--raw-summary", required=True)
    finalize.add_argument("--audit-queue", required=True)
    finalize.add_argument("--output", required=True)
    promote = sub.add_parser("promote")
    promote.add_argument("--audited-confirmation", required=True)
    promote.add_argument("--output-dir", required=True)
    promote.add_argument("--seed", type=int, default=100019)
    promote.add_argument("--design-count", type=int, default=8)
    promote.add_argument("--gate-count", type=int, default=12)
    promote.add_argument("--regression-count", type=int, default=8)
    args = parser.parse_args()
    if args.stage == "generate-confirmation":
        path = generate_confirmation(
            output_dir=Path(args.output_dir),
            calibration_manifest=Path(args.calibration_manifest),
            raw_summary=Path(args.calibration_raw_summary),
            audit_queue=Path(args.calibration_audit_queue),
            seed=args.seed, cases=args.cases,
            mechanism=args.mechanism, difficulty=args.difficulty,
        )
        print(f"Operational confirmation manifest: {path.resolve()}")
        print("Primary metric: correct_within_timeout; candidate access remains prohibited.")
    elif args.stage == "finalize":
        report = finalize_confirmation(
            confirmation_manifest=Path(args.confirmation_manifest),
            raw_summary=Path(args.raw_summary), audit_queue=Path(args.audit_queue),
            output_path=Path(args.output),
        )
        print(f"Audited operational confirmation: {Path(args.output).resolve()}")
        print(f"Headroom eligible: {report['headroom_eligible']}")
        print(json.dumps(report["metrics"], indent=2, ensure_ascii=False))
    else:
        path = promote_operational_family(
            output_dir=Path(args.output_dir),
            audited_confirmation=Path(args.audited_confirmation), seed=args.seed,
            design_count=args.design_count, gate_count=args.gate_count,
            regression_count=args.regression_count,
        )
        print(f"Operational operator-family manifest: {path.resolve()}")


if __name__ == "__main__":
    main()
