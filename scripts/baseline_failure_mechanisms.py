# -*- coding: utf-8 -*-
"""Deterministic exact-oracle screening families for baseline headroom.

These families are derived from persistent MATH failures other than index 91.
They are screening data only: no candidate generation, routing, or tournament
is allowed to consume them during the headroom measurement stage.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
GENERATOR_VERSION = "baseline_headroom_mechanisms_v1"
SUPPORTED_GENERATOR_VERSIONS = {
    GENERATOR_VERSION,
    "baseline_headroom_mechanisms_v2",
    "baseline_headroom_mechanisms_v2_1",
    "baseline_headroom_mechanisms_v2_2",
}
MECHANISMS = (
    "irregular_grid_rectangles",
    "top_k_replacement_mean",
    "conditional_reroll_multiplicity",
    "distinct_label_symmetry_quotient",
    "circular_mutual_nonadjacency",
)
SOURCE_FAILURE_INDEX = {
    "irregular_grid_rectangles": 15,
    "top_k_replacement_mean": 28,
    "conditional_reroll_multiplicity": 95,
    "distinct_label_symmetry_quotient": 102,
    "circular_mutual_nonadjacency": 107,
}


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_problem_id(problem: str) -> str:
    normalized = " ".join(str(problem).lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _json_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rng(seed: int, mechanism: str, ordinal: int) -> random.Random:
    digest = hashlib.sha256(
        f"{GENERATOR_VERSION}:{seed}:{mechanism}:{ordinal}".encode()
    ).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _boxed_solution(explanation: str, answer: str | int) -> str:
    return f"{explanation} Therefore the answer is $\\boxed{{{answer}}}$."


def _row(
    *, mechanism: str, ordinal: int, problem: str, solution: str,
    oracle: str | int, parameters: Mapping[str, Any], audit_facts: Mapping[str, Any],
) -> Dict[str, Any]:
    case_id = _json_hash(
        {"mechanism": mechanism, "ordinal": ordinal, "parameters": parameters}
    )[:16]
    return {
        "problem": problem,
        "solution": solution,
        "level": "Level 5",
        "type": (
            "Prealgebra"
            if mechanism in {"irregular_grid_rectangles", "top_k_replacement_mean"}
            else "Counting & Probability"
        ),
        "mechanism_metadata": {
            "family": mechanism,
            "generator_version": GENERATOR_VERSION,
            "case_id": case_id,
            "problem_id": stable_problem_id(problem),
            "origin": "generated_exact_oracle",
            "source_failure_index": SOURCE_FAILURE_INDEX[mechanism],
            "ordinal": ordinal,
            "parameters": dict(parameters),
            "oracle_answer": str(oracle),
            "audit_facts": dict(audit_facts),
        },
    }


def _block_edges(
    x0: int, y0: int, width: int, height: int
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    horizontal = {
        (x, y)
        for y in range(y0, y0 + height + 1)
        for x in range(x0, x0 + width)
    }
    vertical = {
        (x, y)
        for x in range(x0, x0 + width + 1)
        for y in range(y0, y0 + height)
    }
    return horizontal, vertical


def _count_rectangles(
    horizontal: set[tuple[int, int]], vertical: set[tuple[int, int]]
) -> int:
    xs = sorted(
        {x for x, _ in horizontal}
        | {x + 1 for x, _ in horizontal}
        | {x for x, _ in vertical}
    )
    ys = sorted(
        {y for _, y in vertical}
        | {y + 1 for _, y in vertical}
        | {y for _, y in horizontal}
    )
    total = 0
    for x1, x2 in itertools.combinations(xs, 2):
        for y1, y2 in itertools.combinations(ys, 2):
            top_bottom = all(
                (x, y1) in horizontal and (x, y2) in horizontal
                for x in range(x1, x2)
            )
            sides = all(
                (x1, y) in vertical and (x2, y) in vertical
                for y in range(y1, y2)
            )
            total += bool(top_bottom and sides)
    return total


def _rectangle_case(seed: int, ordinal: int) -> Dict[str, Any]:
    rng = _rng(seed, "irregular_grid_rectangles", ordinal)
    width_a, height_a = rng.randint(2, 4), rng.randint(2, 4)
    width_b, height_b = rng.randint(2, 4), rng.randint(2, 4)
    shift_x = rng.randint(1, min(width_a - 1, 2))
    shift_y = -rng.randint(1, min(height_b - 1, 2))
    h1, v1 = _block_edges(0, 0, width_a, height_a)
    h2, v2 = _block_edges(shift_x, shift_y, width_b, height_b)
    horizontal, vertical = h1 | h2, v1 | v2
    answer = _count_rectangles(horizontal, vertical)
    commands = [
        rf"draw(({x},{y})--({x + 1},{y}));" for x, y in sorted(horizontal)
    ] + [
        rf"draw(({x},{y})--({x},{y + 1}));" for x, y in sorted(vertical)
    ]
    diagram = "\n".join(commands)
    problem = (
        "How many axis-aligned rectangles are present in the figure below? "
        "A rectangle is counted whenever all four of its sides are completely drawn.\n\n"
        f"[asy]\nunitsize(0.24inch);\n{diagram}\n[/asy]"
    )
    explanation = (
        "Enumerate every pair of vertical coordinates and every pair of horizontal "
        "coordinates, retaining a rectangle exactly when every unit segment on all "
        f"four sides is present. This gives {answer} valid boundary cycles."
    )
    parameters = {
        "block_a": [0, 0, width_a, height_a],
        "block_b": [shift_x, shift_y, width_b, height_b],
    }
    return _row(
        mechanism="irregular_grid_rectangles", ordinal=ordinal, problem=problem,
        solution=_boxed_solution(explanation, answer), oracle=answer,
        parameters=parameters,
        audit_facts={"horizontal_unit_edges": len(horizontal), "vertical_unit_edges": len(vertical)},
    )


def _top_k_case(seed: int, ordinal: int) -> Dict[str, Any]:
    rng = _rng(seed, "top_k_replacement_mean", ordinal)
    k = rng.choice((5, 7, 9))
    mean = rng.randint(500, 1200)
    offsets = sorted(rng.sample(range(25, 190), k // 2))
    values = [mean - value for value in reversed(offsets)] + [mean] + [
        mean + value for value in offsets
    ]
    old_min, old_max = values[0], values[-1]
    increase = rng.randint(20, 85)
    new_value = old_min + k * increase
    if new_value <= old_max:
        increase += math.ceil((old_max - new_value + 1) / k)
        new_value = old_min + k * increase
    nouns = rng.choice(("buildings", "mountains", "annual salaries", "test scores"))
    problem = (
        f"The current top {k} {nouns} have mean value {mean}. Their smallest value "
        f"is {old_min} and their largest value is {old_max}. A new value of "
        f"{new_value} is added and becomes the largest, so the old smallest member "
        f"drops out of the top {k}. By how much does the mean of the top {k} increase?"
    )
    explanation = (
        f"The selected set keeps size {k}; only {old_min} is replaced by {new_value}. "
        f"The sum rises by {new_value - old_min}, so the mean rises by "
        f"({new_value}-{old_min})/{k}={increase}."
    )
    return _row(
        mechanism="top_k_replacement_mean", ordinal=ordinal, problem=problem,
        solution=_boxed_solution(explanation, increase), oracle=increase,
        parameters={"k": k, "mean": mean, "minimum": old_min, "maximum": old_max, "new_value": new_value},
        audit_facts={"constructed_values": values, "sum_delta": new_value - old_min},
    )


def _partition_text(parts: Sequence[int]) -> str:
    return " and ".join(str(value) for value in sorted(parts, reverse=True))


def _reroll_case(seed: int, ordinal: int) -> Dict[str, Any]:
    rng = _rng(seed, "conditional_reroll_multiplicity", ordinal)
    sides = rng.randint(4, 8)
    kept = rng.choice((2, 3))
    rerolled = rng.choice((3, 4))
    total_dice = kept + rerolled
    first = total_dice // 2 + 1
    target = (first, total_dice - first)
    count_all_same = rng.random() < 0.5
    favorable = 0
    for outcome in itertools.product(range(1, sides + 1), repeat=rerolled):
        counts = Counter((1,) * kept + outcome)
        multiplicities = tuple(sorted(counts.values(), reverse=True))
        success = multiplicities == tuple(sorted(target, reverse=True))
        if count_all_same and multiplicities == (total_dice,):
            success = True
        favorable += success
    denominator = sides**rerolled
    probability = Fraction(favorable, denominator)
    answer = (
        str(probability.numerator)
        if probability.denominator == 1
        else rf"\frac{{{probability.numerator}}}{{{probability.denominator}}}"
    )
    extra = " All dice showing one value also counts as success." if count_all_same else ""
    problem = (
        f"A fair {sides}-sided die is used. {kept} dice already show the same face and "
        f"are kept; the remaining {rerolled} distinguishable dice are rerolled "
        f"independently. The final {total_dice} dice are successful exactly when two "
        f"distinct face values occur with multiplicities {_partition_text(target)}."
        f"{extra} What is the probability of success?"
    )
    explanation = (
        f"There are {sides}^{rerolled}={denominator} ordered reroll outcomes. Exact "
        f"enumeration by final face multiplicities gives {favorable} successful outcomes, "
        f"so the reduced probability is {answer}."
    )
    return _row(
        mechanism="conditional_reroll_multiplicity", ordinal=ordinal, problem=problem,
        solution=_boxed_solution(explanation, answer), oracle=answer,
        parameters={"sides": sides, "kept": kept, "rerolled": rerolled, "target": list(target), "all_same_counts": count_all_same},
        audit_facts={"favorable_ordered_outcomes": favorable, "total_ordered_outcomes": denominator},
    )


def _symmetry_case(seed: int, ordinal: int) -> Dict[str, Any]:
    rng = _rng(seed, "distinct_label_symmetry_quotient", ordinal)
    variant = ordinal % 3
    if variant == 0:
        rows, columns = rng.choice(
            ((2, 3), (2, 4), (2, 5), (3, 4), (3, 5), (4, 5))
        )
        objects = rows * columns
        group_order = 4
        layout = f"a {rows} by {columns} rectangular grid"
        transformations = "the rectangle's rotations and reflections"
        parameters = {"layout": "non_square_grid", "rows": rows, "columns": columns}
    elif variant == 1:
        side = rng.choice((2, 3, 4, 5))
        objects = side * side
        group_order = 8
        layout = f"a {side} by {side} square grid"
        transformations = "all rotations and reflections of the square"
        parameters = {"layout": "square_grid", "side": side}
    else:
        objects = rng.randint(5, 14)
        group_order = 2 * objects
        layout = f"the vertices of a regular {objects}-gon"
        transformations = "all rotations and reflections of the polygon"
        parameters = {"layout": "regular_polygon", "vertices": objects}
    answer = math.factorial(objects) // group_order
    problem = (
        f"In how many inequivalent ways can {objects} pairwise distinct labeled tokens "
        f"be placed bijectively on {layout}, if two placements are equivalent under "
        f"{transformations}?"
    )
    explanation = (
        f"There are {objects}! labeled placements. Because every label is distinct, no "
        "nonidentity symmetry fixes a placement: every such symmetry moves at least one "
        f"token to a differently labeled position. Hence every orbit has size {group_order}, "
        f"giving {objects}!/{group_order}={answer}."
    )
    return _row(
        mechanism="distinct_label_symmetry_quotient", ordinal=ordinal, problem=problem,
        solution=_boxed_solution(explanation, answer), oracle=answer,
        parameters={**parameters, "objects": objects, "group_order": group_order},
        audit_facts={"action_is_free": True, "orbit_size": group_order},
    )


def _circular_case(seed: int, ordinal: int) -> Dict[str, Any]:
    rng = _rng(seed, "circular_mutual_nonadjacency", ordinal)
    restricted = rng.randint(3, 5)
    unrestricted = rng.randint(restricted, restricted + 5)
    total = restricted + unrestricted
    answer = (
        math.factorial(unrestricted - 1)
        * math.comb(unrestricted, restricted)
        * math.factorial(restricted)
    )
    labels = [chr(ord("A") + index) for index in range(restricted)]
    names = ", ".join(labels[:-1]) + f", and {labels[-1]}"
    problem = (
        f"How many rotationally distinct circular seatings are there for {total} distinct "
        f"people if the specified people {names} must be pairwise nonadjacent? Reflections "
        "are considered different."
    )
    explanation = (
        f"First arrange the {unrestricted} unrestricted people around the circle in "
        f"({unrestricted}-1)! ways. Their {unrestricted} gaps are distinct relative to that "
        f"seating. Choose {restricted} gaps and permute the specified people, giving "
        f"({unrestricted}-1)! C({unrestricted},{restricted}) {restricted}!={answer}."
    )
    return _row(
        mechanism="circular_mutual_nonadjacency", ordinal=ordinal, problem=problem,
        solution=_boxed_solution(explanation, answer), oracle=answer,
        parameters={"total_people": total, "restricted_people": restricted, "unrestricted_people": unrestricted},
        audit_facts={"gap_count": unrestricted, "formula": "(u-1)!*C(u,r)*r!"},
    )


BUILDERS = {
    "irregular_grid_rectangles": _rectangle_case,
    "top_k_replacement_mean": _top_k_case,
    "conditional_reroll_multiplicity": _reroll_case,
    "distinct_label_symmetry_quotient": _symmetry_case,
    "circular_mutual_nonadjacency": _circular_case,
}


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def generate_bundle(
    *, output_dir: str | Path, seed: int = 94017, cases_per_mechanism: int = 12,
    source_report: str | Path = "results/20260809_MATH_true_failures.json",
    source_dataset: str | Path = "data/datasets/math_validate.jsonl",
    num_runs: int = 3, min_error_rate: float = 0.20,
    min_stable_problems: int = 3, min_failed_runs_per_problem: int = 2,
) -> Path:
    if not 12 <= cases_per_mechanism <= 20:
        raise ValueError("cases_per_mechanism must be between 12 and 20")
    if num_runs != 3:
        raise ValueError("baseline headroom protocol requires exactly 3 runs")
    if not 0 < min_error_rate <= 1:
        raise ValueError("min_error_rate must be in (0, 1]")
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    output.mkdir(parents=True)

    rows = []
    seen_problem_ids: set[str] = set()
    for mechanism in MECHANISMS:
        for ordinal in range(cases_per_mechanism):
            for attempt in range(100):
                row = BUILDERS[mechanism](seed + attempt * 7919, ordinal)
                problem_id = row["mechanism_metadata"]["problem_id"]
                if problem_id not in seen_problem_ids:
                    seen_problem_ids.add(problem_id)
                    rows.append(row)
                    break
            else:
                raise RuntimeError(
                    f"could not generate a unique {mechanism} screening case"
                )
    problem_ids = [row["mechanism_metadata"]["problem_id"] for row in rows]
    if len(problem_ids) != len(set(problem_ids)):
        raise AssertionError("generated screening problems are not unique")
    data_path = output / "screening.jsonl"
    _write_jsonl(data_path, rows)

    source_report_path = Path(source_report).resolve()
    source_dataset_path = Path(source_dataset).resolve()
    family_id = hashlib.sha256(
        f"{GENERATOR_VERSION}:{seed}:{cases_per_mechanism}".encode()
    ).hexdigest()[:16]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "family_id": family_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "experiment_role": "baseline_headroom_screening",
        "source": {
            "true_failure_report": str(source_report_path),
            "true_failure_report_sha256": file_sha256(source_report_path),
            "dataset": str(source_dataset_path),
            "dataset_sha256": file_sha256(source_dataset_path),
            "excluded_source_indices": [91],
        },
        "baseline_only_contract": {
            "candidate_generation_allowed": False,
            "candidate_execution_allowed": False,
            "operator_library_access_allowed": False,
            "routing_allowed": False,
            "solver": "generic_two_stage_baseline",
        },
        "screening_protocol": {
            "num_runs": num_runs,
            "cases_per_mechanism": cases_per_mechanism,
            "min_audited_error_rate": min_error_rate,
            "min_stable_failed_problems": min_stable_problems,
            "stable_failure_min_failed_runs": min_failed_runs_per_problem,
            "manual_audit_required": True,
            "accepted_error_labels": [
                "true_model_error", "scorer_false_negative",
                "invalid_generated_problem", "execution_error",
            ],
        },
        "mechanisms": [
            {
                "mechanism_id": mechanism,
                "source_failure_index": SOURCE_FAILURE_INDEX[mechanism],
                "count": cases_per_mechanism,
            }
            for mechanism in MECHANISMS
        ],
        "screening_data": {
            "path": data_path.name,
            "sha256": file_sha256(data_path),
            "count": len(rows),
        },
    }
    manifest_path = output / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return manifest_path


def load_bundle(path: str | Path) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    manifest_path = Path(path).resolve()
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported baseline-screening schema")
    if manifest.get("generator_version") not in SUPPORTED_GENERATOR_VERSIONS:
        raise ValueError("unsupported baseline-screening generator")
    contract = manifest.get("baseline_only_contract", {})
    if any(
        contract.get(key) is not False
        for key in (
            "candidate_generation_allowed", "candidate_execution_allowed",
            "operator_library_access_allowed", "routing_allowed",
        )
    ):
        raise ValueError("manifest does not enforce the baseline-only contract")
    data_path = manifest_path.parent / manifest["screening_data"]["path"]
    if file_sha256(data_path) != manifest["screening_data"]["sha256"]:
        raise ValueError("screening data hash mismatch")
    rows = [json.loads(line) for line in data_path.read_text(encoding="utf-8").splitlines()]
    if len(rows) != manifest["screening_data"]["count"]:
        raise ValueError("screening row count mismatch")
    manifest["_manifest_path"] = str(manifest_path)
    manifest["_manifest_sha256"] = file_sha256(manifest_path)
    return manifest, rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate baseline headroom mechanisms.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=94017)
    parser.add_argument("--cases-per-mechanism", type=int, default=12)
    parser.add_argument("--source-report", default="results/20260809_MATH_true_failures.json")
    parser.add_argument("--source-dataset", default="data/datasets/math_validate.jsonl")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = generate_bundle(
        output_dir=args.output_dir, seed=args.seed,
        cases_per_mechanism=args.cases_per_mechanism,
        source_report=args.source_report, source_dataset=args.source_dataset,
    )
    print(f"Baseline-screening manifest: {path}")
    print("Candidate generation/execution: prohibited")


if __name__ == "__main__":
    main()
