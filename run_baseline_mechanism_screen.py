# -*- coding: utf-8 -*-
"""Run and audit baseline-only mechanism headroom screening.

This module intentionally has no dependency on the operator library, routing,
candidate artifacts, or operator-evolution code. It executes only the generic
two-stage baseline through ``scripts.operators.Custom``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from benchmarks.math import MATHBenchmark
from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.baseline_failure_mechanisms import file_sha256, load_bundle
from scripts.operators import Custom


REPO_ROOT = Path(__file__).resolve().parent
ALLOWED_AUDIT_LABELS = {
    "true_model_error",
    "scorer_false_negative",
    "invalid_generated_problem",
    "execution_error",
}
GENERIC_ANALYSIS_PROMPT = (
    "Analyze the mathematical problem. Identify its type, constraints, useful "
    "theorems, and a reliable solution plan. Do not guess the final answer.\n"
)
GENERIC_SOLVE_PROMPT = (
    "Use the supplied analysis to solve the problem carefully. Verify every "
    "step and put the final answer in \\boxed{}.\n"
)


class BaselineTwoStageSolver:
    """The generic two-call control used for headroom measurement."""

    def __init__(self, llm: Any):
        self.custom = Custom(llm, "BaselineHeadroomCustom")

    async def __call__(self, problem: str) -> Dict[str, str]:
        analysis = await self.custom(
            input=problem, instruction=GENERIC_ANALYSIS_PROMPT
        )
        if analysis.get("status") == "error":
            return {
                "status": "error",
                "response": "",
                "observation": f"analysis_failed:{analysis.get('observation', '')}",
            }
        combined = (
            f"Problem:\n{problem}\n\nAnalysis:\n"
            f"{analysis.get('response', '')}"
        )
        result = await self.custom(
            input=combined, instruction=GENERIC_SOLVE_PROMPT
        )
        if result.get("status") == "error":
            return {
                "status": "error",
                "response": "",
                "observation": f"solve_failed:{result.get('observation', '')}",
            }
        return {
            "status": "success",
            "response": str(result.get("response", "")),
            "observation": "",
        }


def _model_config(name: str) -> Any:
    try:
        return LLMsConfig.default().get(name)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: str | Path) -> list[Dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"row {line_number} is not an object: {path}")
            rows.append(value)
    return rows


def apply_audit_labels(
    *, audit_queue_path: str | Path, labels_path: str | Path,
) -> Dict[str, int]:
    """Apply a separately reviewed label ledger to an existing JSONL queue."""
    queue_path = Path(audit_queue_path).resolve()
    ledger_path = Path(labels_path).resolve()
    queue = _read_jsonl(queue_path)
    with ledger_path.open(encoding="utf-8") as handle:
        ledger = json.load(handle)
    entries = ledger.get("labels")
    if not isinstance(entries, list):
        raise ValueError("audit label ledger must contain a labels list")
    labels_by_id: Dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("audit_id"):
            raise ValueError("every audit label entry must be an object with audit_id")
        audit_id = str(entry["audit_id"])
        if audit_id in labels_by_id:
            raise ValueError(f"duplicate audit label: {audit_id}")
        if entry.get("audit_label") not in ALLOWED_AUDIT_LABELS:
            raise ValueError(
                f"unsupported manual audit label for {audit_id}: "
                f"{entry.get('audit_label')}"
            )
        labels_by_id[audit_id] = entry
    queue_ids = {str(row["audit_id"]) for row in queue}
    automatic_execution_ids = {
        str(row["audit_id"]): row
        for row in queue if str(row.get("execution_error", "")).strip()
    }
    required_manual_ids = queue_ids - set(automatic_execution_ids)
    supplied_ids = set(labels_by_id)
    if supplied_ids != queue_ids and supplied_ids != required_manual_ids:
        missing = sorted(required_manual_ids - supplied_ids)
        extra = sorted(set(labels_by_id) - queue_ids)
        raise ValueError(f"audit ledger coverage mismatch: missing={missing}, extra={extra}")
    counts: Dict[str, int] = defaultdict(int)
    for row in queue:
        audit_id = str(row["audit_id"])
        entry = labels_by_id.get(audit_id)
        if entry is None:
            source = automatic_execution_ids[audit_id]
            entry = {
                "audit_label": "execution_error",
                "audit_notes": (
                    "Automatically classified from the saved nonempty execution_error "
                    f"field: {source.get('execution_error', '')}"
                ),
            }
        row["audit_label"] = entry["audit_label"]
        row["audit_notes"] = str(entry.get("audit_notes", ""))
        counts[str(entry["audit_label"])] += 1
    temporary = queue_path.with_suffix(queue_path.suffix + ".tmp")
    _write_jsonl(temporary, queue)
    temporary.replace(queue_path)
    return dict(sorted(counts.items()))


def summarize_raw_observations(
    observations: Sequence[Mapping[str, Any]], protocol: Mapping[str, Any]
) -> list[Dict[str, Any]]:
    minimum_error_rate = protocol.get(
        "min_audited_error_rate", protocol.get("min_operational_failure_rate")
    )
    if minimum_error_rate is None:
        raise ValueError("screening protocol does not define a minimum failure rate")
    grouped: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in observations:
        grouped[str(row["mechanism_id"])].append(row)
    summaries = []
    for mechanism in sorted(grouped):
        rows = grouped[mechanism]
        failures = [row for row in rows if int(row["score"]) == 0]
        failed_runs: Dict[str, int] = defaultdict(int)
        for row in failures:
            failed_runs[str(row["problem_id"])] += 1
        stable_ids = sorted(
            problem_id
            for problem_id, count in failed_runs.items()
            if count >= int(protocol["stable_failure_min_failed_runs"])
        )
        error_rate = len(failures) / len(rows) if rows else 0.0
        raw_eligible = (
            error_rate >= float(minimum_error_rate)
            and len(stable_ids) >= int(protocol["min_stable_failed_problems"])
        )
        summaries.append(
            {
                "mechanism_id": mechanism,
                "observations": len(rows),
                "unique_problems": len({str(row["problem_id"]) for row in rows}),
                "raw_failures": len(failures),
                "raw_error_rate": error_rate,
                "raw_stable_failed_problems": len(stable_ids),
                "raw_stable_failed_problem_ids": stable_ids,
                "preliminary_headroom_eligible": raw_eligible,
                "status": "pending_manual_error_audit" if failures else "rejected_no_failures",
            }
        )
    return summaries


def build_raw_report(
    *, manifest: Mapping[str, Any], manifest_path: str | Path,
    model: str, data_count: int, observations: Sequence[Mapping[str, Any]],
    details_path: Path, audit_path: Path, recovered: bool = False,
) -> Dict[str, Any]:
    protocol = manifest["screening_protocol"]
    audit_queue = _read_jsonl(audit_path)
    mechanisms = summarize_raw_observations(observations, protocol)
    return {
        "schema_version": 1,
        "report_type": "baseline_only_mechanism_headroom_screen",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_manual_error_audit",
        "recovered_from_saved_observations": recovered,
        "manifest": str(Path(manifest_path).resolve()),
        "manifest_sha256": manifest["_manifest_sha256"],
        "model": model,
        "scorer_version": MATHBenchmark.SCORER_VERSION,
        "num_runs": int(protocol["num_runs"]),
        "candidate_generation_invocations": 0,
        "candidate_execution_invocations": 0,
        "operator_library_accesses": 0,
        "routing_invocations": 0,
        "protocol": protocol,
        "mechanisms": mechanisms,
        "totals": {
            "problems": data_count,
            "observations": len(observations),
            "raw_failures_requiring_audit": len(audit_queue),
            "tokens": sum(int(row.get("tokens", 0)) for row in observations),
            "calls": sum(int(row.get("calls", 0)) for row in observations),
            "cost": sum(float(row.get("cost", 0.0)) for row in observations),
            "execution_errors": sum(bool(row.get("error")) for row in observations),
        },
        "observations": str(details_path.resolve()),
        "observations_sha256": file_sha256(details_path),
        "manual_audit_queue": str(audit_path.resolve()),
        "manual_audit_queue_sha256_before_review": file_sha256(audit_path),
    }


def build_audit_queue(
    observations: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    queue = []
    for row in observations:
        if int(row["score"]) != 0:
            continue
        audit_id = hashlib.sha256(
            f"{row['mechanism_id']}:{row['problem_id']}:{row['run']}".encode()
        ).hexdigest()[:16]
        queue.append(
            {
                "audit_id": audit_id,
                "mechanism_id": row["mechanism_id"],
                "problem_id": row["problem_id"],
                "run": row["run"],
                "problem": row["problem"],
                "reference_solution": row["reference_solution"],
                "oracle_answer": row["oracle_answer"],
                "prediction": row["prediction"],
                "extracted": row["extracted"],
                "scorer_score": row["score"],
                "execution_error": row.get("error", ""),
                "audit_label": None,
                "audit_notes": "",
                "allowed_labels": sorted(ALLOWED_AUDIT_LABELS),
            }
        )
    return queue


async def _execute_one(
    *, item: Mapping[str, Any], run: int, index: int, model_config: Any,
    timeout: float, semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    async with semaphore:
        llm = create_llm_instance(model_config)
        try:
            solver = BaselineTwoStageSolver(llm)
            result = await asyncio.wait_for(
                solver(str(item["problem"])), timeout=timeout
            )
            prediction = str(result.get("response", ""))
            error = (
                "" if result.get("status") == "success"
                else str(result.get("observation", result.get("status", "error")))
            )
        except Exception as exc:
            prediction = ""
            error = f"{type(exc).__name__}: {exc}"
        scorer = MATHBenchmark("MATH", "", "")
        score, extracted = scorer.calculate_score(
            str(item["solution"]), prediction
        )
        usage = llm.get_usage_summary()
        metadata = item["mechanism_metadata"]
        return {
            "run": run,
            "index": index,
            "mechanism_id": metadata["family"],
            "difficulty_bin": metadata.get("difficulty_bin"),
            "case_id": metadata["case_id"],
            "problem_id": metadata["problem_id"],
            "problem": item["problem"],
            "reference_solution": item["solution"],
            "oracle_answer": metadata["oracle_answer"],
            "prediction": prediction,
            "extracted": extracted,
            "score": int(score),
            "error": error,
            "tokens": int(usage.get("total_tokens", 0) or 0),
            "calls": int(usage.get("call_count", 0) or 0),
            "cost": float(usage.get("total_cost", 0.0) or 0.0),
        }


async def screen(args: argparse.Namespace) -> Dict[str, Any]:
    manifest, data = load_bundle(args.manifest)
    protocol = manifest["screening_protocol"]
    if args.num_runs != int(protocol["num_runs"]):
        raise SystemExit(
            f"screening protocol requires {protocol['num_runs']} runs; received {args.num_runs}"
        )
    if protocol.get("timeout_seconds") is not None and float(args.timeout) != float(
        protocol["timeout_seconds"]
    ):
        raise SystemExit(
            f"screening protocol requires timeout={protocol['timeout_seconds']}; "
            f"received {args.timeout}"
        )
    if protocol.get("baseline_model") and args.model != protocol["baseline_model"]:
        raise SystemExit(
            f"screening protocol requires model={protocol['baseline_model']}; "
            f"received {args.model}"
        )
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"screening output already exists: {output}")
    output.mkdir(parents=True)
    semaphore = asyncio.Semaphore(args.concurrency)
    config = _model_config(args.model)
    observations = []
    for run in range(1, args.num_runs + 1):
        run_rows = await asyncio.gather(
            *(
                _execute_one(
                    item=item, run=run, index=index, model_config=config,
                    timeout=args.timeout, semaphore=semaphore,
                )
                for index, item in enumerate(data)
            )
        )
        observations.extend(run_rows)

    details_path = output / "observations.jsonl"
    audit_path = output / "manual_audit_queue.jsonl"
    summary_path = output / "raw_summary.json"
    _write_jsonl(details_path, observations)
    audit_queue = build_audit_queue(observations)
    _write_jsonl(audit_path, audit_queue)
    report = build_raw_report(
        manifest=manifest, manifest_path=args.manifest, model=args.model,
        data_count=len(data), observations=observations,
        details_path=details_path, audit_path=audit_path,
    )
    mechanisms = report["mechanisms"]
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"Raw screening summary: {summary_path}")
    print(f"Full baseline observations: {details_path}")
    print(f"Manual audit queue: {audit_path}")
    for item in mechanisms:
        print(
            f"{item['mechanism_id']}: raw_error={item['raw_error_rate']:.3f}, "
            f"stable_problems={item['raw_stable_failed_problems']}, "
            f"preliminary={item['preliminary_headroom_eligible']}"
        )
    print("No candidate operator was generated, loaded, routed, or executed.")
    return report


def recover_raw_summary(
    *, manifest_path: str | Path, output_dir: str | Path, model: str,
) -> Dict[str, Any]:
    manifest, data = load_bundle(manifest_path)
    output = Path(output_dir).resolve()
    details_path = output / "observations.jsonl"
    audit_path = output / "manual_audit_queue.jsonl"
    summary_path = output / "raw_summary.json"
    if summary_path.exists():
        raise FileExistsError(f"raw summary already exists: {summary_path}")
    if not details_path.is_file() or not audit_path.is_file():
        raise FileNotFoundError("recovery requires saved observations and audit queue")
    observations = _read_jsonl(details_path)
    expected_count = len(data) * int(manifest["screening_protocol"]["num_runs"])
    if len(observations) != expected_count:
        raise ValueError(
            f"saved observations are incomplete: expected {expected_count}, got {len(observations)}"
        )
    expected_keys = {
        (str(row["mechanism_metadata"]["problem_id"]), run)
        for row in data
        for run in range(1, int(manifest["screening_protocol"]["num_runs"]) + 1)
    }
    actual_keys = {
        (str(row["problem_id"]), int(row["run"])) for row in observations
    }
    if actual_keys != expected_keys:
        raise ValueError("saved observations do not cover every manifest problem and run")
    audit_queue = _read_jsonl(audit_path)
    expected_audit_keys = {
        (str(row["problem_id"]), int(row["run"]))
        for row in observations if int(row["score"]) == 0
    }
    actual_audit_keys = {
        (str(row["problem_id"]), int(row["run"])) for row in audit_queue
    }
    if actual_audit_keys != expected_audit_keys:
        raise ValueError("saved audit queue does not cover every zero-score observation")
    report = build_raw_report(
        manifest=manifest, manifest_path=manifest_path, model=model,
        data_count=len(data), observations=observations,
        details_path=details_path, audit_path=audit_path, recovered=True,
    )
    summary_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def finalize_audit(
    *, raw_summary_path: str | Path, audit_queue_path: str | Path,
    output_path: str | Path,
) -> Dict[str, Any]:
    raw_path = Path(raw_summary_path).resolve()
    queue_path = Path(audit_queue_path).resolve()
    destination = Path(output_path).resolve()
    if destination.exists():
        raise FileExistsError(f"audited summary already exists: {destination}")
    with raw_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    observations_path = Path(raw["observations"])
    if file_sha256(observations_path) != raw["observations_sha256"]:
        raise ValueError("baseline observations changed after screening")
    observations = _read_jsonl(observations_path)
    queue = _read_jsonl(queue_path)
    pending = [row["audit_id"] for row in queue if row.get("audit_label") is None]
    if pending:
        raise ValueError(
            f"manual audit incomplete: {len(pending)} rows still have null audit_label"
        )
    invalid_labels = sorted(
        {
            str(row.get("audit_label"))
            for row in queue
            if row.get("audit_label") not in ALLOWED_AUDIT_LABELS
        }
    )
    if invalid_labels:
        raise ValueError(f"unsupported manual audit labels: {invalid_labels}")
    audit_by_key = {
        (str(row["problem_id"]), int(row["run"])): row for row in queue
    }
    expected_failure_keys = {
        (str(row["problem_id"]), int(row["run"]))
        for row in observations if int(row["score"]) == 0
    }
    if set(audit_by_key) != expected_failure_keys:
        raise ValueError("manual audit queue does not cover every raw failure exactly once")
    invalid_problem_ids = {
        str(row["problem_id"])
        for row in queue if row["audit_label"] == "invalid_generated_problem"
    }
    protocol = raw["protocol"]
    count_execution_as_failure = bool(
        protocol.get("execution_errors_count_as_model_failures", True)
    )
    mechanisms = []
    accepted = []
    for mechanism in sorted({str(row["mechanism_id"]) for row in observations}):
        rows = [
            row for row in observations
            if row["mechanism_id"] == mechanism
            and str(row["problem_id"]) not in invalid_problem_ids
        ]
        eligible_rows = []
        true_failures = []
        false_negatives = []
        excluded_execution_observations = 0
        failed_runs: Dict[str, int] = defaultdict(int)
        for row in rows:
            if int(row["score"]) == 1:
                eligible_rows.append(row)
                continue
            audit = audit_by_key[(str(row["problem_id"]), int(row["run"]))]
            if audit["audit_label"] == "execution_error" and not count_execution_as_failure:
                excluded_execution_observations += 1
                continue
            eligible_rows.append(row)
            if audit["audit_label"] in {"true_model_error", "execution_error"}:
                true_failures.append(row)
                failed_runs[str(row["problem_id"])] += 1
            elif audit["audit_label"] == "scorer_false_negative":
                false_negatives.append(row)
        stable_ids = sorted(
            problem_id for problem_id, count in failed_runs.items()
            if count >= int(protocol["stable_failure_min_failed_runs"])
        )
        error_rate = len(true_failures) / len(eligible_rows) if eligible_rows else 0.0
        qualifies = (
            error_rate >= float(protocol["min_audited_error_rate"])
            and len(stable_ids) >= int(protocol["min_stable_failed_problems"])
        )
        item = {
            "mechanism_id": mechanism,
            "eligible_observations": len(eligible_rows),
            "excluded_execution_observations": excluded_execution_observations,
            "excluded_invalid_problems": len(
                {
                    problem_id for problem_id in invalid_problem_ids
                    if any(
                        r["mechanism_id"] == mechanism
                        and str(r["problem_id"]) == problem_id
                        for r in observations
                    )
                }
            ),
            "audited_true_failures": len(true_failures),
            "audited_scorer_false_negatives": len(false_negatives),
            "audited_error_rate": error_rate,
            "stable_failed_problems": len(stable_ids),
            "stable_failed_problem_ids": stable_ids,
            "headroom_eligible": qualifies,
            "status": "accepted_for_operator_design" if qualifies else "rejected_insufficient_headroom",
        }
        mechanisms.append(item)
        if qualifies:
            accepted.append(mechanism)
    report = {
        "schema_version": 1,
        "report_type": "audited_baseline_mechanism_headroom",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "raw_summary": str(raw_path),
        "raw_summary_sha256": file_sha256(raw_path),
        "observations": str(observations_path),
        "observations_sha256": file_sha256(observations_path),
        "manual_audit_queue": str(queue_path),
        "manual_audit_queue_sha256": file_sha256(queue_path),
        "scorer_version": raw["scorer_version"],
        "candidate_generation_invocations": 0,
        "candidate_execution_invocations": 0,
        "operator_library_accesses": 0,
        "routing_invocations": 0,
        "protocol": protocol,
        "accepted_mechanisms": accepted,
        "mechanisms": mechanisms,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Baseline-only mechanism headroom screening.")
    subparsers = parser.add_subparsers(dest="stage", required=True)
    screen_parser = subparsers.add_parser("screen")
    screen_parser.add_argument("--manifest", required=True)
    screen_parser.add_argument("--output-dir", required=True)
    screen_parser.add_argument("--model", default="deepseek-v4-flash")
    screen_parser.add_argument("--num-runs", type=int, default=3)
    screen_parser.add_argument("--concurrency", type=int, default=4)
    screen_parser.add_argument("--timeout", type=float, default=180.0)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--raw-summary", required=True)
    finalize_parser.add_argument("--audit-queue", required=True)
    finalize_parser.add_argument("--output", required=True)
    label_parser = subparsers.add_parser("apply-audit-labels")
    label_parser.add_argument("--audit-queue", required=True)
    label_parser.add_argument("--labels", required=True)
    recover_parser = subparsers.add_parser("recover-summary")
    recover_parser.add_argument("--manifest", required=True)
    recover_parser.add_argument("--output-dir", required=True)
    recover_parser.add_argument("--model", default="deepseek-v4-flash")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.stage == "screen":
        asyncio.run(screen(args))
        return
    if args.stage == "apply-audit-labels":
        counts = apply_audit_labels(
            audit_queue_path=args.audit_queue,
            labels_path=args.labels,
        )
        print(f"Updated audit queue: {Path(args.audit_queue).resolve()}")
        print(f"Applied labels: {counts}")
        return
    if args.stage == "recover-summary":
        report = recover_raw_summary(
            manifest_path=args.manifest, output_dir=args.output_dir, model=args.model
        )
        print(f"Recovered raw summary: {Path(args.output_dir).resolve() / 'raw_summary.json'}")
        print(f"Observations: {report['totals']['observations']}")
        print(f"Raw failures requiring audit: {report['totals']['raw_failures_requiring_audit']}")
        return
    report = finalize_audit(
        raw_summary_path=args.raw_summary,
        audit_queue_path=args.audit_queue,
        output_path=args.output,
    )
    print(f"Audited screening summary: {Path(args.output).resolve()}")
    print(f"Accepted mechanisms: {report['accepted_mechanisms']}")


if __name__ == "__main__":
    main()
