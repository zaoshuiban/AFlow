"""Fresh mixed-hidden joint confirmation for two installed specialists."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from benchmarks.math import MATHBenchmark
from scripts.baseline_failure_mechanisms_v2 import _unique_rows
from scripts.operator_library import OperatorLibrary, ProblemFeatureExtractor, stable_problem_id
from scripts.parameterized_tools import PARENTHESIZATION_MECHANISM, ParameterizedToolRegistry
from scripts.parenthesization_v2_upgrade import _render_surface
from scripts.restricted_prime_tool import (
    RESTRICTED_DIGIT_PRIME_MECHANISM,
    RestrictedDigitPrimeEnumerationTool,
)
from scripts.restricted_prime_v2_confirmation import (
    _near_misses as _prime_near_misses,
    _render_positive as _render_prime_positive,
)
from scripts.router_end_to_end_experiment import (
    EndToEndRunner,
    _json_sha256,
    _method_stats,
    _near_miss_rows as _parenthesization_near_misses,
    _read_json,
    _read_jsonl,
    _utc_now,
    _write_json,
    _write_jsonl,
)
from scripts.trajectory_failure_expander import file_sha256


PARENTHESIZATION_OPERATOR = "ReusableParenthesizationIntervalDPTool"
PRIME_OPERATOR = "ReusableRestrictedDigitPrimeEnumerator"
EXPERIMENT_ROLE = "blind_joint_two_specialist_router_confirmation_v1"
GENERATOR_VERSION = "joint_two_specialist_mixed_hidden_v1"


def _occupied_ids(workspace_root: Path) -> set[str]:
    occupied: set[str] = set()
    for path in workspace_root.rglob("*.jsonl"):
        try:
            rows = _read_jsonl(path)
        except Exception:
            continue
        for row in rows:
            metadata = row.get("mechanism_metadata", {})
            if metadata.get("problem_id"):
                occupied.add(str(metadata["problem_id"]))
            elif row.get("problem"):
                occupied.add(stable_problem_id(str(row["problem"])))
    return occupied


def _decorate_parenthesization(row: Mapping[str, Any], variant: int) -> Dict[str, Any]:
    result = _render_surface(row, variant, "joint_hidden_positive")
    metadata = dict(result["mechanism_metadata"])
    metadata.update(
        {
            "mixed_split": "specialist_positive",
            "specialist_family": PARENTHESIZATION_MECHANISM,
            "expected_route": PARENTHESIZATION_OPERATOR,
            "visible_to_operator_generator": False,
        }
    )
    result["mechanism_metadata"] = metadata
    return result


def _decorate_prime(row: Mapping[str, Any], variant: int) -> Dict[str, Any]:
    result = _render_prime_positive(row, variant)
    metadata = dict(result["mechanism_metadata"])
    metadata.update(
        {
            "mixed_split": "specialist_positive",
            "specialist_family": RESTRICTED_DIGIT_PRIME_MECHANISM,
            "expected_route": PRIME_OPERATOR,
            "visible_to_operator_generator": False,
        }
    )
    result["mechanism_metadata"] = metadata
    return result


def _decorate_near_miss(
    row: Mapping[str, Any], *, family: str,
) -> Dict[str, Any]:
    result = dict(row)
    metadata = dict(result["mechanism_metadata"])
    metadata.update(
        {
            "mixed_split": "non_specialist_near_miss",
            "near_miss_for_family": family,
            "expected_route": "generic_two_stage_fallback",
            "visible_to_operator_generator": False,
        }
    )
    result["mechanism_metadata"] = metadata
    return result


def _math_negatives(
    *, path: Path, seed: int, count: int, occupied: set[str]
) -> list[Dict[str, Any]]:
    extractor = ProblemFeatureExtractor()
    candidates = []
    for source in _read_jsonl(path):
        problem = str(source["problem"])
        problem_id = stable_problem_id(problem)
        if problem_id in occupied:
            continue
        features = extractor.extract(problem).features
        if (
            PARENTHESIZATION_MECHANISM in features
            or RESTRICTED_DIGIT_PRIME_MECHANISM in features
        ):
            continue
        candidates.append(dict(source))
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


def _verify_installed_records(
    workspace_root: Path,
) -> tuple[OperatorLibrary, Dict[str, Dict[str, Any]]]:
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    records: Dict[str, Dict[str, Any]] = {}
    representatives = {
        PARENTHESIZATION_OPERATOR: (
            "Consider the expression 2 + 3 * 4 - 5. Fully parenthesize it in every "
            "possible way. How many distinct values are possible?"
        ),
        PRIME_OPERATOR: (
            "Pick two or fewer different digits from the set $\\{1,3,6,7\\}$ and "
            "arrange them to form a number. How many prime numbers can we create?"
        ),
    }
    for name, problem in representatives.items():
        record = library.get(name)
        if not record or record.get("status") not in {"experimental", "admitted", "active"}:
            raise ValueError(f"installed specialist is not active: {name}")
        dependency = record.get("artifact", {}).get("runtime_dependency")
        dependency_hash = record.get("artifact", {}).get("runtime_dependency_sha256")
        runtime = (Path(__file__).resolve().parents[1] / str(dependency)).resolve()
        if not runtime.is_file() or file_sha256(runtime) != dependency_hash:
            raise ValueError(f"runtime dependency changed for {name}")
        decision = library.retrieve(problem)
        if not decision.selected or decision.selected.operator_name != name:
            raise ValueError(f"production router cannot retrieve {name}")
        records[name] = record
    return library, records


def prepare(
    *, workspace_root: Path, output_dir: Path, math_test: Path, seed: int,
    parenthesization_count: int, prime_count: int, negative_count: int,
    parenthesization_near_miss_count: int, prime_near_miss_count: int,
    exec_model: str, num_runs: int, timeout: float,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"joint confirmation already exists: {output_dir}")
    counts = (
        parenthesization_count, prime_count, negative_count,
        parenthesization_near_miss_count, prime_near_miss_count,
    )
    if any(value <= 0 for value in counts) or num_runs < 2:
        raise ValueError("all counts must be positive and formal runs must be at least two")
    _, records = _verify_installed_records(workspace_root)
    execution = {
        "primary_metric": "correct_within_timeout",
        "methods": ["generic_two_stage_aflow", "production_two_specialist_routed_aflow"],
        "exec_model": exec_model,
        "num_runs": num_runs,
        "timeout_seconds": timeout,
        "formal_reruns_allowed": 1,
    }
    gates = {
        "overall_accuracy_strictly_above_baseline": True,
        "each_specialist_accuracy_at_least": 0.95,
        "each_specialist_route_recall": 1.0,
        "negative_false_route_rate": 0.0,
        "cross_specialist_routes": 0,
        "negative_accuracy_delta_at_least": -0.05,
        "routing_attributable_negative_regressions": 0,
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
                "parenthesization_positive": parenthesization_count,
                "restricted_prime_positive": prime_count,
                "negative_math": negative_count,
                "parenthesization_near_miss": parenthesization_near_miss_count,
                "restricted_prime_near_miss": prime_near_miss_count,
            },
            "operators": {
                name: {
                    "version": record["version"],
                    "artifact_sha256": record["artifact"]["artifact_sha256"],
                    "record_sha256": _json_sha256(record),
                    "status": record["status"],
                }
                for name, record in records.items()
            },
            "source_hashes": {"math_test": file_sha256(math_test)},
            "execution": execution,
            "success_gates": gates,
        },
    )
    occupied = _occupied_ids(workspace_root)
    parenthesization_rows = [
        _decorate_parenthesization(row, index)
        for index, row in enumerate(
            _unique_rows(
                PARENTHESIZATION_MECHANISM,
                seed=seed + 100003,
                count=parenthesization_count,
                split="joint_hidden_positive",
                occupied=occupied,
                difficulty="hard",
            )
        )
    ]
    prime_rows = [
        _decorate_prime(row, index)
        for index, row in enumerate(
            _unique_rows(
                RESTRICTED_DIGIT_PRIME_MECHANISM,
                seed=seed + 200003,
                count=prime_count,
                split="joint_hidden_positive",
                occupied=occupied,
                difficulty="hard",
            )
        )
    ]
    negatives = _math_negatives(
        path=math_test, seed=seed + 300007, count=negative_count, occupied=occupied
    )
    parenthesis_near = [
        _decorate_near_miss(row, family=PARENTHESIZATION_MECHANISM)
        for row in _parenthesization_near_misses(
            seed=seed + 400009, count=parenthesization_near_miss_count
        )
    ]
    prime_near = [
        _decorate_near_miss(row, family=RESTRICTED_DIGIT_PRIME_MECHANISM)
        for row in _prime_near_misses(
            seed=seed + 500009, count=prime_near_miss_count
        )
    ]
    rows = [*parenthesization_rows, *prime_rows, *negatives, *parenthesis_near, *prime_near]
    rng = random.Random(seed + 600011)
    rng.shuffle(rows)
    ids = [str(row["mechanism_metadata"]["problem_id"]) for row in rows]
    if len(ids) != len(set(ids)) or set(ids) & occupied:
        raise RuntimeError("joint hidden set overlaps prior data or itself")

    extractor = ProblemFeatureExtractor()
    parenthesis_registry = ParameterizedToolRegistry([PARENTHESIZATION_MECHANISM])
    scorer = MATHBenchmark("MATH", "", "")
    for row in rows:
        metadata = row["mechanism_metadata"]
        expected = metadata.get("expected_route")
        features = extractor.extract(str(row["problem"])).features
        if expected == PARENTHESIZATION_OPERATOR:
            if PARENTHESIZATION_MECHANISM not in features or RESTRICTED_DIGIT_PRIME_MECHANISM in features:
                raise RuntimeError("parenthesization positive has ambiguous frozen features")
            result = parenthesis_registry.solve(str(row["problem"]))
            score, _ = scorer.calculate_score(str(row["solution"]), result.response)
            if result.status != "success" or int(score) != 1:
                raise RuntimeError("parenthesization positive fails frozen tool oracle")
        elif expected == PRIME_OPERATOR:
            if RESTRICTED_DIGIT_PRIME_MECHANISM not in features or PARENTHESIZATION_MECHANISM in features:
                raise RuntimeError("prime positive has ambiguous frozen features")
            result = RestrictedDigitPrimeEnumerationTool.solve(str(row["problem"]))
            score, _ = scorer.calculate_score(str(row["solution"]), result.response)
            if result.status != "success" or int(score) != 1:
                raise RuntimeError("prime positive fails frozen tool oracle")
        elif PARENTHESIZATION_MECHANISM in features or RESTRICTED_DIGIT_PRIME_MECHANISM in features:
            raise RuntimeError("negative or near miss triggers a frozen specialist")

    dataset = output_dir / "mixed_hidden.jsonl"
    _write_jsonl(dataset, rows)
    manifest = {
        "schema_version": 1,
        "experiment_role": EXPERIMENT_ROLE,
        "status": "prepared_unopened",
        "created_at": _utc_now(),
        "seed": seed,
        "operators": {
            name: {
                "version": record["version"],
                "artifact_sha256": record["artifact"]["artifact_sha256"],
                "record_sha256": _json_sha256(record),
            }
            for name, record in records.items()
        },
        "preparation_protocol": {
            "path": preparation.name,
            "sha256": file_sha256(preparation),
        },
        "hidden_dataset": {
            "path": dataset.name,
            "sha256": file_sha256(dataset),
            "count": len(rows),
            "parenthesization_positive_count": len(parenthesization_rows),
            "restricted_prime_positive_count": len(prime_rows),
            "negative_math_count": len(negatives),
            "parenthesization_near_miss_count": len(parenthesis_near),
            "restricted_prime_near_miss_count": len(prime_near),
            "overlap_with_prior_rows": 0,
        },
        "preregistered_execution": execution,
        "preregistered_success_gates": gates,
        "freeze_policy": {
            "do_not_tune_or_rerun": True,
            "failed_formal_execution_requires_new_seed": True,
        },
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def _joint_metrics(
    details: Sequence[Mapping[str, Any]], gates: Mapping[str, Any]
) -> Dict[str, Any]:
    positives = [row for row in details if row["split"] == "specialist_positive"]
    negatives = [row for row in details if row["split"] != "specialist_positive"]
    by_family = {
        PARENTHESIZATION_MECHANISM: [
            row for row in positives
            if row["metadata"].get("specialist_family") == PARENTHESIZATION_MECHANISM
        ],
        RESTRICTED_DIGIT_PRIME_MECHANISM: [
            row for row in positives
            if row["metadata"].get("specialist_family") == RESTRICTED_DIGIT_PRIME_MECHANISM
        ],
    }
    expected_by_family = {
        PARENTHESIZATION_MECHANISM: PARENTHESIZATION_OPERATOR,
        RESTRICTED_DIGIT_PRIME_MECHANISM: PRIME_OPERATOR,
    }
    overall_baseline = _method_stats(details, "baseline")
    overall_routed = _method_stats(details, "routed")
    negative_baseline = _method_stats(negatives, "baseline")
    negative_routed = _method_stats(negatives, "routed")
    family_metrics: Dict[str, Any] = {}
    cross_routes = []
    for family, rows in by_family.items():
        expected = expected_by_family[family]
        missed = [
            row for row in rows
            if row.get("routed_route_selected_operator") != expected
            or row.get("routed_used_fallback") is True
        ]
        crossed = [
            row for row in rows
            if row.get("routed_route_selected_operator") not in {None, expected}
        ]
        cross_routes.extend(crossed)
        routed_stats = _method_stats(rows, "routed")
        family_metrics[family] = {
            "expected_operator": expected,
            "baseline": _method_stats(rows, "baseline"),
            "routed_aflow": routed_stats,
            "route_recall": 1.0 - len(missed) / len(rows) if rows else 0.0,
            "missed_routes": len(missed),
            "cross_routes": len(crossed),
        }
    false_routes = [row for row in negatives if row.get("routed_route_selected_operator")]
    regressions = [
        row for row in negatives
        if int(row["baseline_score"]) == 1 and int(row["routed_score"]) == 0
    ]
    attributable = [row for row in regressions if row.get("routed_route_selected_operator")]
    negative_delta = negative_routed["accuracy"] - negative_baseline["accuracy"]
    false_route_rate = len(false_routes) / len(negatives) if negatives else 0.0
    checks = {
        "overall_accuracy_strictly_above_baseline": overall_routed["accuracy"] > overall_baseline["accuracy"],
        "each_specialist_accuracy_at_least": all(
            value["routed_aflow"]["accuracy"] >= float(gates["each_specialist_accuracy_at_least"])
            for value in family_metrics.values()
        ),
        "each_specialist_route_recall": all(
            value["route_recall"] >= float(gates["each_specialist_route_recall"])
            for value in family_metrics.values()
        ),
        "negative_false_route_rate": false_route_rate <= float(gates["negative_false_route_rate"]),
        "cross_specialist_routes": len(cross_routes) <= int(gates["cross_specialist_routes"]),
        "negative_accuracy_delta_at_least": negative_delta >= float(gates["negative_accuracy_delta_at_least"]),
        "routing_attributable_negative_regressions": len(attributable)
        <= int(gates["routing_attributable_negative_regressions"]),
    }
    wins = sum(int(row["routed_score"]) > int(row["baseline_score"]) for row in details)
    ties = sum(int(row["routed_score"]) == int(row["baseline_score"]) for row in details)
    losses = len(details) - wins - ties
    return {
        "passed": all(checks.values()),
        "gate_checks": checks,
        "primary_metric": "correct_within_timeout",
        "overall": {"baseline": overall_baseline, "routed_aflow": overall_routed},
        "specialists": family_metrics,
        "non_specialist": {
            "baseline": negative_baseline,
            "routed_aflow": negative_routed,
            "accuracy_delta": negative_delta,
            "regression_observations": len(regressions),
            "routing_attributable_regressions": len(attributable),
        },
        "routing": {
            "negative_false_routes": len(false_routes),
            "negative_false_route_rate": false_route_rate,
            "cross_specialist_routes": len(cross_routes),
        },
        "paired": {"wins": wins, "ties": ties, "losses": losses, "net_wins": wins - losses},
    }


async def run_confirmation(
    *, workspace_root: Path, manifest_path: Path, concurrency: int
) -> Path:
    manifest = _read_json(manifest_path)
    if manifest.get("experiment_role") != EXPERIMENT_ROLE:
        raise ValueError("manifest is not the joint two-specialist confirmation")
    if manifest.get("status") != "prepared_unopened":
        raise ValueError("joint formal confirmation is single-use")
    output_dir = manifest_path.parent
    protocol_path = output_dir / "protocol.json"
    details_path = output_dir / "comparison_details.jsonl"
    summary_path = output_dir / "comparison_summary.json"
    if any(path.exists() for path in (protocol_path, details_path, summary_path)):
        raise FileExistsError("joint confirmation output already exists")
    dataset_path = output_dir / manifest["hidden_dataset"]["path"]
    preparation = output_dir / manifest["preparation_protocol"]["path"]
    if file_sha256(dataset_path) != manifest["hidden_dataset"]["sha256"]:
        raise ValueError("joint hidden dataset changed")
    if file_sha256(preparation) != manifest["preparation_protocol"]["sha256"]:
        raise ValueError("joint preparation protocol changed")
    _, records = _verify_installed_records(workspace_root)
    for name, expected in manifest["operators"].items():
        record = records[name]
        if (
            record["artifact"]["artifact_sha256"] != expected["artifact_sha256"]
            or _json_sha256(record) != expected["record_sha256"]
        ):
            raise ValueError(f"installed record changed before joint run: {name}")
    execution = manifest["preregistered_execution"]
    _write_json(
        protocol_path,
        {
            "schema_version": 1,
            "status": "preregistered_before_hidden_rows_load",
            "created_at": _utc_now(),
            "manifest_sha256": file_sha256(manifest_path),
            "hidden_dataset_sha256": manifest["hidden_dataset"]["sha256"],
            "operators": manifest["operators"],
            "execution": execution,
            "success_gates": manifest["preregistered_success_gates"],
            "runtime_sha256": {
                "joint_confirmation": file_sha256(Path(__file__).resolve()),
                "router": file_sha256(Path(__file__).with_name("contextual_operator_router.py")),
                "library": file_sha256(Path(__file__).with_name("operator_library.py")),
                "parenthesization_tool": file_sha256(Path(__file__).with_name("parameterized_tools.py")),
                "restricted_prime_tool": file_sha256(Path(__file__).with_name("restricted_prime_tool.py")),
                "scorer": file_sha256(Path(__file__).resolve().parents[1] / "benchmarks/math.py"),
                "installed_operator_module": file_sha256(workspace_root / "workflows/template/operator.py"),
            },
        },
    )
    rows = _read_jsonl(dataset_path)
    runner = EndToEndRunner(
        workspace_root=workspace_root,
        model=str(execution["exec_model"]),
        timeout=float(execution["timeout_seconds"]),
        concurrency=concurrency,
    )
    details = await runner.run(rows=rows, num_runs=int(execution["num_runs"]))
    _write_jsonl(details_path, details)
    metrics = _joint_metrics(details, manifest["preregistered_success_gates"])
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
        "operators": manifest["operators"],
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
    print(f"Joint two-specialist summary: {path.resolve()}")
    print(f"Passed: {summary['passed']}")
    print(
        f"Overall: baseline={overall['baseline']['correct']}/{overall['baseline']['total']}, "
        f"joint-routed={overall['routed_aflow']['correct']}/{overall['routed_aflow']['total']}"
    )
    for family, value in metrics["specialists"].items():
        print(
            f"{family}: baseline={value['baseline']['correct']}/{value['baseline']['total']}, "
            f"routed={value['routed_aflow']['correct']}/{value['routed_aflow']['total']}, "
            f"route_recall={value['route_recall']:.3f}, cross_routes={value['cross_routes']}"
        )
    negative = metrics["non_specialist"]
    print(
        f"Non-specialist regressions={negative['regression_observations']}; "
        f"routing-attributable={negative['routing_attributable_regressions']}"
    )
    routing = metrics["routing"]
    print(
        f"False routes={routing['negative_false_routes']}; "
        f"cross-specialist routes={routing['cross_specialist_routes']}"
    )
    print(
        f"Timeouts: baseline={overall['baseline']['timeouts']}, "
        f"joint-routed={overall['routed_aflow']['timeouts']}"
    )
    paired = metrics["paired"]
    print(f"Paired W/T/L={paired['wins']}/{paired['ties']}/{paired['losses']}")
    print("Joint confirmation never changes installation or routing state.")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--workspace-root", default="workspace/MATH")
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--math-test", default="data/datasets/math_test.jsonl")
    prepare_parser.add_argument("--seed", type=int, required=True)
    prepare_parser.add_argument("--parenthesization-count", type=int, default=8)
    prepare_parser.add_argument("--prime-count", type=int, default=8)
    prepare_parser.add_argument("--negative-count", type=int, default=24)
    prepare_parser.add_argument("--parenthesization-near-miss-count", type=int, default=4)
    prepare_parser.add_argument("--prime-near-miss-count", type=int, default=4)
    prepare_parser.add_argument("--exec-model", default="deepseek-v4-flash")
    prepare_parser.add_argument("--num-runs", type=int, default=3)
    prepare_parser.add_argument("--timeout", type=float, default=180.0)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--workspace-root", default="workspace/MATH")
    run_parser.add_argument("--manifest", required=True)
    run_parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    resolve = lambda value: Path(value) if Path(value).is_absolute() else repo_root / value
    if args.stage == "prepare":
        path = prepare(
            workspace_root=resolve(args.workspace_root),
            output_dir=resolve(args.output_dir),
            math_test=resolve(args.math_test),
            seed=args.seed,
            parenthesization_count=args.parenthesization_count,
            prime_count=args.prime_count,
            negative_count=args.negative_count,
            parenthesization_near_miss_count=args.parenthesization_near_miss_count,
            prime_near_miss_count=args.prime_near_miss_count,
            exec_model=args.exec_model,
            num_runs=args.num_runs,
            timeout=args.timeout,
        )
        print(f"Joint two-specialist hidden manifest: {path.resolve()}")
        print("Both installed operators and all success gates are frozen.")
    else:
        path = asyncio.run(
            run_confirmation(
                workspace_root=resolve(args.workspace_root),
                manifest_path=resolve(args.manifest),
                concurrency=args.concurrency,
            )
        )
        _print_summary(path)


if __name__ == "__main__":
    main()
