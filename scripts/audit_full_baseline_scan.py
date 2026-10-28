# -*- coding: utf-8 -*-
"""Offline audit and mechanism clustering for the saved 119-problem baseline.

The script never calls an LLM.  It reuses saved responses, applies the current
MATH scorer, separates execution failures, and requires a complete problem-level
manual audit ledger before exposing any new mechanism cluster for screening.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from benchmarks.math import MATHBenchmark
from scripts.baseline_failure_mechanisms import file_sha256


REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_LABELS = {"true_model_error", "scorer_false_negative", "invalid_source"}
EXCLUDED_MECHANISMS = {
    "irregular_grid_rectangles": "screen_v1 rejected",
    "top_k_replacement_mean": "screen_v1 rejected",
    "conditional_reroll_multiplicity": "screen_v1 rejected",
    "distinct_label_symmetry_quotient": "screen_v1 rejected",
    "circular_mutual_nonadjacency": "screen_v1 rejected",
    "parametric_path_retracing": "trajectory v5 frozen; do not extend",
}


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value.resolve() if value.is_absolute() else (REPO_ROOT / value).resolve()


def _load_jsonl(path: Path) -> list[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _read_csv(path: Path, run: int, source: str) -> list[Dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["_run"] = run
        row["_source"] = source
        row["_csv_path"] = str(path)
    return rows


def _execution_issue(row: Mapping[str, Any]) -> str:
    error = str(row.get("error", "")).strip()
    prediction = str(row.get("prediction", "")).strip()
    if error:
        return error
    if not prediction or prediction.upper() == "TIMEOUT":
        return "empty_or_timeout_prediction"
    return ""


def mechanism_for(index: int, problem: str) -> str:
    """Deterministic reasoning-mechanism labels, not topic labels."""
    text = " ".join(problem.lower().split())
    if "inserting parentheses" in text:
        return "parenthesization_distinct_value_dedup"
    if "at least two" in text and "as digits" in text:
        return "repeated_digit_interval_inclusion_exclusion"
    if "pounds" in text and "euros" in text and "round" in text:
        return "staged_conversion_rounding"
    if "arrange them to form a number" in text and "prime" in text:
        return "restricted_digit_prime_enumeration"
    if "all possible values" in text and "angle" in text:
        return "domain_filtered_trigonometric_branches"
    if "rectangles are in this figure" in text:
        return "irregular_grid_rectangles"
    if "paint trail" in text:
        return "parametric_path_retracing"
    if "re-rolled" in text and "full house" in text:
        return "conditional_reroll_multiplicity"
    if "circular table" in text or "round table" in text:
        return "circular_mutual_nonadjacency"
    return f"unclustered_index_{index}"


def _load_labels(path: Path | None) -> Dict[str, Dict[str, str]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("labels", [])
    labels: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        problem_id = str(entry["problem_id"])
        label = str(entry["audit_label"])
        if label not in ALLOWED_LABELS:
            raise ValueError(f"unsupported label for {problem_id}: {label}")
        if problem_id in labels:
            raise ValueError(f"duplicate problem audit: {problem_id}")
        labels[problem_id] = {
            "audit_label": label,
            "audit_notes": str(entry.get("audit_notes", "")),
        }
    return labels


def build_report(
    *, dataset_path: Path, full_csv: Path, repeat_dir: Path,
    labels_path: Path | None = None,
) -> Dict[str, Any]:
    dataset = _load_jsonl(dataset_path)
    if len(dataset) != 119:
        raise ValueError(f"expected the complete 119-problem dataset, got {len(dataset)}")
    full_rows = _read_csv(full_csv, 0, "full_scan")
    if len(full_rows) != 119 or {int(row["index"]) for row in full_rows} != set(range(119)):
        raise ValueError("full scan CSV must cover every index 0..118 exactly once")
    repeat_paths = sorted(repeat_dir.glob("run_*.csv"))
    repeat_rows: list[Dict[str, Any]] = []
    for run, path in enumerate(repeat_paths, 1):
        repeat_rows.extend(_read_csv(path, run, "repeat"))
    benchmark = MATHBenchmark("MATH", str(dataset_path), str(repeat_dir))
    labels = _load_labels(labels_path)
    observations = []
    for row in [*full_rows, *repeat_rows]:
        index = int(row["index"])
        expected = str(dataset[index]["solution"])
        issue = _execution_issue(row)
        if issue:
            new_score, extracted = 0, str(row.get("extracted", ""))
            status = "execution_issue"
        else:
            new_score, extracted = benchmark.calculate_score(
                expected, str(row.get("prediction", ""))
            )
            if new_score:
                status = (
                    "scorer_false_negative"
                    if int(float(row.get("score", 0) or 0)) == 0
                    else "correct"
                )
            else:
                status = "semantic_mismatch"
        observations.append(
            {
                "source": row["_source"],
                "run": row["_run"],
                "index": index,
                "problem_id": str(row["problem_id"]),
                "old_score": int(float(row.get("score", 0) or 0)),
                "new_score": int(new_score),
                "status": status,
                "execution_issue": issue,
                "old_extracted": str(row.get("extracted", "")),
                "new_extracted": extracted,
                "prediction": str(row.get("prediction", "")),
            }
        )

    semantic_problem_ids = {
        row["problem_id"] for row in observations
        if row["status"] == "semantic_mismatch"
    }
    if labels_path is not None and set(labels) != semantic_problem_ids:
        raise ValueError(
            "manual audit ledger must cover each semantic-mismatch problem exactly: "
            f"missing={sorted(semantic_problem_ids-set(labels))}, "
            f"extra={sorted(set(labels)-semantic_problem_ids)}"
        )

    grouped: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for row in observations:
        grouped[row["problem_id"]].append(row)
    problems = []
    clusters: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for problem_id, rows in sorted(grouped.items(), key=lambda item: int(item[1][0]["index"])):
        index = int(rows[0]["index"])
        item = dataset[index]
        mismatches = [row for row in rows if row["status"] == "semantic_mismatch"]
        executions = [row for row in rows if row["status"] == "execution_issue"]
        repeats = [row for row in rows if row["source"] == "repeat" and not row["execution_issue"]]
        repeat_failures = [row for row in repeats if row["status"] == "semantic_mismatch"]
        audit = labels.get(problem_id)
        audited_true = bool(mismatches and audit and audit["audit_label"] == "true_model_error")
        stable = bool(
            audited_true and len(repeats) >= 3 and len(repeat_failures) >= 2
        )
        mechanism = mechanism_for(index, str(item["problem"])) if audited_true else ""
        record = {
            "index": index,
            "problem_id": problem_id,
            "type": item.get("type", "unknown"),
            "level": item.get("level", "unknown"),
            "problem": item["problem"],
            "reference_answer": benchmark.extract_model_answer(str(item["solution"])),
            "semantic_mismatch_events": len(mismatches),
            "execution_issue_events": len(executions),
            "repeat_valid_runs": len(repeats),
            "repeat_failure_runs": len(repeat_failures),
            "stable_real_failure": stable,
            "manual_audit": audit,
            "mechanism_id": mechanism,
            "excluded_prior_mechanism": mechanism in EXCLUDED_MECHANISMS,
            "exclusion_reason": EXCLUDED_MECHANISMS.get(mechanism, ""),
        }
        problems.append(record)
        if audited_true:
            clusters[mechanism].append(record)

    cluster_rows = []
    selectable = []
    for mechanism, members in sorted(clusters.items()):
        excluded = mechanism in EXCLUDED_MECHANISMS or mechanism.startswith("unclustered_")
        cluster = {
            "mechanism_id": mechanism,
            "source_indices": sorted(row["index"] for row in members),
            "source_problem_ids": sorted(row["problem_id"] for row in members),
            "stable_source_failures": sum(row["stable_real_failure"] for row in members),
            "excluded": excluded,
            "exclusion_reason": EXCLUDED_MECHANISMS.get(
                mechanism, "unclustered mechanism" if excluded else ""
            ),
            "selected_for_baseline_only_screening": bool(labels_path and not excluded),
        }
        cluster_rows.append(cluster)
        if cluster["selected_for_baseline_only_screening"]:
            selectable.append(mechanism)

    pending = sorted(semantic_problem_ids - set(labels))
    return {
        "schema_version": 1,
        "report_type": "audited_complete_119_baseline_recluster",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if labels_path is not None else "pending_manual_audit",
        "scorer": {
            "version": benchmark.SCORER_VERSION,
            "path": str((REPO_ROOT / "benchmarks/math.py").resolve()),
            "sha256": file_sha256(REPO_ROOT / "benchmarks/math.py"),
        },
        "sources": {
            "dataset": str(dataset_path),
            "dataset_sha256": file_sha256(dataset_path),
            "full_scan": str(full_csv),
            "full_scan_sha256": file_sha256(full_csv),
            "repeat_runs": [
                {"path": str(path), "sha256": file_sha256(path)} for path in repeat_paths
            ],
            "audit_labels": str(labels_path) if labels_path else None,
            "audit_labels_sha256": file_sha256(labels_path) if labels_path else None,
        },
        "summary": {
            "dataset_rows": len(dataset),
            "saved_observations": len(observations),
            "offline_scorer_corrections": sum(
                row["status"] == "scorer_false_negative" for row in observations
            ),
            "execution_issues_excluded": sum(
                row["status"] == "execution_issue" for row in observations
            ),
            "semantic_mismatch_events": sum(
                row["status"] == "semantic_mismatch" for row in observations
            ),
            "semantic_mismatch_problems": len(semantic_problem_ids),
            "pending_manual_problem_audits": len(pending),
            "stable_real_failure_problems": sum(
                row["stable_real_failure"] for row in problems
            ),
            "selected_new_mechanisms": len(selectable),
            "status_counts": dict(sorted(Counter(row["status"] for row in observations).items())),
        },
        "excluded_mechanisms": EXCLUDED_MECHANISMS,
        "selected_new_mechanisms": selectable,
        "clusters": cluster_rows,
        "problems": problems,
        "observations": observations,
        "pending_manual_problem_ids": pending,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit saved complete MATH baseline runs.")
    parser.add_argument("--dataset", default="data/datasets/math_validate.jsonl")
    parser.add_argument(
        "--full-csv",
        default="workspace/MATH/baseline_v3_full_scan_20260809/generic_two_stage/run_1.csv",
    )
    parser.add_argument(
        "--repeat-dir",
        default="workspace/MATH/baseline_v3_failure_repeat_20260809/generic_two_stage",
    )
    parser.add_argument("--audit-labels", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    labels = _resolve(args.audit_labels) if args.audit_labels else None
    report = build_report(
        dataset_path=_resolve(args.dataset),
        full_csv=_resolve(args.full_csv),
        repeat_dir=_resolve(args.repeat_dir),
        labels_path=labels,
    )
    output = _resolve(args.output)
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print(f"Report: {output}")
    print(f"Selected new mechanisms: {report['selected_new_mechanisms']}")


if __name__ == "__main__":
    main()
