# -*- coding: utf-8 -*-
"""Generate a harder hidden trajectory-retracing family with exact oracles.

Unlike v4's shared quadratic parameter, v5 uses cubic S-curves and quartic
double-well latent polynomials. Their expanded coordinate equations hide the
shared scalar parameter and contain two or three internal turning points.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from scripts.operator_library import stable_problem_id
from scripts.trajectory_failure_expander import (
    SCHEMA_VERSION,
    _fraction_latex,
    _json_sha256,
    _largest_square_factor,
    _load_jsonl,
    _polynomial_term,
    _write_jsonl,
    file_sha256,
)


GENERATOR_VERSION = "trajectory_retrace_v5"
SPLITS = ("design", "gate", "regression")
LATENT_FAMILIES = ("cubic_s_curve", "quartic_double_well")


def _case_rng(seed: int, split: str, ordinal: int) -> random.Random:
    payload = f"{GENERATOR_VERSION}:{seed}:{split}:{ordinal}".encode()
    digest = hashlib.sha256(payload).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _radical_latex(coefficient: Fraction, radicand: int) -> str:
    outside, squarefree = _largest_square_factor(radicand)
    coefficient *= outside
    if squarefree == 1:
        return _fraction_latex(coefficient)
    radical = rf"\sqrt{{{squarefree}}}"
    numerator = coefficient.numerator
    denominator = coefficient.denominator
    numerator_text = radical if numerator == 1 else f"{numerator}{radical}"
    if denominator == 1:
        return numerator_text
    return rf"\frac{{{numerator_text}}}{{{denominator}}}"


def _shift(parameters: Mapping[str, int]) -> Fraction:
    return Fraction(parameters["shift_num"], parameters["shift_den"])


def _degree(latent_family: str) -> int:
    return 3 if latent_family == "cubic_s_curve" else 4


def _critical_offsets(latent_family: str, turn_scale: int) -> tuple[int, ...]:
    if latent_family == "cubic_s_curve":
        return (-turn_scale, turn_scale)
    if latent_family == "quartic_double_well":
        return (-turn_scale, 0, turn_scale)
    raise ValueError(f"unknown latent family: {latent_family}")


def _latent_value(latent_family: str, z: Fraction, turn_scale: int) -> Fraction:
    k = Fraction(turn_scale)
    if latent_family == "cubic_s_curve":
        return z**3 - 3 * k**2 * z
    if latent_family == "quartic_double_well":
        return (z**2 - k**2) ** 2
    raise ValueError(f"unknown latent family: {latent_family}")


def _latent_coefficients(
    latent_family: str, shift: Fraction, turn_scale: int
) -> Dict[int, Fraction]:
    h = shift
    k = Fraction(turn_scale)
    if latent_family == "cubic_s_curve":
        return {
            3: Fraction(1),
            2: -3 * h,
            1: 3 * h**2 - 3 * k**2,
            0: -h**3 + 3 * k**2 * h,
        }
    if latent_family == "quartic_double_well":
        return {
            4: Fraction(1),
            3: -4 * h,
            2: 6 * h**2 - 2 * k**2,
            1: -4 * h**3 + 4 * k**2 * h,
            0: h**4 - 2 * k**2 * h**2 + k**4,
        }
    raise ValueError(f"unknown latent family: {latent_family}")


def _parameters(
    rng: random.Random, latent_family: str, mode: str
) -> Dict[str, int]:
    directions = ((1, 2), (2, 1), (2, 3), (3, 2), (1, 3), (3, 4))
    a, b = rng.choice(directions)
    if rng.random() < 0.4:
        a = -a
    if rng.random() < 0.4:
        b = -b
    shift_den = rng.choice((2, 3))
    shift_num = rng.choice(
        [value for value in range(-8, 9) if value and value % shift_den]
    )
    shift = Fraction(shift_num, shift_den)
    turn_scale = rng.randint(1, 3)
    coordinate_scale = shift_den ** _degree(latent_family) * rng.randint(1, 2)

    largest_turn = turn_scale
    if mode in {"multi_retraced_trail", "distance_traveled"}:
        start = math.floor(shift - largest_turn) - rng.randint(1, 3)
        end = math.ceil(shift + largest_turn) + rng.randint(1, 3)
    elif mode == "monotone_trail":
        width = rng.randint(2, 5)
        gap = rng.randint(1, 3)
        if rng.random() < 0.5:
            start = math.ceil(shift + largest_turn) + gap
            end = start + width
        else:
            end = math.floor(shift - largest_turn) - gap
            start = end - width
    else:
        raise ValueError(f"unknown mode: {mode}")

    return {
        "a": a,
        "b": b,
        "shift_num": shift_num,
        "shift_den": shift_den,
        "turn_scale": turn_scale,
        "coordinate_scale": coordinate_scale,
        "start": start,
        "end": end,
        "x_offset": rng.randint(-12, 12),
        "y_offset": rng.randint(-12, 12),
    }


def _metric_values(
    parameters: Mapping[str, int], latent_family: str
) -> Dict[str, Any]:
    shift = _shift(parameters)
    start = Fraction(parameters["start"])
    end = Fraction(parameters["end"])
    critical_times = [
        shift + offset
        for offset in _critical_offsets(latent_family, parameters["turn_scale"])
        if start <= shift + offset <= end
    ]
    checkpoints = [start, *sorted(critical_times), end]
    scale = Fraction(parameters["coordinate_scale"])
    values = [
        scale
        * _latent_value(latent_family, time - shift, parameters["turn_scale"])
        for time in checkpoints
    ]
    image_delta = max(values) - min(values)
    travel_delta = sum(
        abs(right - left) for left, right in zip(values, values[1:])
    )
    norm_squared = parameters["a"] ** 2 + parameters["b"] ** 2
    return {
        "critical_times": critical_times,
        "checkpoint_values": values,
        "image_delta": image_delta,
        "travel_delta": travel_delta,
        "image_answer": _radical_latex(image_delta, norm_squared),
        "travel_answer": _radical_latex(travel_delta, norm_squared),
        "norm_squared": norm_squared,
        "retraces": travel_delta > image_delta,
    }


def _shifted_symbol(shift: Fraction) -> str:
    if shift > 0:
        return rf"(t-{_fraction_latex(shift)})"
    return rf"(t+{_fraction_latex(-shift)})"


def _latent_text(latent_family: str, shift: Fraction, turn_scale: int) -> str:
    z = _shifted_symbol(shift)
    k2 = turn_scale**2
    if latent_family == "cubic_s_curve":
        return rf"{z}^3-{3 * k2}{z}"
    return rf"({z}^2-{k2})^2"


def _expanded_coordinate(
    *,
    latent_family: str,
    shift: Fraction,
    turn_scale: int,
    multiplier: int,
    offset: int,
) -> str:
    coefficients = _latent_coefficients(latent_family, shift, turn_scale)
    coefficients[0] += Fraction(offset, multiplier)
    pieces = []
    for power in sorted(coefficients, reverse=True):
        value = coefficients[power] * multiplier
        if value.denominator != 1:
            raise ValueError("v5 expanded coefficient is not integral")
        symbol = "" if power == 0 else ("t" if power == 1 else f"t^{power}")
        piece = _polynomial_term(int(value), symbol, first=not pieces)
        if piece:
            pieces.append(piece)
    return "".join(pieces) or "0"


def _factored_coordinate(latent: str, multiplier: int, offset: int) -> str:
    if multiplier == 1:
        expression = latent
    elif multiplier == -1:
        expression = "-" + latent
    else:
        expression = f"{multiplier}({latent})"
    if offset:
        expression += ("+" if offset > 0 else "-") + str(abs(offset))
    return expression


def _representation(split: str, ordinal: int) -> str:
    schedules = {
        "design": ("mixed_latent", "dual_expanded", "mixed_latent"),
        # Length four is intentionally coprime to the three-case mode
        # schedule, so both target modes occur in both representations.
        "gate": ("dual_expanded", "mixed_latent", "mixed_latent", "dual_expanded"),
        "regression": ("mixed_latent", "dual_expanded"),
    }
    return schedules[split][ordinal % len(schedules[split])]


def _problem_text(
    parameters: Mapping[str, int],
    latent_family: str,
    mode: str,
    representation: str,
    variant: int,
) -> str:
    shift = _shift(parameters)
    latent = _latent_text(latent_family, shift, parameters["turn_scale"])
    x_multiplier = parameters["a"] * parameters["coordinate_scale"]
    y_multiplier = parameters["b"] * parameters["coordinate_scale"]
    x_expanded = _expanded_coordinate(
        latent_family=latent_family,
        shift=shift,
        turn_scale=parameters["turn_scale"],
        multiplier=x_multiplier,
        offset=parameters["x_offset"],
    )
    y_expanded = _expanded_coordinate(
        latent_family=latent_family,
        shift=shift,
        turn_scale=parameters["turn_scale"],
        multiplier=y_multiplier,
        offset=parameters["y_offset"],
    )
    if representation == "dual_expanded":
        x_expr, y_expr = x_expanded, y_expanded
    elif representation == "mixed_latent":
        x_expr = x_expanded
        y_expr = _factored_coordinate(
            latent, y_multiplier, parameters["y_offset"]
        )
    else:
        raise ValueError(f"unknown representation: {representation}")

    equation = rf"x(t)={x_expr},\quad y(t)={y_expr}"
    interval = rf"{parameters['start']}\le t\le {parameters['end']}"
    if mode == "distance_traveled":
        prompts = (
            "A robot's position is "
            rf"\[{equation}\] for ${interval}$. Find its total distance traveled, counting every repeated traversal.",
            "A particle moves according to "
            rf"\[{equation}\] on ${interval}$. Compute the total distance it travels, including backtracking.",
        )
    else:
        prompts = (
            "A marker leaves ink at every point it visits. Its position is "
            rf"\[{equation}\] for ${interval}$. Find the length of the final geometric ink trail, counting an overwritten portion only once.",
            "A light traces a stationary curve while moving according to "
            rf"\[{equation}\] on ${interval}$. Find the length of the geometric curve visible after the motion, counting distinct points only.",
            "A paint-covered point has position "
            rf"\[{equation}\] for ${interval}$. Determine the length of its paint trail rather than its accumulated travel distance.",
        )
    return prompts[variant % len(prompts)]


def _solution_text(
    parameters: Mapping[str, int],
    latent_family: str,
    mode: str,
    metrics: Mapping[str, Any],
) -> str:
    latent = _latent_text(
        latent_family, _shift(parameters), parameters["turn_scale"]
    )
    critical = ",".join(_fraction_latex(value) for value in metrics["critical_times"])
    if mode == "distance_traveled":
        delta = metrics["travel_delta"]
        answer = metrics["travel_answer"]
        rule = "Sum the absolute changes between consecutive turning points."
    else:
        delta = metrics["image_delta"]
        answer = metrics["image_answer"]
        rule = "Use the range of the shared parameter, so repeated portions count once."
    return (
        rf"Let $u={latent}$. Both coordinates are affine functions of $u$, so the image is a line segment. "
        rf"The internal critical times in the interval are ${critical or 'none'}$. "
        f"{rule} The resulting scalar variation is ${_fraction_latex(delta)}$, "
        rf"and the direction-vector norm is $\sqrt{{{metrics['norm_squared']}}}$. "
        rf"Therefore the answer is $\boxed{{{answer}}}$."
    )


def build_generated_row(
    *, seed: int, split: str, ordinal: int, mode: str, used: set[str]
) -> Dict[str, Any]:
    rng = _case_rng(seed, f"{split}:{mode}", ordinal)
    latent_family = LATENT_FAMILIES[ordinal % len(LATENT_FAMILIES)]
    representation = _representation(split, ordinal)
    for _ in range(100):
        parameters = _parameters(rng, latent_family, mode)
        fingerprint = _json_sha256(
            {
                "latent_family": latent_family,
                "mode": mode,
                "parameters": parameters,
            }
        )[:16]
        if fingerprint not in used:
            used.add(fingerprint)
            break
    else:
        raise RuntimeError("could not generate a unique v5 trajectory case")

    metrics = _metric_values(parameters, latent_family)
    if mode in {"multi_retraced_trail", "distance_traveled"} and not metrics["retraces"]:
        raise AssertionError("v5 retraced case did not produce repeated traversal")
    problem = _problem_text(
        parameters, latent_family, mode, representation, ordinal
    )
    answer_key = "travel_answer" if mode == "distance_traveled" else "image_answer"
    return {
        "problem": problem,
        "level": "Level 5",
        "type": "Precalculus",
        "solution": _solution_text(parameters, latent_family, mode, metrics),
        "mechanism_metadata": {
            "family": "parametric_path_retracing",
            "generator_version": GENERATOR_VERSION,
            "case_id": fingerprint,
            "problem_id": stable_problem_id(problem),
            "split": split,
            "origin": "generated_exact_oracle",
            "mode": mode,
            "representation": representation,
            "latent_family": latent_family,
            "polynomial_degree": _degree(latent_family),
            "internal_turning_points": len(metrics["critical_times"]),
            "parameters": parameters,
            "oracle_answer": metrics[answer_key],
            "geometric_trail_answer": metrics["image_answer"],
            "accumulated_distance_answer": metrics["travel_answer"],
            "retraces": metrics["retraces"],
        },
    }


def _source_row(
    source_data: Path, source_index: int, source_run_csv: Path | None
) -> Dict[str, Any]:
    rows = _load_jsonl(source_data)
    row = dict(rows[source_index])
    baseline_failure = None
    if source_run_csv:
        with source_run_csv.open(encoding="utf-8") as handle:
            for record in csv.DictReader(handle):
                if int(record["index"]) == source_index:
                    baseline_failure = {
                        "score": int(record["score"]),
                        "prediction": record["prediction"],
                        "extracted": record["extracted"],
                    }
                    break
        if baseline_failure is None:
            raise ValueError("source index absent from source baseline CSV")
    problem = str(row["problem"])
    row["mechanism_metadata"] = {
        "family": "parametric_path_retracing",
        "generator_version": GENERATOR_VERSION,
        "case_id": "natural_math_validate_91",
        "problem_id": stable_problem_id(problem),
        "split": "design",
        "origin": "natural_source_failure",
        "source_dataset": str(source_data),
        "source_index": source_index,
        "baseline_failure": baseline_failure,
        "mode": "retraced_trail",
    }
    return row


def generate_bundle(
    *,
    output_dir: str | Path,
    seed: int,
    design_count: int,
    gate_count: int,
    regression_count: int,
    source_data: str | Path,
    source_index: int = 91,
    source_run_csv: str | Path | None = None,
    experiment_role: str = "development",
    parent_pilot_batch_id: str = "",
    primary_candidate: str = "",
    confirmation_runs: int = 3,
    pilot_record_sha256: str = "",
    replaces_family_id: str = "",
    replacement_reason: str = "",
) -> Path:
    if min(design_count, gate_count, regression_count) < 2:
        raise ValueError("every v5 split must contain at least two rows")
    if experiment_role not in {"development", "confirmatory"}:
        raise ValueError("experiment_role must be development or confirmatory")
    if experiment_role == "confirmatory" and not (
        parent_pilot_batch_id and primary_candidate and pilot_record_sha256
    ):
        raise ValueError("confirmatory v5 bundles require frozen pilot provenance")
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    output.mkdir(parents=True)

    source_path = Path(source_data).resolve()
    source_csv = Path(source_run_csv).resolve() if source_run_csv else None
    used: set[str] = set()
    design = [_source_row(source_path, source_index, source_csv)]
    for ordinal in range(design_count - 1):
        mode = "monotone_trail" if ordinal % 4 == 3 else "multi_retraced_trail"
        design.append(
            build_generated_row(
                seed=seed, split="design", ordinal=ordinal, mode=mode, used=used
            )
        )
    gate = []
    for ordinal in range(gate_count):
        mode = "monotone_trail" if ordinal % 3 == 2 else "multi_retraced_trail"
        gate.append(
            build_generated_row(
                seed=seed, split="gate", ordinal=ordinal, mode=mode, used=used
            )
        )
    regression = [
        build_generated_row(
            seed=seed,
            split="regression",
            ordinal=ordinal,
            mode="distance_traveled",
            used=used,
        )
        for ordinal in range(regression_count)
    ]

    split_rows = {"design": design, "gate": gate, "regression": regression}
    split_manifest = {}
    for split, rows in split_rows.items():
        path = output / f"{split}.jsonl"
        _write_jsonl(path, rows)
        split_manifest[split] = {
            "path": path.name,
            "sha256": file_sha256(path),
            "count": len(rows),
            "visible_to_operator_generator": split == "design",
        }

    family_id = hashlib.sha256(
        (
            f"{GENERATOR_VERSION}:{seed}:{design_count}:{gate_count}:"
            f"{regression_count}:{experiment_role}:{parent_pilot_batch_id}:"
            f"{primary_candidate}:{confirmation_runs}:{pilot_record_sha256}:"
            f"{replaces_family_id}"
        ).encode()
    ).hexdigest()[:16]
    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "family_id": family_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "experiment_role": experiment_role,
        "mechanism": {
            "name": "higher_order_parametric_path_retracing",
            "capability_gap": (
                "Recover the range and total variation of a hidden shared cubic or "
                "quartic parameter with multiple reversals, then distinguish the "
                "geometric trace from accumulated distance."
            ),
            "source_index": source_index,
            "source_problem_id": stable_problem_id(design[0]["problem"]),
        },
        "difficulty_contract": {
            "latent_polynomial_degrees": [3, 4],
            "latent_families": list(LATENT_FAMILIES),
            "retraced_internal_turning_points": [2, 3],
            "rational_hidden_shifts": True,
            "representations": ["dual_expanded", "mixed_latent"],
            "v4_shared_quadratic_cases": False,
        },
        "split_policy": {
            "design": "Visible mechanism examples only; never used as an evaluation split.",
            "gate": (
                "Hidden higher-order capability targets containing multi-retraced "
                "and monotone geometric-trace requests."
            ),
            "regression": (
                "Hidden hard boundary containing higher-order accumulated-distance requests only."
            ),
        },
        "splits": split_manifest,
    }
    if experiment_role == "confirmatory":
        manifest["confirmation_protocol"] = {
            "parent_pilot_batch_id": parent_pilot_batch_id,
            "pilot_record_sha256": pilot_record_sha256,
            "primary_candidate": primary_candidate,
            "candidate_selection_frozen_before_confirmation": True,
            "num_runs": confirmation_runs,
            "promotion_allowed": False,
            "gate_target_modes": ["multi_retraced_trail", "monotone_trail"],
            "hard_regression_modes": ["distance_traveled"],
            "replaces_family_id": replaces_family_id,
            "replacement_reason": replacement_reason,
        }
    manifest_path = output / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the harder v5 trajectory family.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=93031)
    parser.add_argument("--design-count", type=int, default=12)
    parser.add_argument("--gate-count", type=int, default=18)
    parser.add_argument("--regression-count", type=int, default=10)
    parser.add_argument("--source-data", default="data/datasets/math_validate.jsonl")
    parser.add_argument("--source-index", type=int, default=91)
    parser.add_argument("--source-run-csv", default="")
    parser.add_argument(
        "--experiment-role", choices=("development", "confirmatory"), default="development"
    )
    parser.add_argument("--parent-pilot-batch-id", default="")
    parser.add_argument("--primary-candidate", default="")
    parser.add_argument("--confirmation-runs", type=int, default=3)
    parser.add_argument("--pilot-record-sha256", default="")
    parser.add_argument("--replaces-family-id", default="")
    parser.add_argument("--replacement-reason", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = generate_bundle(
        output_dir=args.output_dir,
        seed=args.seed,
        design_count=args.design_count,
        gate_count=args.gate_count,
        regression_count=args.regression_count,
        source_data=args.source_data,
        source_index=args.source_index,
        source_run_csv=args.source_run_csv or None,
        experiment_role=args.experiment_role,
        parent_pilot_batch_id=args.parent_pilot_batch_id,
        primary_candidate=args.primary_candidate,
        confirmation_runs=args.confirmation_runs,
        pilot_record_sha256=args.pilot_record_sha256,
        replaces_family_id=args.replaces_family_id,
        replacement_reason=args.replacement_reason,
    )
    print(f"Failure-family manifest: {path}")
    print("Visible to operator generation: design only")
    print("Hidden tournament target: gate")
    print("Hidden safety checks: regression")


if __name__ == "__main__":
    main()
