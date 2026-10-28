# -*- coding: utf-8 -*-
"""Calibrate mechanism parameter ranges using baseline-only aggregate evidence.

Protocol:
1. Generate a balanced calibration pool over fixed parameter bins.
2. Run only the generic baseline and manually audit every raw zero.
3. Select complete parameter bins from aggregate error/stability statistics.
4. Generate a disjoint hidden confirmation set from a fresh random seed.

No individual failed problem is selected or exposed as an operator design case.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from scripts.baseline_failure_mechanisms import file_sha256, load_bundle
from scripts.baseline_failure_mechanisms_v2 import (
    DIFFICULTY_LEVELS,
    DIFFICULTY_SPECS,
    GENERATOR_VERSION,
    MECHANISMS,
    SOURCE_INDICES,
    _json_hash,
    _unique_rows,
    _write_jsonl,
)


CALIBRATOR_VERSION = "mechanism_difficulty_calibrator_v1"
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
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"row {number} is not an object: {path}")
        rows.append(value)
    return rows


def _baseline_contract() -> Dict[str, Any]:
    return {
        "candidate_generation_allowed": False,
        "candidate_execution_allowed": False,
        "operator_library_access_allowed": False,
        "routing_allowed": False,
        "solver": "generic_two_stage_baseline",
    }


def generate_calibration_pool(
    *, output_dir: Path, source_report: Path, seed: int, cases_per_bin: int,
    num_runs: int = 3, min_error_rate: float = 0.15,
    max_error_rate: float = 0.80, min_stable_problems: int = 2,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"calibration output already exists: {output_dir}")
    if not 12 <= cases_per_bin <= 30:
        raise ValueError("cases_per_bin must be between 12 and 30")
    if num_runs != 3:
        raise ValueError("calibration protocol requires exactly three runs")
    source = _read_json(source_report)
    if set(source.get("selected_new_mechanisms", [])) != set(MECHANISMS):
        raise ValueError("source audit does not authorize the five v2 mechanisms")
    output_dir.mkdir(parents=True)
    occupied: set[str] = set()
    rows = []
    strata = []
    for mechanism in MECHANISMS:
        for offset, difficulty in enumerate(DIFFICULTY_LEVELS):
            generated = _unique_rows(
                mechanism,
                seed=seed + offset * 100003,
                count=cases_per_bin,
                occupied=occupied,
                difficulty=difficulty,
            )
            rows.extend(generated)
            strata.append(
                {
                    "mechanism_id": mechanism,
                    "difficulty_bin": difficulty,
                    "parameter_range": DIFFICULTY_SPECS[mechanism][difficulty],
                    "count": len(generated),
                }
            )
    data_path = output_dir / "calibration_pool.jsonl"
    _write_jsonl(data_path, rows)
    manifest = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "calibrator_version": CALIBRATOR_VERSION,
        "family_id": _json_hash(
            {"role": "calibration", "seed": seed, "cases_per_bin": cases_per_bin}
        )[:16],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "experiment_role": "baseline_only_parameter_calibration",
        "source": {
            "audited_119_report": str(source_report.resolve()),
            "audited_119_report_sha256": file_sha256(source_report),
        },
        "baseline_only_contract": _baseline_contract(),
        "selection_unit": "complete_parameter_bin",
        "individual_problem_selection_allowed": False,
        "screening_protocol": {
            "num_runs": num_runs,
            "cases_per_mechanism": cases_per_bin * len(DIFFICULTY_LEVELS),
            "cases_per_parameter_bin": cases_per_bin,
            "min_audited_error_rate": min_error_rate,
            "max_audited_error_rate": max_error_rate,
            "min_stable_failed_problems": min_stable_problems,
            "stable_failure_min_failed_runs": 2,
            "manual_audit_required": True,
            "execution_errors_count_as_model_failures": False,
        },
        "mechanisms": [
            {
                "mechanism_id": mechanism,
                "source_failure_indices": SOURCE_INDICES[mechanism],
                "count": cases_per_bin * len(DIFFICULTY_LEVELS),
            }
            for mechanism in MECHANISMS
        ],
        "parameter_strata": strata,
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


def select_parameter_ranges(
    *, calibration_manifest: Path, raw_summary: Path, audit_queue: Path,
    output_path: Path,
) -> Dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(f"selection output already exists: {output_path}")
    manifest, calibration_rows = load_bundle(calibration_manifest)
    if manifest.get("experiment_role") != "baseline_only_parameter_calibration":
        raise ValueError("manifest is not a parameter calibration pool")
    raw = _read_json(raw_summary)
    if raw.get("manifest_sha256") != manifest["_manifest_sha256"]:
        raise ValueError("raw baseline run does not match calibration manifest")
    observations_path = Path(str(raw["observations"])).resolve()
    if file_sha256(observations_path) != raw.get("observations_sha256"):
        raise ValueError("calibration observations changed after baseline execution")
    observations = _read_jsonl(observations_path)
    audits = _read_jsonl(audit_queue)
    pending = [row.get("audit_id") for row in audits if row.get("audit_label") is None]
    if pending:
        raise ValueError(f"manual calibration audit incomplete: {len(pending)} pending rows")
    invalid_labels = {
        str(row.get("audit_label")) for row in audits
        if row.get("audit_label") not in ALLOWED_AUDIT_LABELS
    }
    if invalid_labels:
        raise ValueError(f"unsupported audit labels: {sorted(invalid_labels)}")
    audit_by_key = {
        (str(row["problem_id"]), int(row["run"])): row for row in audits
    }
    expected_zero_keys = {
        (str(row["problem_id"]), int(row["run"]))
        for row in observations if int(row["score"]) == 0
    }
    if set(audit_by_key) != expected_zero_keys:
        raise ValueError("audit queue does not cover each raw zero exactly once")
    metadata_by_problem = {
        str(row["mechanism_metadata"]["problem_id"]): row["mechanism_metadata"]
        for row in calibration_rows
    }
    invalid_problem_ids = {
        str(row["problem_id"]) for row in audits
        if row["audit_label"] == "invalid_generated_problem"
    }
    grouped: Dict[tuple[str, str], list[Dict[str, Any]]] = defaultdict(list)
    for row in observations:
        problem_id = str(row["problem_id"])
        if problem_id in invalid_problem_ids:
            continue
        metadata = metadata_by_problem.get(problem_id)
        if metadata is None:
            raise ValueError(f"observation is absent from calibration pool: {problem_id}")
        difficulty = str(metadata.get("difficulty_bin", ""))
        if difficulty not in DIFFICULTY_LEVELS:
            raise ValueError(f"missing difficulty bin for {problem_id}")
        grouped[(str(row["mechanism_id"]), difficulty)].append(row)

    protocol = manifest["screening_protocol"]
    strata = []
    selected = []
    for mechanism in MECHANISMS:
        for difficulty in DIFFICULTY_LEVELS:
            rows = grouped[(mechanism, difficulty)]
            failures = []
            scorer_false_negatives = 0
            execution_errors = 0
            failed_runs: Dict[str, int] = defaultdict(int)
            for row in rows:
                if int(row["score"]) == 1:
                    continue
                audit = audit_by_key[(str(row["problem_id"]), int(row["run"]))]
                label = audit["audit_label"]
                if label == "true_model_error":
                    failures.append(row)
                    failed_runs[str(row["problem_id"])] += 1
                elif label == "scorer_false_negative":
                    scorer_false_negatives += 1
                elif label == "execution_error":
                    execution_errors += 1
            stable_count = sum(
                count >= int(protocol["stable_failure_min_failed_runs"])
                for count in failed_runs.values()
            )
            eligible_observations = len(rows) - execution_errors
            error_rate = len(failures) / eligible_observations if eligible_observations else 0.0
            qualifies = (
                error_rate >= float(protocol["min_audited_error_rate"])
                and error_rate <= float(protocol["max_audited_error_rate"])
                and stable_count >= int(protocol["min_stable_failed_problems"])
            )
            item = {
                "mechanism_id": mechanism,
                "difficulty_bin": difficulty,
                "parameter_range": DIFFICULTY_SPECS[mechanism][difficulty],
                "eligible_observations": eligible_observations,
                "audited_true_failures": len(failures),
                "scorer_false_negatives": scorer_false_negatives,
                "execution_errors_excluded": execution_errors,
                "invalid_problems_excluded": sum(
                    metadata_by_problem[problem_id]["family"] == mechanism
                    and metadata_by_problem[problem_id].get("difficulty_bin") == difficulty
                    for problem_id in invalid_problem_ids
                ),
                "audited_error_rate": error_rate,
                "stable_failed_problems": stable_count,
                "selected": qualifies,
            }
            strata.append(item)
            if qualifies:
                selected.append(
                    {
                        "mechanism_id": mechanism,
                        "difficulty_bin": difficulty,
                        "parameter_range": DIFFICULTY_SPECS[mechanism][difficulty],
                    }
                )
    report = {
        "schema_version": 1,
        "report_type": "baseline_only_parameter_range_selection",
        "calibrator_version": CALIBRATOR_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "selected" if selected else "no_qualifying_parameter_range",
        "selection_unit": "complete_parameter_bin",
        "individual_problem_selection_count": 0,
        "calibration_seed": manifest["seed"],
        "calibration_manifest": str(calibration_manifest.resolve()),
        "calibration_manifest_sha256": manifest["_manifest_sha256"],
        "raw_summary": str(raw_summary.resolve()),
        "raw_summary_sha256": file_sha256(raw_summary),
        "observations_sha256": file_sha256(observations_path),
        "audit_queue": str(audit_queue.resolve()),
        "audit_queue_sha256": file_sha256(audit_queue),
        "selection_protocol": protocol,
        "selected_parameter_ranges": selected,
        "strata": strata,
        "visibility_contract": {
            "calibration_problems_visible_to_operator_generator": False,
            "calibration_failures_visible_to_operator_generator": False,
            "visible_selection_information": "mechanism_id and aggregate parameter bin only",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def generate_hidden_confirmation(
    *, output_dir: Path, selection_report: Path, seed: int,
    cases_per_selected_bin: int = 12,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"confirmation output already exists: {output_dir}")
    if not 12 <= cases_per_selected_bin <= 20:
        raise ValueError("cases_per_selected_bin must be between 12 and 20")
    selection = _read_json(selection_report)
    selected = selection.get("selected_parameter_ranges", [])
    if selection.get("status") != "selected" or not selected:
        raise ValueError("no calibrated parameter range is eligible for confirmation")
    calibration_seed = int(selection["calibration_seed"])
    if seed == calibration_seed:
        raise ValueError("hidden confirmation must use a fresh random seed")
    calibration_manifest = Path(selection["calibration_manifest"])
    if file_sha256(calibration_manifest) != selection["calibration_manifest_sha256"]:
        raise ValueError("calibration manifest changed after range selection")
    _, calibration_rows = load_bundle(calibration_manifest)
    occupied = {
        str(row["mechanism_metadata"]["problem_id"]) for row in calibration_rows
    }
    output_dir.mkdir(parents=True)
    rows = []
    for offset, spec in enumerate(selected):
        mechanism = str(spec["mechanism_id"])
        difficulty = str(spec["difficulty_bin"])
        if mechanism not in MECHANISMS or difficulty not in DIFFICULTY_LEVELS:
            raise ValueError(f"invalid selected parameter range: {spec}")
        rows.extend(
            _unique_rows(
                mechanism,
                seed=seed + offset * 100003,
                count=cases_per_selected_bin,
                occupied=occupied,
                difficulty=difficulty,
            )
        )
    data_path = output_dir / "hidden_confirmation.jsonl"
    _write_jsonl(data_path, rows)
    mechanism_counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        mechanism_counts[str(row["mechanism_metadata"]["family"])] += 1
    manifest = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "calibrator_version": CALIBRATOR_VERSION,
        "family_id": _json_hash(
            {"role": "hidden_confirmation", "seed": seed, "selected": selected}
        )[:16],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "experiment_role": "baseline_only_hidden_parameter_confirmation",
        "source": {
            "selection_report": str(selection_report.resolve()),
            "selection_report_sha256": file_sha256(selection_report),
            "calibration_seed": calibration_seed,
            "fresh_seed_verified": True,
            "problem_overlap_with_calibration": 0,
        },
        "baseline_only_contract": _baseline_contract(),
        "hidden_from_operator_generator": True,
        "selected_parameter_ranges": selected,
        "screening_protocol": {
            "num_runs": 3,
            "cases_per_mechanism": None,
            "cases_per_selected_parameter_bin": cases_per_selected_bin,
            "min_audited_error_rate": 0.20,
            "min_stable_failed_problems": 3,
            "stable_failure_min_failed_runs": 2,
            "manual_audit_required": True,
            "execution_errors_count_as_model_failures": False,
        },
        "mechanisms": [
            {
                "mechanism_id": mechanism,
                "source_failure_indices": SOURCE_INDICES[mechanism],
                "count": count,
            }
            for mechanism, count in sorted(mechanism_counts.items())
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Baseline-only mechanism difficulty calibration.")
    sub = parser.add_subparsers(dest="stage", required=True)
    generate = sub.add_parser("generate-calibration")
    generate.add_argument("--output-dir", required=True)
    generate.add_argument("--source-report", required=True)
    generate.add_argument("--seed", type=int, default=97001)
    generate.add_argument("--cases-per-bin", type=int, default=12)
    select = sub.add_parser("select-ranges")
    select.add_argument("--calibration-manifest", required=True)
    select.add_argument("--raw-summary", required=True)
    select.add_argument("--audit-queue", required=True)
    select.add_argument("--output", required=True)
    confirm = sub.add_parser("generate-confirmation")
    confirm.add_argument("--selection-report", required=True)
    confirm.add_argument("--output-dir", required=True)
    confirm.add_argument("--seed", type=int, default=98003)
    confirm.add_argument("--cases-per-selected-bin", type=int, default=12)
    args = parser.parse_args()
    if args.stage == "generate-calibration":
        path = generate_calibration_pool(
            output_dir=Path(args.output_dir), source_report=Path(args.source_report),
            seed=args.seed, cases_per_bin=args.cases_per_bin,
        )
        print(f"Calibration manifest: {path.resolve()}")
        print("Selection unit: complete parameter bin; individual failures cannot be selected.")
    elif args.stage == "select-ranges":
        report = select_parameter_ranges(
            calibration_manifest=Path(args.calibration_manifest),
            raw_summary=Path(args.raw_summary), audit_queue=Path(args.audit_queue),
            output_path=Path(args.output),
        )
        print(f"Selection report: {Path(args.output).resolve()}")
        print(f"Status: {report['status']}")
        print(f"Selected ranges: {report['selected_parameter_ranges']}")
    else:
        path = generate_hidden_confirmation(
            output_dir=Path(args.output_dir), selection_report=Path(args.selection_report),
            seed=args.seed, cases_per_selected_bin=args.cases_per_selected_bin,
        )
        print(f"Hidden confirmation manifest: {path.resolve()}")
        print("Candidate generation/execution remains prohibited.")


if __name__ == "__main__":
    main()
