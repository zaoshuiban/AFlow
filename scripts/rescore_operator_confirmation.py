# -*- coding: utf-8 -*-
"""Offline re-score saved operator-tournament answers without model calls.

The raw confirmation artifacts remain immutable. This tool writes a separate
details JSONL and compact summary that record both the original and current
scores, together with hashes of the source artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from benchmarks.math import MATHBenchmark
from scripts.operator_tournament import calculate_operator_quality


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"row {line_number} is not an object: {path}")
            rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _score_change_counts(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    return {
        "rows": len(rows),
        "baseline_score_changes": sum(
            row["baseline_score"] != row["original_baseline_score"] for row in rows
        ),
        "candidate_score_changes": sum(
            row["candidate_score"] != row["original_candidate_score"] for row in rows
        ),
        "effective_score_changes": sum(
            row["effective_candidate_score"]
            != row["original_effective_candidate_score"]
            for row in rows
        ),
        "paired_outcome_changes": sum(
            (row["candidate_score"] - row["baseline_score"])
            != (
                row["original_candidate_score"]
                - row["original_baseline_score"]
            )
            for row in rows
            if row["split"] == "target"
        ),
    }


def rescore_confirmation(
    *,
    details_path: str | Path,
    summary_path: str | Path,
    output_prefix: str | Path,
) -> tuple[Path, Path, Dict[str, Any]]:
    details_source = Path(details_path).resolve()
    summary_source = Path(summary_path).resolve()
    prefix = Path(output_prefix).resolve()
    output_details = prefix.with_name(prefix.name + "_details.jsonl")
    output_summary = prefix.with_name(prefix.name + "_summary.json")
    if output_details.exists() or output_summary.exists():
        raise FileExistsError(
            f"offline rescore output already exists: {output_details} or {output_summary}"
        )

    with summary_source.open(encoding="utf-8") as handle:
        original_summary = json.load(handle)
    source_rows = _read_jsonl(details_source)
    if not source_rows:
        raise ValueError("confirmation details cannot be empty")

    scorer = MATHBenchmark("MATH", "", str(output_summary.parent))
    rescored_rows: list[Dict[str, Any]] = []
    for row in source_rows:
        required = {
            "operator_name",
            "split",
            "reference_solution",
            "baseline_prediction",
            "candidate_prediction",
            "baseline_score",
            "candidate_score",
            "effective_candidate_score",
        }
        missing = sorted(required - set(row))
        if missing:
            raise ValueError(f"details row is missing fields: {missing}")
        baseline_score, baseline_extracted = scorer.calculate_score(
            str(row["reference_solution"]), str(row["baseline_prediction"])
        )
        candidate_score, candidate_extracted = scorer.calculate_score(
            str(row["reference_solution"]), str(row["candidate_prediction"])
        )
        effective_score = (
            baseline_score
            if row["split"] == "regression" and bool(row.get("declined"))
            else candidate_score
        )
        updated = dict(row)
        updated.update(
            {
                "original_baseline_score": int(row["baseline_score"]),
                "original_candidate_score": int(row["candidate_score"]),
                "original_effective_candidate_score": int(
                    row["effective_candidate_score"]
                ),
                "original_baseline_extracted": row.get("baseline_extracted", ""),
                "original_candidate_extracted": row.get("candidate_extracted", ""),
                "baseline_score": int(baseline_score),
                "candidate_score": int(candidate_score),
                "effective_candidate_score": int(effective_score),
                "baseline_extracted": baseline_extracted,
                "candidate_extracted": candidate_extracted,
                "rescored_with": scorer.SCORER_VERSION,
            }
        )
        rescored_rows.append(updated)

    original_ranking = {
        item["operator_name"]: item.get("metrics", {})
        for item in original_summary.get("ranking", [])
    }
    operator_names = list(original_ranking)
    observed_names = {str(row["operator_name"]) for row in rescored_rows}
    operator_names.extend(sorted(observed_names - set(operator_names)))
    ranking = []
    for name in operator_names:
        rows = [row for row in rescored_rows if row["operator_name"] == name]
        if not rows:
            continue
        target_rows = [row for row in rows if row["split"] == "target"]
        regression_rows = [row for row in rows if row["split"] == "regression"]
        metrics = calculate_operator_quality(target_rows, regression_rows)
        ranking.append(
            {
                "operator_name": name,
                "metrics": metrics,
                "original_metrics": original_ranking.get(name, {}),
                "score_changes": _score_change_counts(rows),
            }
        )
    ranking.sort(
        key=lambda item: (
            not item["metrics"]["passed"],
            -item["metrics"]["quality_score"],
            item["operator_name"],
        )
    )
    passed = [item for item in ranking if item["metrics"]["passed"]]
    winner = passed[0]["operator_name"] if passed else None
    primary = str(original_summary.get("primary_candidate", ""))
    primary_result = next(
        (item for item in ranking if item["operator_name"] == primary), None
    )

    report = {
        "schema_version": 1,
        "report_type": "offline_operator_confirmation_rescore",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "offline_only": True,
        "model_calls_made": 0,
        "source": {
            "summary_path": str(summary_source),
            "summary_sha256": file_sha256(summary_source),
            "details_path": str(details_source),
            "details_sha256": file_sha256(details_source),
            "scorer_version": original_summary.get("scorer_version", "unknown"),
        },
        "scorer_version": scorer.SCORER_VERSION,
        "experiment_role": original_summary.get("experiment_role"),
        "mechanism_family": original_summary.get("mechanism_family"),
        "num_runs": original_summary.get("num_runs"),
        "primary_candidate": primary,
        "primary_candidate_passed": bool(
            primary_result and primary_result["metrics"]["passed"]
        ),
        "winner": winner,
        "promoted": False,
        "promotion_eligible": False,
        "ranking": ranking,
        "details_path": str(output_details),
    }
    output_details.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_details, rescored_rows)
    with output_summary.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output_summary, output_details, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline re-score saved operator confirmation answers."
    )
    parser.add_argument("--details", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output-prefix", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary_path, details_path, report = rescore_confirmation(
        details_path=args.details,
        summary_path=args.summary,
        output_prefix=args.output_prefix,
    )
    print(f"Offline rescore summary: {summary_path}")
    print(f"Offline rescore details: {details_path}")
    for rank, item in enumerate(report["ranking"], 1):
        metrics = item["metrics"]
        marker = " [PRIMARY]" if item["operator_name"] == report["primary_candidate"] else ""
        print(
            f"{rank}. {item['operator_name']}{marker}: "
            f"W/T/L={metrics['wins']}/{metrics['ties']}/{metrics['losses']}, "
            f"passed={metrics['passed']}"
        )


if __name__ == "__main__":
    main()
