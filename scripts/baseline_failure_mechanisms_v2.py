# -*- coding: utf-8 -*-
"""New exact-oracle mechanism families mined from the audited 119-problem scan.

The ``screen`` stage creates baseline-only data.  The ``promote`` stage is the
only path that creates a design/gate/regression family, and it refuses to do so
unless the audited baseline screen accepted the requested mechanism.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
from datetime import datetime, timezone
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping

from scripts.baseline_failure_mechanisms import file_sha256, stable_problem_id


GENERATOR_VERSION = "baseline_headroom_mechanisms_v2_2"
SCHEMA_VERSION = 1
DIFFICULTY_LEVELS = ("easy", "medium", "hard")
DIFFICULTY_SPECS = {
    "parenthesization_distinct_value_dedup": {
        "easy": "5 operands; addition and multiplication",
        "medium": "6 operands; includes subtraction",
        "hard": "7 operands; at least two subtraction operators",
    },
    "repeated_digit_interval_inclusion_exclusion": {
        "easy": "4-digit irregular interval; nonzero target digit",
        "medium": "5-digit irregular interval; zero may be the target",
        "hard": "6-digit irregular interval; at least three target occurrences",
    },
    "staged_conversion_rounding": {
        "easy": "equal allocation; two-decimal exchange rates",
        "medium": "3/5 versus 2/5 allocation; two-decimal rates",
        "hard": "4/7 versus 3/7 allocation; three-decimal rates",
    },
    "restricted_digit_prime_enumeration": {
        "easy": "4 available digits; numbers of length at most 2",
        "medium": "5 available digits; numbers of length at most 3",
        "hard": "6 available digits; numbers of length at most 4",
    },
    "domain_filtered_trigonometric_branches": {
        "easy": "2 algebraic roots; acute/obtuse filter",
        "medium": "3 algebraic roots; open interval filter",
        "hard": "4 algebraic roots; open interval filter",
    },
}
MECHANISMS = (
    "domain_filtered_trigonometric_branches",
    "parenthesization_distinct_value_dedup",
    "repeated_digit_interval_inclusion_exclusion",
    "restricted_digit_prime_enumeration",
    "staged_conversion_rounding",
)
EXCLUDED_PRIOR_MECHANISMS = (
    "irregular_grid_rectangles",
    "top_k_replacement_mean",
    "conditional_reroll_multiplicity",
    "distinct_label_symmetry_quotient",
    "circular_mutual_nonadjacency",
    "parametric_path_retracing",
)
SOURCE_INDICES = {
    "parenthesization_distinct_value_dedup": [7],
    "repeated_digit_interval_inclusion_exclusion": [19],
    "staged_conversion_rounding": [20],
    "restricted_digit_prime_enumeration": [24],
    "domain_filtered_trigonometric_branches": [78],
}
CAPABILITY_GAPS = {
    "parenthesization_distinct_value_dedup": (
        "Enumerate all order-preserving parenthesizations and deduplicate equal values."
    ),
    "repeated_digit_interval_inclusion_exclusion": (
        "Count bounded integers with repeated target digits without overlap or leading-zero errors."
    ),
    "staged_conversion_rounding": (
        "Preserve exact quantities through multiple conversions and round only the requested final result."
    ),
    "restricted_digit_prime_enumeration": (
        "Enumerate legal digit arrangements exactly once and apply primality filtering."
    ),
    "domain_filtered_trigonometric_branches": (
        "Generate all algebraic trigonometric branches, then reject branches outside the stated domain."
    ),
}


def _rng(seed: int, mechanism: str, ordinal: int) -> random.Random:
    digest = hashlib.sha256(
        f"{GENERATOR_VERSION}:{seed}:{mechanism}:{ordinal}".encode()
    ).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _json_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _boxed(explanation: str, answer: str | int) -> str:
    return f"{explanation} Therefore the answer is $\\boxed{{{answer}}}$."


def _row(
    *, mechanism: str, ordinal: int, problem: str, answer: str | int,
    explanation: str, parameters: Mapping[str, Any], audit_facts: Mapping[str, Any],
    split: str | None = None, difficulty: str | None = None,
) -> Dict[str, Any]:
    case_id = _json_hash(
        {"mechanism": mechanism, "ordinal": ordinal, "parameters": parameters}
    )[:16]
    metadata: Dict[str, Any] = {
        "family": mechanism,
        "generator_version": GENERATOR_VERSION,
        "case_id": case_id,
        "problem_id": stable_problem_id(problem),
        "origin": "generated_exact_oracle",
        "source_failure_indices": SOURCE_INDICES[mechanism],
        "ordinal": ordinal,
        "parameters": dict(parameters),
        "oracle_answer": str(answer),
        "audit_facts": dict(audit_facts),
    }
    if difficulty is not None:
        if difficulty not in DIFFICULTY_LEVELS:
            raise ValueError(f"unsupported difficulty: {difficulty}")
        metadata["difficulty_bin"] = difficulty
    if split is not None:
        metadata["split"] = split
    math_type = (
        "Counting & Probability"
        if mechanism in {
            "parenthesization_distinct_value_dedup",
            "repeated_digit_interval_inclusion_exclusion",
            "restricted_digit_prime_enumeration",
        }
        else "Prealgebra" if mechanism == "staged_conversion_rounding" else "Precalculus"
    )
    return {
        "problem": problem,
        "solution": _boxed(explanation, answer),
        "level": "Level 5",
        "type": math_type,
        "mechanism_metadata": metadata,
    }


def _parenthesized_values(numbers: tuple[int, ...], operators: tuple[str, ...]) -> set[Fraction]:
    @lru_cache(maxsize=None)
    def solve(left: int, right: int) -> frozenset[Fraction]:
        if right - left == 1:
            return frozenset({Fraction(numbers[left])})
        values: set[Fraction] = set()
        for split in range(left + 1, right):
            operator = operators[split - 1]
            for a in solve(left, split):
                for b in solve(split, right):
                    values.add(a + b if operator == "+" else a - b if operator == "-" else a * b)
        return frozenset(values)
    return set(solve(0, len(numbers)))


def _parenthesization_case(
    seed: int, ordinal: int, split: str | None = None,
    difficulty: str | None = None,
) -> Dict[str, Any]:
    mechanism = "parenthesization_distinct_value_dedup"
    rng = _rng(seed, mechanism, ordinal)
    count = (
        {"easy": 5, "medium": 6, "hard": 7}[difficulty]
        if difficulty else rng.choice((5, 6))
    )
    numbers = tuple(rng.randint(2, 8) for _ in range(count))
    if difficulty == "easy":
        operators_list = ["*", "+"] + [rng.choice(("*", "+")) for _ in range(count - 3)]
    elif difficulty == "hard":
        operators_list = ["*", "+", "-", "-"] + [
            rng.choice(("*", "+", "-")) for _ in range(count - 5)
        ]
    else:
        operators_list = ["*", "+", "-"] + [rng.choice(("*", "+")) for _ in range(count - 4)]
    rng.shuffle(operators_list)
    operators = tuple(operators_list)
    values = _parenthesized_values(numbers, operators)
    expression = " ".join(
        part for pair in zip(map(str, numbers), operators + ("",)) for part in pair if part
    )
    answer = len(values)
    problem = (
        f"Consider the expression {expression}. Fully parenthesize it in every possible "
        "way without changing the order of numbers or operations. Different "
        f"parenthesizations that have the same numerical value count only once. How many "
        "distinct values are possible?"
    )
    explanation = (
        "Dynamic programming over every contiguous subexpression combines the exact value "
        f"sets at each possible final operation. The resulting set has {answer} elements."
    )
    return _row(
        mechanism=mechanism, ordinal=ordinal, problem=problem, answer=answer,
        explanation=explanation, parameters={"numbers": numbers, "operators": operators},
        audit_facts={"sorted_values": [str(value) for value in sorted(values)]},
        split=split, difficulty=difficulty,
    )


def _count_digit_matches(limit: int, target: int, multiplicity: int) -> int:
    """Count positive integers <= limit with enough target digits via digit DP."""
    if limit <= 0:
        return 0
    digits = tuple(map(int, str(limit)))

    @lru_cache(maxsize=None)
    def solve(position: int, count: int, tight: bool, started: bool) -> int:
        if position == len(digits):
            return int(started and count >= multiplicity)
        upper = digits[position] if tight else 9
        total = 0
        for digit in range(upper + 1):
            next_started = started or digit != 0
            next_count = count
            if next_started and digit == target:
                next_count = min(multiplicity, count + 1)
            total += solve(
                position + 1,
                next_count,
                tight and digit == upper,
                next_started,
            )
        return total

    return solve(0, 0, True, False)


def _repeated_digit_case(
    seed: int, ordinal: int, split: str | None = None,
    difficulty: str | None = None,
) -> Dict[str, Any]:
    mechanism = "repeated_digit_interval_inclusion_exclusion"
    rng = _rng(seed, mechanism, ordinal)
    digits = (
        {"easy": 4, "medium": 5, "hard": 6}[difficulty]
        if difficulty else rng.choice((3, 4, 5))
    )
    lower = 10 ** (digits - 1) + rng.randint(0, 2 * 10 ** (digits - 2))
    upper = rng.randint(max(lower + 500, 7 * 10 ** (digits - 1)), 10**digits - 1)
    target = (
        rng.choice((2, 3, 5, 7)) if difficulty == "easy"
        else rng.choice((0, 2, 3, 5, 7))
    )
    multiplicity = 3 if difficulty == "hard" else rng.choice((2, 2, 3))
    answer = _count_digit_matches(upper, target, multiplicity) - _count_digit_matches(
        lower - 1, target, multiplicity
    )
    problem = (
        f"How many integers n with {lower} <= n <= {upper} contain the digit {target} "
        f"at least {multiplicity} times in their ordinary decimal representation?"
    )
    explanation = (
        "Partition by digit positions (or use a tight digit-DP), enforce the interval "
        f"bounds and no leading zero, and deduplicate overlaps. This gives {answer} integers."
    )
    return _row(
        mechanism=mechanism, ordinal=ordinal, problem=problem, answer=answer,
        explanation=explanation,
        parameters={"lower": lower, "upper": upper, "target": target, "multiplicity": multiplicity},
        audit_facts={"match_count": answer, "oracle_method": "tight_digit_dp"},
        split=split, difficulty=difficulty,
    )


def _round_half_up(value: Fraction) -> int:
    quotient, remainder = divmod(value.numerator, value.denominator)
    return quotient + int(2 * remainder >= value.denominator)


def _conversion_case(
    seed: int, ordinal: int, split: str | None = None,
    difficulty: str | None = None,
) -> Dict[str, Any]:
    mechanism = "staged_conversion_rounding"
    rng = _rng(seed, mechanism, ordinal)
    if difficulty == "medium":
        share_a, denominator, rate_scale = 3, 5, 100
    elif difficulty == "hard":
        share_a, denominator, rate_scale = 4, 7, 1000
    else:
        share_a, denominator, rate_scale = 1, 2, 100
    amount = denominator * rng.randint(60, 360)
    rate_a = Fraction(
        rng.randint(700 if difficulty == "hard" else 75, 1150 if difficulty == "hard" else 125),
        rate_scale,
    )
    rate_b = Fraction(
        rng.randint(1400 if difficulty == "hard" else 140, 2200 if difficulty == "hard" else 210),
        rate_scale,
    )
    allocation_a = Fraction(amount * share_a, denominator)
    allocation_b = amount - allocation_a
    exact_a = allocation_a / rate_a
    exact_b = allocation_b / rate_b
    exact_difference = exact_a - exact_b
    answer = _round_half_up(exact_difference)
    problem = (
        f"A traveler allocates {share_a}/{denominator} of ${amount} to currency A and the "
        f"remaining {denominator-share_a}/{denominator} to currency B. The exchange rates are "
        f"{float(rate_a):.{3 if rate_scale == 1000 else 2}f} US dollars per A and "
        f"{float(rate_b):.{3 if rate_scale == 1000 else 2}f} US dollars per B. "
        "To the nearest whole unit, how many more "
        "units of A than B does the traveler receive? Do not round intermediate values."
    )
    explanation = (
        f"The exact quantities are {allocation_a}/{rate_a} and {allocation_b}/{rate_b}. "
        f"Their exact difference is {exact_difference}; rounding only this final value gives {answer}."
    )
    return _row(
        mechanism=mechanism, ordinal=ordinal, problem=problem, answer=answer,
        explanation=explanation,
        parameters={
            "amount": amount, "share_a": [share_a, denominator],
            "rate_a": str(rate_a), "rate_b": str(rate_b),
        },
        audit_facts={"exact_a": str(exact_a), "exact_b": str(exact_b), "difference": str(exact_difference)},
        split=split, difficulty=difficulty,
    )


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _prime_case(
    seed: int, ordinal: int, split: str | None = None,
    difficulty: str | None = None,
) -> Dict[str, Any]:
    mechanism = "restricted_digit_prime_enumeration"
    rng = _rng(seed, mechanism, ordinal)
    digit_count = (
        {"easy": 4, "medium": 5, "hard": 6}[difficulty]
        if difficulty else rng.choice((4, 5, 6))
    )
    digits = tuple(sorted(rng.sample(range(10), digit_count)))
    max_length = (
        {"easy": 2, "medium": 3, "hard": 4}[difficulty]
        if difficulty else rng.choice((2, 3))
    )
    primes: set[int] = set()
    for length in range(1, max_length + 1):
        for arrangement in itertools.permutations(digits, length):
            if arrangement[0] == 0:
                continue
            value = int("".join(map(str, arrangement)))
            if _is_prime(value):
                primes.add(value)
    answer = len(primes)
    problem = (
        f"Choose between one and {max_length} distinct digits from the set "
        f"{{{', '.join(map(str, digits))}}} and arrange the chosen digits without repetition "
        "to form a positive integer (a leading zero is not allowed). How many distinct "
        "prime numbers can be formed?"
    )
    explanation = (
        "Enumerate legal permutations by length, reject leading zero, deduplicate the "
        f"formed integers, and test primality. The prime set has {answer} elements."
    )
    return _row(
        mechanism=mechanism, ordinal=ordinal, problem=problem, answer=answer,
        explanation=explanation, parameters={"digits": digits, "max_length": max_length},
        audit_facts={"primes": sorted(primes)}, split=split, difficulty=difficulty,
    )


TRIG_FACTORS = {
    30: r"(2\cos A-\sqrt{3})",
    45: r"(\sqrt{2}\cos A-1)",
    60: r"(2\cos A-1)",
    90: r"\cos A",
    120: r"(2\cos A+1)",
    135: r"(\sqrt{2}\cos A+1)",
    150: r"(2\cos A+\sqrt{3})",
}


def _trig_case(
    seed: int, ordinal: int, split: str | None = None,
    difficulty: str | None = None,
) -> Dict[str, Any]:
    mechanism = "domain_filtered_trigonometric_branches"
    rng = _rng(seed, mechanism, ordinal)
    root_count = {"easy": 2, "medium": 3, "hard": 4}.get(difficulty, 3)
    roots = sorted(rng.sample(tuple(TRIG_FACTORS), root_count))
    qualifier_mode = difficulty == "easy" or (difficulty is None and ordinal % 2)
    if qualifier_mode:
        qualifier = rng.choice(("acute", "obtuse"))
        accepted = (
            [x for x in roots if x < 90]
            if qualifier == "acute"
            else [x for x in roots if x > 90]
        )
        if not accepted:
            qualifier = "acute" if roots[0] < 90 else "obtuse"
            accepted = (
                [x for x in roots if x < 90]
                if qualifier == "acute"
                else [x for x in roots if x > 90]
            )
        domain = f"A is {qualifier}"
    else:
        low = rng.choice((20, 40, 70, 100))
        high = rng.choice((110, 140, 160))
        if low >= high or not [x for x in roots if low < x < high]:
            low, high = roots[0] - 1, roots[-1] + 1
        accepted = [x for x in roots if low < x < high]
        domain = f"{low} degrees < A < {high} degrees"
    answer = ",".join(map(str, accepted))
    equation = "".join(TRIG_FACTORS[root] for root in roots) + "=0"
    problem = (
        f"An angle A satisfies 0 degrees < A < 180 degrees and ${equation}$. In addition, "
        f"{domain}. Enter all possible values of A in degrees, separated by commas."
    )
    explanation = (
        f"The factors give candidate angles {roots}. Applying the additional domain "
        f"condition leaves {accepted}."
    )
    return _row(
        mechanism=mechanism, ordinal=ordinal, problem=problem, answer=answer,
        explanation=explanation, parameters={"roots": roots, "domain": domain},
        audit_facts={"candidate_angles": roots, "accepted_angles": accepted},
        split=split, difficulty=difficulty,
    )


BUILDERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "parenthesization_distinct_value_dedup": _parenthesization_case,
    "repeated_digit_interval_inclusion_exclusion": _repeated_digit_case,
    "staged_conversion_rounding": _conversion_case,
    "restricted_digit_prime_enumeration": _prime_case,
    "domain_filtered_trigonometric_branches": _trig_case,
}


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _unique_rows(
    mechanism: str, *, seed: int, count: int, split: str | None = None,
    occupied: set[str] | None = None, difficulty: str | None = None,
) -> list[Dict[str, Any]]:
    seen = occupied if occupied is not None else set()
    rows = []
    for ordinal in range(count):
        for attempt in range(100):
            row = BUILDERS[mechanism](
                seed + attempt * 7919, ordinal, split, difficulty
            )
            problem_id = row["mechanism_metadata"]["problem_id"]
            if problem_id not in seen:
                seen.add(problem_id)
                rows.append(row)
                break
        else:
            raise RuntimeError(f"could not make a unique {mechanism} case")
    return rows


def generate_screen(
    *, output_dir: Path, source_report: Path, seed: int, cases_per_mechanism: int,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    if not 12 <= cases_per_mechanism <= 20:
        raise ValueError("cases per mechanism must be between 12 and 20")
    source = json.loads(source_report.read_text(encoding="utf-8"))
    selected = tuple(source.get("selected_new_mechanisms", []))
    if set(selected) != set(MECHANISMS):
        raise ValueError("audited source report does not select the expected five new mechanisms")
    output_dir.mkdir(parents=True)
    occupied: set[str] = set()
    rows = []
    for mechanism in MECHANISMS:
        rows.extend(
            _unique_rows(
                mechanism, seed=seed, count=cases_per_mechanism, occupied=occupied
            )
        )
    data_path = output_dir / "screening.jsonl"
    _write_jsonl(data_path, rows)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "family_id": _json_hash({"seed": seed, "count": cases_per_mechanism})[:16],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "experiment_role": "baseline_headroom_screening",
        "source": {
            "audited_119_report": str(source_report.resolve()),
            "audited_119_report_sha256": file_sha256(source_report),
            "excluded_prior_mechanisms": list(EXCLUDED_PRIOR_MECHANISMS),
        },
        "baseline_only_contract": {
            "candidate_generation_allowed": False,
            "candidate_execution_allowed": False,
            "operator_library_access_allowed": False,
            "routing_allowed": False,
            "solver": "generic_two_stage_baseline",
        },
        "screening_protocol": {
            "num_runs": 3,
            "cases_per_mechanism": cases_per_mechanism,
            "min_audited_error_rate": 0.20,
            "min_stable_failed_problems": 3,
            "stable_failure_min_failed_runs": 2,
            "manual_audit_required": True,
        },
        "mechanisms": [
            {"mechanism_id": mechanism, "source_failure_indices": SOURCE_INDICES[mechanism], "count": cases_per_mechanism}
            for mechanism in MECHANISMS
        ],
        "screening_data": {"path": data_path.name, "sha256": file_sha256(data_path), "count": len(rows)},
    }
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def promote_family(
    *, output_dir: Path, audited_summary: Path, mechanism: str, seed: int,
    design_count: int, gate_count: int, regression_count: int,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    audit = json.loads(audited_summary.read_text(encoding="utf-8"))
    if audit.get("status") != "complete" or mechanism not in audit.get("accepted_mechanisms", []):
        raise ValueError(f"mechanism did not pass audited baseline headroom gate: {mechanism}")
    if mechanism not in MECHANISMS:
        raise ValueError(f"not a v2 mechanism: {mechanism}")
    output_dir.mkdir(parents=True)
    occupied: set[str] = set()
    counts = {"design": design_count, "gate": gate_count, "regression": regression_count}
    split_manifest = {}
    for offset, (split, count) in enumerate(counts.items(), 1):
        rows = _unique_rows(
            mechanism, seed=seed + offset * 100003, count=count,
            split=split, occupied=occupied,
        )
        path = output_dir / f"{split}.jsonl"
        _write_jsonl(path, rows)
        split_manifest[split] = {
            "path": path.name,
            "sha256": file_sha256(path),
            "count": len(rows),
            "visible_to_operator_generator": split == "design",
        }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "family_id": _json_hash({"mechanism": mechanism, "seed": seed, "counts": counts})[:16],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "experiment_role": "development",
        "mechanism": {
            "name": mechanism,
            "capability_gap": CAPABILITY_GAPS[mechanism],
            "source_indices": SOURCE_INDICES[mechanism],
        },
        "headroom_gate": {
            "status": "passed",
            "audited_summary": str(audited_summary.resolve()),
            "audited_summary_sha256": file_sha256(audited_summary),
            "thresholds_frozen": True,
        },
        "split_policy": {
            "design": "Fresh visible cases generated only after headroom acceptance.",
            "gate": "Fresh hidden in-family capability cases.",
            "regression": "Fresh hidden neighboring cases; candidate must not lose to baseline.",
        },
        "splits": split_manifest,
    }
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate v2 baseline-only mechanisms.")
    sub = parser.add_subparsers(dest="stage", required=True)
    screen = sub.add_parser("screen")
    screen.add_argument("--output-dir", required=True)
    screen.add_argument("--source-report", required=True)
    screen.add_argument("--seed", type=int, default=95021)
    screen.add_argument("--cases-per-mechanism", type=int, default=12)
    promote = sub.add_parser("promote")
    promote.add_argument("--output-dir", required=True)
    promote.add_argument("--audited-summary", required=True)
    promote.add_argument("--mechanism", required=True, choices=MECHANISMS)
    promote.add_argument("--seed", type=int, default=96031)
    promote.add_argument("--design-count", type=int, default=8)
    promote.add_argument("--gate-count", type=int, default=12)
    promote.add_argument("--regression-count", type=int, default=8)
    args = parser.parse_args()
    if args.stage == "screen":
        path = generate_screen(
            output_dir=Path(args.output_dir), source_report=Path(args.source_report),
            seed=args.seed, cases_per_mechanism=args.cases_per_mechanism,
        )
        print(f"Baseline-only manifest: {path.resolve()}")
        print("Candidate generation/execution is prohibited until audited promotion.")
        return
    path = promote_family(
        output_dir=Path(args.output_dir), audited_summary=Path(args.audited_summary),
        mechanism=args.mechanism, seed=args.seed, design_count=args.design_count,
        gate_count=args.gate_count, regression_count=args.regression_count,
    )
    print(f"Headroom-gated operator family: {path.resolve()}")


if __name__ == "__main__":
    main()
