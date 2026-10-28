"""Fresh mixed-hidden routed confirmation for restricted-prime operator V2."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import random
from pathlib import Path
from typing import Any, Dict, Mapping

from benchmarks.math import MATHBenchmark
from scripts.baseline_failure_mechanisms_v2 import _unique_rows
from scripts.operator_library import OperatorLibrary, ProblemFeatureExtractor, stable_problem_id
from scripts.contextual_operator_router import ContextAwareOperatorRouter
from scripts.restricted_prime_tool import (
    RESTRICTED_DIGIT_PRIME_MECHANISM,
    RestrictedDigitPrimeEnumerationTool,
)
from scripts.router_end_to_end_experiment import (
    EndToEndRunner,
    _json_sha256,
    _read_json,
    _read_jsonl,
    _utc_now,
    _write_json,
    _write_jsonl,
    summarize,
)
from scripts.trajectory_failure_expander import file_sha256


OPERATOR_NAME = "ReusableRestrictedDigitPrimeEnumerator"
EXPERIMENT_ROLE = "blind_end_to_end_restricted_prime_candidate_router_v2"
GENERATOR_VERSION = "restricted_prime_mixed_hidden_v2"


def _occupied_ids(workspace_root: Path) -> set[str]:
    occupied: set[str] = set()
    for path in workspace_root.rglob("*.jsonl"):
        try:
            rows = _read_jsonl(path)
        except Exception:
            continue
        for row in rows:
            metadata = row.get("mechanism_metadata", {})
            problem_id = metadata.get("problem_id")
            if problem_id:
                occupied.add(str(problem_id))
            elif row.get("problem"):
                occupied.add(stable_problem_id(str(row["problem"])))
    return occupied


def _render_positive(row: Mapping[str, Any], variant: int) -> Dict[str, Any]:
    result = dict(row)
    metadata = dict(result["mechanism_metadata"])
    digits = [int(value) for value in metadata["parameters"]["digits"]]
    maximum = int(metadata["parameters"]["max_length"])
    digit_text = ", ".join(map(str, digits))
    zero_clause = " A leading zero is not allowed." if 0 in digits else ""
    surface = variant % 4
    if surface == 0:
        surface_name = "canonical_between_one_and_n"
        problem = (
            f"Choose between one and {maximum} distinct digits from the set "
            f"{{{digit_text}}} and arrange the chosen digits without repetition to "
            "form a positive integer."
            f"{zero_clause} How many distinct prime numbers can be formed?"
        )
    elif surface == 1:
        surface_name = "natural_pick_n_or_fewer"
        problem = (
            f"Pick {maximum} or fewer different digits from the set $\\{{{digit_text}\\}}$ "
            "and arrange them to form a number."
            f"{zero_clause} How many prime numbers can we create in this manner?"
        )
    elif surface == 2:
        surface_name = "up_to_n_distinct_digits"
        problem = (
            f"Using up to {maximum} digits from the set {{{digit_text}}}, "
            "arrange the digits without repetition to make positive integers."
            f"{zero_clause} What is the number of distinct prime integers obtainable?"
        )
    else:
        surface_name = "one_to_n_distinct_digits"
        problem = (
            f"From one to {maximum} digits may be selected from the set "
            f"{{{digit_text}}} and arranged without repetition as a positive integer."
            f"{zero_clause} How many distinct prime numbers result?"
        )
    metadata.update(
        {
            "surface_form": surface_name,
            "mixed_split": "specialist_positive",
            "expected_route": OPERATOR_NAME,
            "visible_to_operator_generator": False,
            "problem_id": stable_problem_id(problem),
        }
    )
    result["problem"] = problem
    result["mechanism_metadata"] = metadata
    return result


def _formed_values(digits: list[int], maximum: int, *, repetition: bool) -> set[int]:
    values: set[int] = set()
    for length in range(1, maximum + 1):
        iterator = (
            itertools.product(digits, repeat=length)
            if repetition else itertools.permutations(digits, length)
        )
        for arrangement in iterator:
            if arrangement[0] == 0:
                continue
            values.add(int("".join(map(str, arrangement))))
    return values


def _near_misses(*, seed: int, count: int) -> list[Dict[str, Any]]:
    rng = random.Random(seed)
    rows = []
    kinds = ("repetition_allowed", "composite_count", "integer_count", "even_count")
    for ordinal in range(count):
        digits = sorted(rng.sample(range(10), 5))
        maximum = 3
        digit_text = ", ".join(map(str, digits))
        zero_clause = " A leading zero is not allowed." if 0 in digits else ""
        kind = kinds[ordinal % len(kinds)]
        if kind == "repetition_allowed":
            values = _formed_values(digits, maximum, repetition=True)
            answer = sum(RestrictedDigitPrimeEnumerationTool._is_prime(v) for v in values)
            problem = (
                f"Choose up to {maximum} digits from the set {{{digit_text}}} and arrange "
                "them to form a positive integer; digit repetition is allowed."
                f"{zero_clause} How many distinct prime numbers can be formed?"
            )
        else:
            values = _formed_values(digits, maximum, repetition=False)
            if kind == "composite_count":
                answer = sum(v > 1 and not RestrictedDigitPrimeEnumerationTool._is_prime(v) for v in values)
                request = "distinct composite positive integers"
            elif kind == "integer_count":
                answer = len(values)
                request = "distinct positive integers"
            else:
                answer = sum(v % 2 == 0 for v in values)
                request = "distinct even positive integers"
            problem = (
                f"Choose between one and {maximum} distinct digits from the set "
                f"{{{digit_text}}} and arrange them without repetition."
                f"{zero_clause} How many {request} can be formed?"
            )
        problem_id = stable_problem_id(problem)
        rows.append(
            {
                "problem": problem,
                "solution": f"Exact finite enumeration gives $\\boxed{{{answer}}}$.",
                "level": "Level 5",
                "type": "Counting & Probability",
                "mechanism_metadata": {
                    "family": "restricted_prime_semantic_near_miss",
                    "generator_version": GENERATOR_VERSION,
                    "problem_id": problem_id,
                    "origin": "generated_exact_oracle",
                    "near_miss_kind": kind,
                    "oracle_answer": str(answer),
                    "mixed_split": "non_specialist_near_miss",
                    "expected_route": "generic_two_stage_fallback",
                    "visible_to_operator_generator": False,
                },
            }
        )
    return rows


def _math_negatives(
    *, path: Path, seed: int, count: int, occupied: set[str]
) -> list[Dict[str, Any]]:
    extractor = ProblemFeatureExtractor()
    candidates = []
    for row in _read_jsonl(path):
        problem = str(row["problem"])
        problem_id = stable_problem_id(problem)
        if problem_id in occupied:
            continue
        if RESTRICTED_DIGIT_PRIME_MECHANISM in extractor.extract(problem).features:
            continue
        candidates.append(dict(row))
    rng = random.Random(seed)
    rng.shuffle(candidates)
    selected = candidates[:count]
    if len(selected) != count:
        raise ValueError("not enough unrelated MATH negatives")
    output = []
    for row in selected:
        problem_id = stable_problem_id(str(row["problem"]))
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


def prepare(
    *, workspace_root: Path, batch_manifest: Path, output_dir: Path,
    math_test: Path, seed: int, positive_count: int, negative_count: int,
    near_miss_count: int, exec_model: str, num_runs: int, timeout: float,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"confirmation output already exists: {output_dir}")
    if min(positive_count, negative_count, near_miss_count) <= 0:
        raise ValueError("all mixed-set counts must be positive")
    batch = _read_json(batch_manifest)
    if batch.get("status") != "pending_tournament" or int(
        batch.get("operator_version", 0)
    ) != 2:
        raise ValueError("V2 batch is not frozen and pending confirmation")
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    record = library.get(OPERATOR_NAME)
    if (
        not record or record.get("status") != "candidate"
        or int(record.get("version", 0)) != 2
        or record.get("artifact", {}).get("artifact_sha256")
        != batch.get("candidate_artifact_sha256")
        or _json_sha256(record) != batch.get("operator_record_sha256")
    ):
        raise ValueError("V2 candidate changed after materialization")
    runtime_path = Path(__file__).with_name("restricted_prime_tool.py")
    if file_sha256(runtime_path) != batch.get("tool_runtime_sha256"):
        raise ValueError("V2 restricted-prime runtime changed")
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
        "formal_reruns_allowed": 1,
    }
    output_dir.mkdir(parents=True)
    preparation = output_dir / "preparation_protocol.json"
    _write_json(
        preparation,
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
            "batch_manifest": str(batch_manifest.resolve()),
            "batch_manifest_sha256": file_sha256(batch_manifest),
            "candidate_artifact_sha256": batch["candidate_artifact_sha256"],
            "operator_record_sha256": batch["operator_record_sha256"],
            "execution": execution,
            "success_gates": gates,
        },
    )
    occupied = _occupied_ids(workspace_root)
    positives = [
        _render_positive(row, index)
        for index, row in enumerate(
            _unique_rows(
                RESTRICTED_DIGIT_PRIME_MECHANISM,
                seed=seed + 100003,
                count=positive_count,
                split="mixed_hidden_positive",
                occupied=occupied,
                difficulty="hard",
            )
        )
    ]
    negatives = _math_negatives(
        path=math_test, seed=seed + 200003, count=negative_count, occupied=occupied
    )
    near_misses = _near_misses(seed=seed + 300007, count=near_miss_count)
    all_rows = [*positives, *negatives, *near_misses]
    ids = [row["mechanism_metadata"]["problem_id"] for row in all_rows]
    if len(ids) != len(set(ids)) or set(ids) & occupied:
        raise RuntimeError("mixed hidden set overlaps prior data or itself")
    # Fail before execution if the frozen detector cannot represent the new surfaces.
    extractor = ProblemFeatureExtractor()
    if any(
        RESTRICTED_DIGIT_PRIME_MECHANISM not in extractor.extract(row["problem"]).features
        for row in positives
    ) or any(
        RESTRICTED_DIGIT_PRIME_MECHANISM in extractor.extract(row["problem"]).features
        for row in [*negatives, *near_misses]
    ):
        raise RuntimeError("frozen V2 routing detector fails prepared surface audit")
    dataset = output_dir / "mixed_hidden.jsonl"
    _write_jsonl(dataset, all_rows)
    manifest = {
        "schema_version": 1,
        "experiment_role": EXPERIMENT_ROLE,
        "status": "prepared_unopened",
        "created_at": _utc_now(),
        "seed": seed,
        "operator": {
            "name": OPERATOR_NAME,
            "version": 2,
            "artifact_sha256": batch["candidate_artifact_sha256"],
            "record_sha256": batch["operator_record_sha256"],
            "production_routable_during_confirmation": False,
        },
        "batch": {
            "path": str(batch_manifest.resolve()),
            "sha256": file_sha256(batch_manifest),
        },
        "preparation_protocol": {
            "path": preparation.name,
            "sha256": file_sha256(preparation),
        },
        "hidden_dataset": {
            "path": dataset.name,
            "sha256": file_sha256(dataset),
            "count": len(all_rows),
            "positive_count": len(positives),
            "negative_math_count": len(negatives),
            "near_miss_count": len(near_misses),
        },
        "preregistered_execution": execution,
        "preregistered_success_gates": gates,
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


async def run_confirmation(
    *, workspace_root: Path, manifest_path: Path, concurrency: int
) -> Path:
    manifest = _read_json(manifest_path)
    if manifest.get("experiment_role") != EXPERIMENT_ROLE:
        raise ValueError("manifest is not restricted-prime V2 confirmation")
    if manifest.get("status") != "prepared_unopened":
        raise ValueError("formal confirmation is single-use")
    output_dir = manifest_path.parent
    summary_path = output_dir / "comparison_summary.json"
    details_path = output_dir / "comparison_details.jsonl"
    protocol_path = output_dir / "protocol.json"
    if any(path.exists() for path in (summary_path, details_path, protocol_path)):
        raise FileExistsError("confirmation output already exists")
    dataset_path = output_dir / manifest["hidden_dataset"]["path"]
    preparation = output_dir / manifest["preparation_protocol"]["path"]
    if file_sha256(dataset_path) != manifest["hidden_dataset"]["sha256"]:
        raise ValueError("hidden dataset changed")
    if file_sha256(preparation) != manifest["preparation_protocol"]["sha256"]:
        raise ValueError("preparation protocol changed")
    batch_path = Path(manifest["batch"]["path"])
    if file_sha256(batch_path) != manifest["batch"]["sha256"]:
        raise ValueError("candidate batch changed")
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    record = library.get(OPERATOR_NAME)
    if (
        not record or record.get("status") != "candidate"
        or record.get("artifact", {}).get("artifact_sha256")
        != manifest["operator"]["artifact_sha256"]
        or _json_sha256(record) != manifest["operator"]["record_sha256"]
    ):
        raise ValueError("candidate changed before confirmation")
    execution = manifest["preregistered_execution"]
    _write_json(
        protocol_path,
        {
            "schema_version": 1,
            "status": "preregistered_before_hidden_rows_load",
            "created_at": _utc_now(),
            "manifest_sha256": file_sha256(manifest_path),
            "hidden_dataset_sha256": manifest["hidden_dataset"]["sha256"],
            "operator": manifest["operator"],
            "execution": execution,
            "success_gates": manifest["preregistered_success_gates"],
            "runtime_sha256": {
                "confirmation": file_sha256(Path(__file__).resolve()),
                "prime_tool": file_sha256(Path(__file__).with_name("restricted_prime_tool.py")),
                "router": file_sha256(Path(__file__).with_name("contextual_operator_router.py")),
                "library": file_sha256(Path(__file__).with_name("operator_library.py")),
                "scorer": file_sha256(Path(__file__).resolve().parents[1] / "benchmarks/math.py"),
            },
        },
    )
    rows = _read_jsonl(dataset_path)
    runner = EndToEndRunner(
        workspace_root=workspace_root,
        model=str(execution["exec_model"]),
        timeout=float(execution["timeout_seconds"]),
        concurrency=concurrency,
        candidate_name=OPERATOR_NAME,
    )
    details = await runner.run(rows=rows, num_runs=int(execution["num_runs"]))
    _write_jsonl(details_path, details)
    metrics = summarize(
        details, manifest["preregistered_success_gates"], OPERATOR_NAME
    )
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


def _print(path: Path) -> None:
    summary = _read_json(path)
    metrics = summary["metrics"]
    overall = metrics["overall"]
    specialist = metrics["specialist_questions"]
    negative = metrics["non_specialist_questions"]
    routing = metrics["routing"]
    print(f"Restricted-prime V2 confirmation summary: {path.resolve()}")
    print(f"Passed: {summary['passed']}")
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
    print("Confirmation never promotes or installs the candidate.")


def promote(
    *, workspace_root: Path, summary_path: Path, math_validate: Path,
    output_path: Path,
) -> Path:
    if output_path.exists():
        raise FileExistsError(f"promotion audit already exists: {output_path}")
    summary = _read_json(summary_path)
    if (
        summary.get("experiment_role") != EXPERIMENT_ROLE
        or summary.get("passed") is not True
        or int(summary.get("seed", -1)) != 150017
    ):
        raise ValueError("V2 confirmation is not the frozen passing seed")
    protocol_path = Path(str(summary["protocol"])).resolve()
    details_path = Path(str(summary["details"])).resolve()
    if (
        file_sha256(protocol_path) != summary.get("protocol_sha256")
        or file_sha256(details_path) != summary.get("details_sha256")
    ):
        raise ValueError("confirmation protocol or details changed")
    details = _read_jsonl(details_path)
    positive = [row for row in details if row["split"] == "specialist_positive"]
    negative = [row for row in details if row["split"] != "specialist_positive"]
    if (
        len(positive) != 36
        or any(int(row["routed_score"]) != 1 for row in positive)
        or any(row.get("routed_route_selected_operator") != OPERATOR_NAME for row in positive)
        or any(row.get("routed_route_selected_operator") for row in negative)
    ):
        raise ValueError("saved routes or specialist answers fail promotion audit")
    metrics = summary["metrics"]
    if (
        not all(metrics.get("gate_checks", {}).values())
        or metrics["routing"]["positive_route_recall"] != 1.0
        or metrics["routing"]["negative_false_routes"] != 0
        or metrics["non_specialist_questions"]["routing_attributable_regressions"] != 0
    ):
        raise ValueError("confirmation gates are not promotion-safe")
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    record = library.get(OPERATOR_NAME)
    expected = summary["operator"]
    if (
        not record or record.get("status") != "candidate"
        or int(record.get("version", 0)) != 2
        or record.get("artifact", {}).get("artifact_sha256")
        != expected["artifact_sha256"]
        or _json_sha256(record) != expected["record_sha256"]
    ):
        raise ValueError("V2 candidate changed after confirmation")
    rule_name = "restricted_digit_prime_enumeration_v2"
    wins = sum(int(row["routed_score"]) > int(row["baseline_score"]) for row in positive)
    ties = sum(int(row["routed_score"]) == int(row["baseline_score"]) for row in positive)
    losses = len(positive) - wins - ties
    winning_ids = {
        str(row["problem_id"])
        for row in positive
        if int(row["routed_score"]) > int(row["baseline_score"])
    }
    evidence = library.record_evidence(
        OPERATOR_NAME,
        {
            "id": "restricted-prime-v2-hidden-seed150017",
            "source": str(details_path),
            "source_summary": str(summary_path.resolve()),
            "scope": rule_name,
            "dataset": "MATH",
            "baseline": "generic_two_stage",
            "protocol": "paired_correct_within_timeout_blind_router_v2",
            "routing_eligible": True,
            "wins": wins,
            "ties": ties,
            "losses": losses,
            "unique_problems": len({str(row["problem_id"]) for row in positive}),
            "unique_winning_problems": len(winning_ids),
            "comparison_summary_sha256": file_sha256(summary_path),
            "comparison_details_sha256": file_sha256(details_path),
            "primary_metric": "correct_within_timeout",
        },
    )
    library.set_status(
        OPERATOR_NAME,
        "experimental",
        "Passed fresh mixed-hidden V2 routed correct-within-timeout confirmation.",
    )
    template_dir = workspace_root / "workflows" / "template"
    library.install_artifact(OPERATOR_NAME, template_dir)

    rows119 = _read_jsonl(math_validate)
    original = rows119[24]
    decision = library.retrieve(str(original["problem"]))
    if not decision.selected or decision.selected.operator_name != OPERATOR_NAME:
        raise RuntimeError("promoted V2 does not route original MATH index 24")
    router = ContextAwareOperatorRouter(
        llm=None,
        library_path=str(workspace_root / "operator_library" / "operators.json"),
        operator_module="workspace.MATH.workflows.template.operator",
        fallback=lambda problem: None,
    )
    installed = router._resolve(OPERATOR_NAME)
    result = asyncio.run(installed(str(original["problem"])))
    score, extracted = MATHBenchmark("MATH", "", "").calculate_score(
        str(original["solution"]), str(result.get("response", ""))
    )
    if result.get("status") != "success" or int(score) != 1:
        raise RuntimeError("installed V2 fails original MATH index 24")

    raw_false_negatives = [
        row for row in negative
        if int(row["baseline_score"]) == 0
        and int(row["routed_score"]) == 0
        and row.get("baseline_extracted") == r"\frac{1}{9}"
        and row.get("routed_extracted") == r"\frac{1}{9}"
    ]
    audit = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "operator_name": OPERATOR_NAME,
        "operator_version": 2,
        "status": "experimental",
        "installed": True,
        "production_routable": True,
        "artifact_sha256": expected["artifact_sha256"],
        "confirmation_seed": 150017,
        "confirmation_summary": str(summary_path.resolve()),
        "confirmation_summary_sha256": file_sha256(summary_path),
        "confirmation_details_sha256": file_sha256(details_path),
        "raw_metrics": metrics,
        "offline_audit": {
            "scorer_false_negative_observations": len(raw_false_negatives),
            "scorer_false_negative_unique_problems": len(
                {str(row["problem_id"]) for row in raw_false_negatives}
            ),
            "audited_overall_correct": {
                "baseline": metrics["overall"]["baseline"]["correct"] + len(raw_false_negatives),
                "routed_v2": metrics["overall"]["routed_aflow"]["correct"] + len(raw_false_negatives),
                "total": metrics["overall"]["baseline"]["total"],
            },
            "paired_result_changed": False,
        },
        "routing_evidence_id": evidence["id"],
        "original_119_verification": {
            "index": 24,
            "selected_operator": decision.selected.operator_name,
            "score": int(score),
            "extracted": extracted,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output_path, audit)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--workspace-root", default="workspace/MATH")
    prepare_parser.add_argument("--batch-manifest", required=True)
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--math-test", default="data/datasets/math_test.jsonl")
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
    promote_parser = sub.add_parser("promote")
    promote_parser.add_argument("--workspace-root", default="workspace/MATH")
    promote_parser.add_argument("--summary", required=True)
    promote_parser.add_argument("--math-validate", default="data/datasets/math_validate.jsonl")
    promote_parser.add_argument("--output", required=True)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    resolve = lambda value: Path(value) if Path(value).is_absolute() else repo_root / value
    if args.stage == "prepare":
        path = prepare(
            workspace_root=resolve(args.workspace_root),
            batch_manifest=resolve(args.batch_manifest),
            output_dir=resolve(args.output_dir),
            math_test=resolve(args.math_test),
            seed=args.seed,
            positive_count=args.positive_count,
            negative_count=args.negative_count,
            near_miss_count=args.near_miss_count,
            exec_model=args.exec_model,
            num_runs=args.num_runs,
            timeout=args.timeout,
        )
        print(f"Restricted-prime V2 mixed hidden manifest: {path.resolve()}")
        print("Candidate and routing rule are frozen; candidate remains uninstalled.")
    elif args.stage == "run":
        path = asyncio.run(
            run_confirmation(
                workspace_root=resolve(args.workspace_root),
                manifest_path=resolve(args.manifest),
                concurrency=args.concurrency,
            )
        )
        _print(path)
    else:
        path = promote(
            workspace_root=resolve(args.workspace_root),
            summary_path=resolve(args.summary),
            math_validate=resolve(args.math_validate),
            output_path=resolve(args.output),
        )
        print(f"Restricted-prime V2 promotion audit: {path.resolve()}")
        print(f"Promoted and installed: {OPERATOR_NAME} version 2")


if __name__ == "__main__":
    main()
