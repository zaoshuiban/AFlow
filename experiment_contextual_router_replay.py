# -*- coding: utf-8 -*-
"""Auditable offline replay of contextual routing over legacy paired outputs.

This script intentionally reports two different policies:

* ``current_safe_router_v2`` uses the current persistent library.  The narrowed
  v2 operator is a candidate with no revision-matched evidence, so every item
  must fall back to the generic two-stage output.
* ``legacy_v1_policy_counterfactual`` applies a frozen, post-hoc routing rule to
  the already-generated v1 outputs.  It is a mechanism counterfactual on the
  same validation data used to define the rule, not evidence for v2.

Stored CSV scores are retained only for audit.  All reported scores are
recomputed from predictions with the current ``MATHBenchmark`` scorer.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.math import MATHBenchmark
from scripts.operator_library import (
    OperatorLibrary,
    combined_artifact_sha256,
    semantic_python_sha256,
    stable_problem_id,
)


REPLAY_SCHEMA_VERSION = 2
OPERATOR_NAME = "CombinatorialSymmetryDecomposer"
GENERIC_CONFIG = "generic_two_stage"
LEGACY_CONFIG = "evolved_combo"

# The legacy CSV files were generated against this exact validation file.
# Refusing a different digest prevents an identically indexed but different
# dataset from being silently joined to old predictions.
LEGACY_DATASET_SHA256 = (
    "0ade63e8c9fe01fb8964fa22f73423be5e1c78addc9942808de464cabab188c0"
)
LEGACY_V1_ARTIFACT_SHA256 = (
    "2d91832f35f860114bbe39cc7c6512dcc12267e983c04629f006f3ad99727f60"
)
LEGACY_MATCHED_INDICES = tuple(range(95, 119))

# This is the post-hoc v1 policy whose old-output counterfactual is reported.
# It is deliberately embedded and content-hashed instead of being inferred
# from current scores or current lifecycle status.
LEGACY_V1_POLICY: dict[str, Any] = {
    "name": "legacy_v1_finite_geometric_symmetry_counterfactual",
    "operator_name": OPERATOR_NAME,
    "operator_version": 1,
    "artifact_sha256": LEGACY_V1_ARTIFACT_SHA256,
    "source_config": LEGACY_CONFIG,
    "required_features": [
        "counting_problem",
        "burnside_safe",
        "explicit_rotation_equivalence",
        "explicit_reflection_equivalence",
    ],
    "forbidden_features": [
        "circular_seating",
        "rotation_already_quotiented",
        "continuous_probability",
    ],
    "derivation": (
        "Post-hoc rule derived from the same MATH validation items replayed "
        "here; mechanism counterfactual only."
    ),
}


def sha256_file(path: os.PathLike[str] | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_dataset(path: Path) -> list[dict[str, Any]]:
    actual_hash = sha256_file(path)
    if actual_hash != LEGACY_DATASET_SHA256:
        raise ValueError(
            "Dataset digest does not match the dataset used by the legacy "
            f"outputs: expected {LEGACY_DATASET_SHA256}, got {actual_hash}"
        )
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            item = json.loads(line)
            if not isinstance(item, dict) or not {
                "problem",
                "solution",
                "type",
                "level",
            }.issubset(item):
                raise ValueError(
                    f"Malformed MATH item at {path}:{line_number}"
                )
            rows.append(item)
    return rows


def validate_source_summary(
    summary: dict[str, Any],
    *,
    summary_path: Path,
    data_path: Path,
    data: list[dict[str, Any]],
    num_runs: int,
) -> list[int]:
    arguments = summary.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError(f"Missing arguments object in {summary_path}")
    if int(arguments.get("num_runs", -1)) != num_runs:
        raise ValueError(
            "--num-runs must exactly match the source experiment's num_runs"
        )
    source_data = Path(str(arguments.get("data", ""))).resolve()
    if source_data != data_path.resolve():
        raise ValueError(
            f"Source summary dataset {source_data} does not match {data_path.resolve()}"
        )
    configs = arguments.get("configs")
    if configs != [GENERIC_CONFIG, LEGACY_CONFIG]:
        raise ValueError("Source summary does not contain the exact paired configs")
    if arguments.get("types") != ["Counting & Probability"]:
        raise ValueError(
            "Legacy replay expects the exact Counting & Probability slice"
        )

    raw_indices = summary.get("matched_indices")
    if not isinstance(raw_indices, list) or not raw_indices:
        raise ValueError(f"Missing matched_indices in {summary_path}")
    try:
        indices = [int(index) for index in raw_indices]
    except (TypeError, ValueError) as exc:
        raise ValueError("matched_indices must contain integers") from exc
    if indices != sorted(set(indices)):
        raise ValueError("matched_indices must be sorted and unique")
    if indices != list(LEGACY_MATCHED_INDICES):
        raise ValueError(
            "matched_indices does not match the exact legacy index set"
        )
    if any(index < 0 or index >= len(data) for index in indices):
        raise ValueError("matched_indices contains an out-of-range dataset index")
    wrong_types = [
        index
        for index in indices
        if data[index].get("type") != "Counting & Probability"
    ]
    if wrong_types:
        raise ValueError(f"matched_indices contains wrong problem types: {wrong_types}")
    return indices


def load_rows_strict(
    path: Path,
    *,
    expected_indices: list[int],
    data: list[dict[str, Any]],
) -> dict[int, dict[str, str]]:
    required_columns = {
        "index",
        "type",
        "level",
        "prediction",
        "score",
        "call_count",
        "error",
    }
    rows: dict[int, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required_columns.issubset(reader.fieldnames):
            missing = sorted(required_columns - set(reader.fieldnames or []))
            raise ValueError(f"Missing CSV columns in {path}: {missing}")
        for line_number, row in enumerate(reader, 2):
            try:
                index = int(row["index"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid index at {path}:{line_number}") from exc
            if index in rows:
                raise ValueError(f"Duplicate index {index} in {path}")
            if index < 0 or index >= len(data):
                raise ValueError(f"Out-of-range index {index} in {path}")
            if row.get("type") != str(data[index].get("type")):
                raise ValueError(f"Type mismatch for index {index} in {path}")
            if row.get("level") != str(data[index].get("level")):
                raise ValueError(f"Level mismatch for index {index} in {path}")
            if (row.get("error") or "").strip():
                raise ValueError(
                    f"Replay refuses errored source row index {index} in {path}"
                )
            try:
                call_count = int(row.get("call_count", ""))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid call_count for index {index} in {path}"
                ) from exc
            if call_count != 2:
                raise ValueError(
                    f"Unequal-call source row index {index} in {path}: {call_count}"
                )
            if not (row.get("prediction") or "").strip():
                raise ValueError(f"Empty prediction for index {index} in {path}")
            rows[index] = row

    actual_indices = sorted(rows)
    if actual_indices != expected_indices:
        missing = sorted(set(expected_indices) - set(actual_indices))
        extra = sorted(set(actual_indices) - set(expected_indices))
        raise ValueError(
            f"Index set mismatch in {path}; missing={missing}, extra={extra}"
        )
    return rows


def _artifact_manifest(
    artifact: dict[str, Any], *, label: str
) -> dict[str, Any]:
    class_path = Path(str(artifact.get("class_code_path", "")))
    prompt_path = Path(str(artifact.get("prompt_code_path", "")))
    if not class_path.is_file() or not prompt_path.is_file():
        raise ValueError(f"Missing {label} archived artifact files")
    class_code = class_path.read_text(encoding="utf-8")
    prompt_code = prompt_path.read_text(encoding="utf-8")
    computed = {
        "class_sha256": semantic_python_sha256(class_code),
        "prompt_sha256": semantic_python_sha256(prompt_code),
        "artifact_sha256": combined_artifact_sha256(class_code, prompt_code),
    }
    for key, actual in computed.items():
        expected = artifact.get(key)
        if expected != actual:
            raise ValueError(
                f"{label} artifact integrity failure for {key}: "
                f"expected {expected}, got {actual}"
            )
    return {
        "label": label,
        "class_path": str(class_path),
        "prompt_path": str(prompt_path),
        "class_file_sha256": sha256_file(class_path),
        "prompt_file_sha256": sha256_file(prompt_path),
        **computed,
    }


def validate_operator_revisions(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("name") != OPERATOR_NAME:
        raise ValueError(f"Missing operator record for {OPERATOR_NAME}")
    if record.get("version") != 2 or record.get("status") != "candidate":
        raise ValueError(
            "Current replay requires the narrowed v2 record to remain a candidate"
        )
    current = _artifact_manifest(record.get("artifact", {}), label="current_v2")

    historical = [
        item
        for item in record.get("historical_versions", [])
        if item.get("version") == 1
    ]
    if len(historical) != 1:
        raise ValueError("Expected exactly one archived legacy v1 revision")
    legacy_record = historical[0]
    if legacy_record.get("status") != "retired":
        raise ValueError("Legacy v1 revision must be retired")
    legacy = _artifact_manifest(
        legacy_record.get("artifact", {}), label="legacy_v1"
    )
    if legacy["artifact_sha256"] != LEGACY_V1_ARTIFACT_SHA256:
        raise ValueError("Legacy policy artifact digest mismatch")

    evidence = record.get("evidence", [])
    if any(item.get("routing_eligible") is True for item in evidence):
        raise ValueError("Legacy evidence must not be routing-eligible for v2")
    v1_digests = {
        item.get("artifact_sha256")
        for item in evidence
        if item.get("operator_version") == 1
    }
    if v1_digests != {legacy["artifact_sha256"]}:
        raise ValueError("Legacy evidence is not bound to the archived v1 artifact")
    return {"current_v2": current, "legacy_v1": legacy}


def legacy_v1_policy_matches(features: set[str]) -> bool:
    required = set(LEGACY_V1_POLICY["required_features"])
    forbidden = set(LEGACY_V1_POLICY["forbidden_features"])
    return required.issubset(features) and not bool(forbidden & features)


def _wtl(candidate_score: int, baseline_score: int) -> str:
    if candidate_score > baseline_score:
        return "win"
    if candidate_score < baseline_score:
        return "loss"
    return "tie"


def _summarize_runs(runs: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    return {
        "correct": sum(item[f"{prefix}_correct"] for item in runs),
        "trial_instances": sum(item["total"] for item in runs),
        "selected_trial_instances": sum(
            item.get(f"{prefix}_selected", 0) for item in runs
        ),
        "wins": sum(item.get(f"{prefix}_wins", 0) for item in runs),
        "ties": sum(item.get(f"{prefix}_ties", 0) for item in runs),
        "losses": sum(item.get(f"{prefix}_losses", 0) for item in runs),
    }


def build_replay(args: argparse.Namespace) -> dict[str, Any]:
    data_path = Path(args.data)
    input_dir = Path(args.input_dir)
    library_path = Path(args.library)
    source_summary_path = input_dir / "summary.json"

    data = load_dataset(data_path)
    source_summary = _read_json(source_summary_path)
    expected_indices = validate_source_summary(
        source_summary,
        summary_path=source_summary_path,
        data_path=data_path,
        data=data,
        num_runs=args.num_runs,
    )

    library = OperatorLibrary(library_path, auto_create=False)
    record = library.get(OPERATOR_NAME)
    if record is None:
        raise ValueError(f"No {OPERATOR_NAME} record in {library_path}")
    artifact_manifests = validate_operator_revisions(record)

    benchmark = MATHBenchmark(
        name="MATH",
        file_path=str(data_path),
        log_path=str(Path(args.output).parent / "replay_mismatches_unused"),
    )

    run_summaries: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    source_files: list[dict[str, str]] = []
    current_selected_indices: set[int] = set()
    legacy_selected_indices: set[int] = set()
    legacy_winning_indices: set[int] = set()
    score_changes = {GENERIC_CONFIG: 0, LEGACY_CONFIG: 0}

    for run in range(1, args.num_runs + 1):
        generic_path = input_dir / GENERIC_CONFIG / f"run_{run}.csv"
        legacy_path = input_dir / LEGACY_CONFIG / f"run_{run}.csv"
        generic = load_rows_strict(
            generic_path, expected_indices=expected_indices, data=data
        )
        legacy = load_rows_strict(
            legacy_path, expected_indices=expected_indices, data=data
        )
        source_files.extend(
            [
                {"path": str(generic_path), "sha256": sha256_file(generic_path)},
                {"path": str(legacy_path), "sha256": sha256_file(legacy_path)},
            ]
        )

        counters = {
            "generic_correct": 0,
            "legacy_v1_unconditional_correct": 0,
            "current_safe_router_v2_correct": 0,
            "legacy_v1_policy_counterfactual_correct": 0,
            "current_safe_router_v2_selected": 0,
            "legacy_v1_policy_counterfactual_selected": 0,
            "legacy_v1_unconditional_wins": 0,
            "legacy_v1_unconditional_ties": 0,
            "legacy_v1_unconditional_losses": 0,
            "current_safe_router_v2_wins": 0,
            "current_safe_router_v2_ties": 0,
            "current_safe_router_v2_losses": 0,
            "legacy_v1_policy_counterfactual_wins": 0,
            "legacy_v1_policy_counterfactual_ties": 0,
            "legacy_v1_policy_counterfactual_losses": 0,
        }

        for index in expected_indices:
            item = data[index]
            generic_row = generic[index]
            legacy_row = legacy[index]
            generic_score, generic_extracted = benchmark.calculate_score(
                item["solution"], generic_row["prediction"]
            )
            legacy_score, legacy_extracted = benchmark.calculate_score(
                item["solution"], legacy_row["prediction"]
            )
            generic_stored = int(generic_row["score"])
            legacy_stored = int(legacy_row["score"])
            if generic_stored != generic_score:
                score_changes[GENERIC_CONFIG] += 1
            if legacy_stored != legacy_score:
                score_changes[LEGACY_CONFIG] += 1

            decision = library.retrieve(
                item["problem"], min_score=args.min_score
            )
            current_selected = (
                decision.selected.operator_name if decision.selected else ""
            )
            if current_selected:
                current_selected_indices.add(index)
                raise ValueError(
                    "Current v2 is a candidate and must not select any source "
                    f"item; selected {current_selected} for index {index}"
                )
            current_score = generic_score

            features = set(decision.context.features)
            legacy_selected = legacy_v1_policy_matches(features)
            legacy_counterfactual_score = (
                legacy_score if legacy_selected else generic_score
            )
            if legacy_selected:
                legacy_selected_indices.add(index)
                if legacy_counterfactual_score > generic_score:
                    legacy_winning_indices.add(index)

            counters["generic_correct"] += generic_score
            counters["legacy_v1_unconditional_correct"] += legacy_score
            counters["current_safe_router_v2_correct"] += current_score
            counters[
                "legacy_v1_policy_counterfactual_correct"
            ] += legacy_counterfactual_score
            counters["current_safe_router_v2_selected"] += int(
                bool(current_selected)
            )
            counters["legacy_v1_policy_counterfactual_selected"] += int(
                legacy_selected
            )
            current_outcome = _wtl(current_score, generic_score)
            unconditional_outcome = _wtl(legacy_score, generic_score)
            legacy_outcome = _wtl(legacy_counterfactual_score, generic_score)
            outcome_keys = {"win": "wins", "tie": "ties", "loss": "losses"}
            counters[
                f"legacy_v1_unconditional_{outcome_keys[unconditional_outcome]}"
            ] += 1
            counters[
                f"current_safe_router_v2_{outcome_keys[current_outcome]}"
            ] += 1
            counters[
                "legacy_v1_policy_counterfactual_"
                f"{outcome_keys[legacy_outcome]}"
            ] += 1

            all_rows.append(
                {
                    "run": run,
                    "index": index,
                    "problem_id": stable_problem_id(item["problem"]),
                    "expected_extracted": benchmark.extract_model_answer(
                        item["solution"]
                    ),
                    "features": sorted(features),
                    "generic_two_stage": {
                        "stored_score": generic_stored,
                        "rescored_score": generic_score,
                        "extracted": generic_extracted,
                        "prediction_sha256": hashlib.sha256(
                            generic_row["prediction"].encode("utf-8")
                        ).hexdigest(),
                    },
                    "legacy_v1_unconditional": {
                        "stored_score": legacy_stored,
                        "rescored_score": legacy_score,
                        "extracted": legacy_extracted,
                        "prediction_sha256": hashlib.sha256(
                            legacy_row["prediction"].encode("utf-8")
                        ).hexdigest(),
                    },
                    "current_safe_router_v2": {
                        "selected_operator": current_selected,
                        "source": GENERIC_CONFIG,
                        "score": current_score,
                        "blocked": decision.blocked,
                        "candidates": [
                            candidate.to_dict()
                            for candidate in decision.candidates
                        ],
                    },
                    "legacy_v1_policy_counterfactual": {
                        "selected_operator": (
                            OPERATOR_NAME if legacy_selected else ""
                        ),
                        "source": LEGACY_CONFIG if legacy_selected else GENERIC_CONFIG,
                        "score": legacy_counterfactual_score,
                        "outcome_vs_generic": legacy_outcome,
                    },
                }
            )

        run_summaries.append(
            {"run": run, "total": len(expected_indices), **counters}
        )

    aggregate = {
        "unique_problems": len(expected_indices),
        "trial_instances": len(expected_indices) * args.num_runs,
        "generic_two_stage": _summarize_runs(run_summaries, "generic"),
        "legacy_v1_unconditional": _summarize_runs(
            run_summaries, "legacy_v1_unconditional"
        ),
        "current_safe_router_v2": _summarize_runs(
            run_summaries, "current_safe_router_v2"
        ),
        "legacy_v1_policy_counterfactual": _summarize_runs(
            run_summaries, "legacy_v1_policy_counterfactual"
        ),
        "current_safe_router_v2_unique_selected": len(current_selected_indices),
        "legacy_v1_policy_unique_selected": len(legacy_selected_indices),
        "legacy_v1_policy_unique_winning": len(legacy_winning_indices),
        "mean_legacy_selected_per_run": statistics.mean(
            item["legacy_v1_policy_counterfactual_selected"]
            for item in run_summaries
        ),
        "stored_score_changes_after_current_rescore": score_changes,
    }

    scorer_path = Path(__file__).resolve().parent / "benchmarks" / "math.py"
    script_path = Path(__file__).resolve()
    output = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": {
            "current_safe_router_v2": (
                "Current executable policy. v2 is a candidate with no "
                "revision-matched routing evidence, so this policy is all fallback."
            ),
            "legacy_v1_policy_counterfactual": (
                "Post-hoc mechanism counterfactual over legacy v1 outputs on "
                "the same validation data used to define the rule."
            ),
        },
        "provenance": {
            "dataset": {
                "path": str(data_path),
                "sha256": sha256_file(data_path),
                "row_count": len(data),
            },
            "scorer": {
                "class": "benchmarks.math.MATHBenchmark",
                "path": str(scorer_path),
                "sha256": sha256_file(scorer_path),
            },
            "library": {
                "path": str(library_path),
                "sha256": sha256_file(library_path),
                "operator_name": OPERATOR_NAME,
                "operator_version": record.get("version"),
                "operator_status": record.get("status"),
            },
            "artifacts": artifact_manifests,
            "replay_script": {
                "path": str(script_path),
                "sha256": sha256_file(script_path),
            },
            "source_experiment": {
                "summary_path": str(source_summary_path),
                "summary_sha256": sha256_file(source_summary_path),
                "configs": [GENERIC_CONFIG, LEGACY_CONFIG],
                "num_runs": args.num_runs,
                "matched_indices": expected_indices,
                "files": source_files,
            },
        },
        "policies": {
            "current_safe_router_v2": {
                "source": "workspace operator library lifecycle and evidence gates",
                "library_sha256": sha256_file(library_path),
            },
            "legacy_v1_policy_counterfactual": {
                **LEGACY_V1_POLICY,
                "policy_sha256": canonical_json_sha256(LEGACY_V1_POLICY),
            },
        },
        "validation": {
            "dataset_digest_matches_legacy_source": True,
            "source_summary_matches_dataset_and_slice": True,
            "every_csv_has_exact_index_set": True,
            "every_source_row_has_two_calls_and_no_error": True,
            "artifact_integrity_verified": True,
            "current_v2_candidate_selected_zero_items": True,
        },
        "runs": run_summaries,
        "aggregate": aggregate,
        "rows": all_rows,
        "caveats": [
            "The current v2 result is an all-fallback safety check, not a v2 effectiveness result.",
            "The legacy v1 policy is post-hoc and replayed on the same validation data used to derive it.",
            "Repeated runs are stochastic trials on 24 fixed problems, not 72 independent problems.",
            "Only one unique problem contributes a legacy-policy win; no significance or generalization claim is supported.",
        ],
    }
    return output


def main(args: argparse.Namespace) -> None:
    output = build_replay(args)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(json.dumps(output["aggregate"], indent=2, ensure_ascii=False))
    for item in output["runs"]:
        print(
            f"run={item['run']}: generic={item['generic_correct']}/{item['total']}, "
            f"legacy_v1={item['legacy_v1_unconditional_correct']}/{item['total']}, "
            "current_safe_v2="
            f"{item['current_safe_router_v2_correct']}/{item['total']} "
            f"(selected={item['current_safe_router_v2_selected']}), "
            "legacy_policy_cf="
            f"{item['legacy_v1_policy_counterfactual_correct']}/{item['total']} "
            f"(selected={item['legacy_v1_policy_counterfactual_selected']})"
        )
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-dir", default="workspace/MATH/operator_direct_combo_full"
    )
    parser.add_argument(
        "--library", default="workspace/MATH/operator_library/operators.json"
    )
    parser.add_argument("--data", default="data/datasets/math_validate.jsonl")
    parser.add_argument("--num-runs", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=0.70)
    parser.add_argument(
        "--output", default="workspace/MATH/operator_library/replay_summary.json"
    )
    main(parser.parse_args())
