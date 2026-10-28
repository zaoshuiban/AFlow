# -*- coding: utf-8 -*-
"""Build a deterministic, oracle-checked family around MATH index 91.

The family distinguishes the geometric image of a retraced parametric path
from accumulated travel distance.  Only the design split is intended to be
visible to operator diagnosis/design.  Gate and regression rows are loaded by
the tournament after candidate artifacts have been frozen.
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
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from scripts.operator_library import stable_problem_id


SCHEMA_VERSION = 1
GENERATOR_VERSION = "trajectory_retrace_v4"
SUPPORTED_GENERATOR_VERSIONS = {
    "trajectory_retrace_v3",
    GENERATOR_VERSION,
    "trajectory_retrace_v5",
    "baseline_headroom_mechanisms_v2",
    "baseline_headroom_mechanisms_v2_1",
    "baseline_headroom_mechanisms_v2_2",
}
SPLITS = ("design", "gate", "regression")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _largest_square_factor(value: int) -> tuple[int, int]:
    outside = 1
    remaining = value
    factor = 2
    while factor * factor <= remaining:
        exponent = 0
        while remaining % factor == 0:
            remaining //= factor
            exponent += 1
        outside *= factor ** (exponent // 2)
        factor += 1
    squarefree = value // (outside * outside)
    return outside, squarefree


def _fraction_latex(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return rf"\frac{{{value.numerator}}}{{{value.denominator}}}"


def radical_latex(coefficient: Fraction, radicand: int) -> str:
    """Return a canonical exact LaTeX representation of c*sqrt(n)."""
    if coefficient < 0:
        return "-" + radical_latex(-coefficient, radicand)
    outside, squarefree = _largest_square_factor(radicand)
    coefficient *= outside
    if squarefree == 1:
        return _fraction_latex(coefficient)
    numerator = coefficient.numerator
    denominator = coefficient.denominator
    radical = rf"\sqrt{{{squarefree}}}"
    if denominator == 1:
        return radical if numerator == 1 else f"{numerator}{radical}"
    numerator_text = radical if numerator == 1 else f"{numerator}{radical}"
    return rf"\frac{{{numerator_text}}}{{{denominator}}}"


def _vertex(parameters: Mapping[str, Any]) -> Fraction:
    if "vertex_num" in parameters:
        return Fraction(parameters["vertex_num"], parameters["vertex_den"])
    return Fraction(parameters["vertex"])


def _quadratic_core(vertex: Fraction | int) -> str:
    vertex = Fraction(vertex)
    if vertex == 0:
        return "t^2"
    if vertex > 0:
        return rf"(t-{_fraction_latex(vertex)})^2"
    return rf"(t+{_fraction_latex(-vertex)})^2"


def _signed_term(offset: int, coefficient: int, vertex: Fraction | int) -> str:
    core = _quadratic_core(vertex)
    if coefficient == 1:
        variable = core
    elif coefficient == -1:
        variable = "-" + core
    else:
        variable = f"{coefficient}{core}"
    if offset == 0:
        return variable
    sign = "+" if offset > 0 else "-"
    return f"{variable}{sign}{abs(offset)}"


def _polynomial_term(coefficient: int, symbol: str, *, first: bool) -> str:
    if coefficient == 0:
        return ""
    sign = "-" if coefficient < 0 else ("" if first else "+")
    magnitude = abs(coefficient)
    numeric = "" if magnitude == 1 and symbol else str(magnitude)
    return f"{sign}{numeric}{symbol}"


def _expanded_term(
    offset: int, coefficient: int, vertex: Fraction | int
) -> str:
    vertex = Fraction(vertex)
    quadratic = coefficient
    linear = -2 * coefficient * vertex
    constant = coefficient * vertex * vertex + offset
    if linear.denominator != 1 or constant.denominator != 1:
        raise ValueError("expanded coefficients must be integral")
    pieces = []
    for value, symbol in (
        (quadratic, "t^2"),
        (int(linear), "t"),
        (int(constant), ""),
    ):
        piece = _polynomial_term(value, symbol, first=not pieces)
        if piece:
            pieces.append(piece)
    return "".join(pieces) or "0"


def _case_rng(seed: int, split: str) -> random.Random:
    digest = hashlib.sha256(f"{GENERATOR_VERSION}:{seed}:{split}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _parameters(
    rng: random.Random, mode: str, *, rational_vertex: bool
) -> Dict[str, int]:
    direction_choices = [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 3),
        (3, 2),
        (3, 4),
        (4, 3),
        (1, 3),
    ]
    a, b = rng.choice(direction_choices)
    if rng.random() < 0.35:
        a = -a
    if rng.random() < 0.35:
        b = -b
    if rational_vertex:
        vertex_den = rng.choice((2, 3))
        choices = [
            value
            for value in range(-7, 8)
            if value % vertex_den != 0
        ]
        vertex_num = rng.choice(choices)
    else:
        vertex_den = 1
        vertex_num = rng.randint(-3, 3)
    vertex = Fraction(vertex_num, vertex_den)
    # A denominator-squared scale keeps expanded polynomial coefficients
    # integral even when the hidden turning point is rational.
    scale = rng.randint(1, 2) * vertex_den * vertex_den
    x_offset = rng.randint(-8, 8)
    y_offset = rng.randint(-8, 8)

    if mode in {"retraced_trail", "distance_traveled"}:
        left_span = rng.randint(1, 5)
        right_span = rng.randint(1, 5)
        if left_span == right_span:
            right_span = 1 + right_span % 5
        start = math.floor(vertex) - left_span
        end = math.ceil(vertex) + right_span
    elif mode == "monotone_trail":
        width = rng.randint(2, 6)
        gap = rng.randint(1, 4)
        if rng.random() < 0.5:
            start = math.ceil(vertex) + gap
            end = start + width
        else:
            end = math.floor(vertex) - gap
            start = end - width
    else:
        raise ValueError(f"unknown trajectory mode: {mode}")

    return {
        "a": a,
        "b": b,
        "scale": scale,
        "vertex_num": vertex_num,
        "vertex_den": vertex_den,
        "start": start,
        "end": end,
        "x_offset": x_offset,
        "y_offset": y_offset,
    }


def _metric_values(parameters: Mapping[str, int]) -> Dict[str, Any]:
    start = Fraction(parameters["start"])
    end = Fraction(parameters["end"])
    vertex = _vertex(parameters)
    scale = Fraction(abs(parameters["scale"]))
    left_value = (start - vertex) ** 2
    right_value = (end - vertex) ** 2
    if start <= vertex <= end:
        image_delta = scale * max(left_value, right_value)
        travel_delta = scale * (left_value + right_value)
        retraces = True
    else:
        image_delta = scale * abs(right_value - left_value)
        travel_delta = image_delta
        retraces = False
    norm_squared = parameters["a"] ** 2 + parameters["b"] ** 2
    return {
        "image_delta": image_delta,
        "travel_delta": travel_delta,
        "norm_squared": norm_squared,
        "image_answer": radical_latex(image_delta, norm_squared),
        "travel_answer": radical_latex(travel_delta, norm_squared),
        "retraces": retraces,
    }


def _representation(split: str, ordinal: int) -> str:
    schedules = {
        "design": ("expanded", "mixed", "factored_rational", "mixed"),
        "gate": ("mixed", "expanded", "factored_rational", "expanded"),
        "regression": ("expanded", "mixed", "factored_rational"),
    }
    return schedules[split][ordinal % len(schedules[split])]


def _problem_text(
    parameters: Mapping[str, int],
    mode: str,
    variant: int,
    representation: str,
) -> str:
    x_coefficient = parameters["a"] * parameters["scale"]
    y_coefficient = parameters["b"] * parameters["scale"]
    vertex = _vertex(parameters)
    if representation == "expanded":
        x_expr = _expanded_term(parameters["x_offset"], x_coefficient, vertex)
        y_expr = _expanded_term(parameters["y_offset"], y_coefficient, vertex)
    elif representation == "mixed":
        x_expr = _expanded_term(parameters["x_offset"], x_coefficient, vertex)
        y_expr = _signed_term(parameters["y_offset"], y_coefficient, vertex)
    elif representation == "factored_rational":
        x_expr = _signed_term(parameters["x_offset"], x_coefficient, vertex)
        y_expr = _signed_term(parameters["y_offset"], y_coefficient, vertex)
    else:
        raise ValueError(f"unknown representation: {representation}")
    equation = rf"(x,y)=({x_expr},{y_expr})"
    interval = rf"{parameters['start']}\le t\le {parameters['end']}"
    if mode == "distance_traveled":
        prompts = (
            "A robot moves in the coordinate plane according to "
            rf"\[{equation}\] for ${interval}$. Find the total distance traveled by the robot.",
            "A particle has position "
            rf"\[{equation}\] for ${interval}$. Compute the total distance it travels, including portions traversed more than once.",
        )
    else:
        prompts = (
            "A marker moves on the coordinate plane and leaves ink wherever it passes. "
            rf"Its position is \[{equation}\] for ${interval}$. Find the length of the ink trail left on the plane.",
            "A point of light traces a curve in the coordinate plane with position "
            rf"\[{equation}\] for ${interval}$. Find the length of the geometric curve visible after the entire motion.",
            "A paint-covered ball moves so that its position at time $t$ is "
            rf"\[{equation}\] where ${interval}$. Find the length of the paint trail it leaves.",
        )
    return prompts[variant % len(prompts)]


def _solution_text(
    parameters: Mapping[str, int], mode: str, metrics: Mapping[str, Any]
) -> str:
    vertex = _vertex(parameters)
    start = parameters["start"]
    end = parameters["end"]
    scale = abs(parameters["scale"])
    norm_squared = metrics["norm_squared"]
    parameter = _quadratic_core(vertex)
    scaled_parameter = "u" if scale == 1 else f"{scale}u"
    if mode == "distance_traveled":
        explanation = (
            rf"Let $u={parameter}$. The motion reverses at $t={_fraction_latex(vertex)}$. "
            rf"The total variation of ${scaled_parameter}$ is "
            rf"${_fraction_latex(metrics['travel_delta'])}$. "
            "Because the direction vector has length "
            rf"$\sqrt{{{norm_squared}}}$, repeated portions must be counted again."
        )
        answer = metrics["travel_answer"]
    elif metrics["retraces"]:
        explanation = (
            rf"Let $u={parameter}$. Both coordinates are affine functions of $u$, "
            "so the geometric image is a line segment. Although the point reverses "
            rf"direction at $t={_fraction_latex(vertex)}$, overwritten portions of the trail form the same segment. "
            rf"For ${start}\le t\le {end}$, the range length of ${scaled_parameter}$ is "
            rf"${_fraction_latex(metrics['image_delta'])}$. Multiplying by the length "
            rf"$\sqrt{{{norm_squared}}}$ of the direction vector gives the trail length."
        )
        answer = metrics["image_answer"]
    else:
        explanation = (
            rf"Let $u={parameter}$. On ${start}\le t\le {end}$ this parameter is "
            "monotone, so the curve does not retrace. The range length of the scaled "
            rf"parameter is ${_fraction_latex(metrics['image_delta'])}$, and the "
            rf"direction vector has length $\sqrt{{{norm_squared}}}$."
        )
        answer = metrics["image_answer"]
    return explanation + rf" Therefore the answer is $\boxed{{{answer}}}$."


def build_generated_row(
    *, seed: int, split: str, ordinal: int, mode: str, used: set[str]
) -> Dict[str, Any]:
    rng = _case_rng(seed + ordinal * 1009, f"{split}:{mode}")
    representation = _representation(split, ordinal)
    rational_vertex = representation == "factored_rational" or ordinal % 2 == 0
    for _ in range(100):
        parameters = _parameters(
            rng, mode, rational_vertex=rational_vertex
        )
        fingerprint = _json_sha256({"mode": mode, "parameters": parameters})[:16]
        if fingerprint not in used:
            used.add(fingerprint)
            break
    else:
        raise RuntimeError("could not generate a unique trajectory case")

    metrics = _metric_values(parameters)
    problem = _problem_text(parameters, mode, ordinal, representation)
    answer_key = "travel_answer" if mode == "distance_traveled" else "image_answer"
    return {
        "problem": problem,
        "level": "Level 5",
        "type": "Precalculus",
        "solution": _solution_text(parameters, mode, metrics),
        "mechanism_metadata": {
            "family": "parametric_path_retracing",
            "generator_version": GENERATOR_VERSION,
            "case_id": fingerprint,
            "problem_id": stable_problem_id(problem),
            "split": split,
            "origin": "generated_exact_oracle",
            "mode": mode,
            "representation": representation,
            "parameters": parameters,
            "oracle_answer": metrics[answer_key],
            "geometric_trail_answer": metrics["image_answer"],
            "accumulated_distance_answer": metrics["travel_answer"],
            "retraces": metrics["retraces"],
        },
    }


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"row {line_number} is not an object: {path}")
            rows.append(value)
    return rows


def _source_row(
    source_data: Path,
    source_index: int,
    source_run_csv: Path | None,
) -> Dict[str, Any]:
    rows = _load_jsonl(source_data)
    if source_index < 0 or source_index >= len(rows):
        raise IndexError("source index outside source dataset")
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


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


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
) -> Path:
    if design_count < 2:
        raise ValueError("design_count must be at least 2 (source plus one variant)")
    if gate_count < 2:
        raise ValueError("gate_count must be at least 2")
    if regression_count < 2:
        raise ValueError("regression_count must be at least 2")
    if experiment_role not in {"development", "confirmatory"}:
        raise ValueError("experiment_role must be development or confirmatory")
    if experiment_role == "confirmatory":
        if not parent_pilot_batch_id or not primary_candidate or not pilot_record_sha256:
            raise ValueError(
                "confirmatory bundles require parent pilot batch, primary candidate, "
                "and pilot record hash"
            )
        if confirmation_runs <= 0:
            raise ValueError("confirmation_runs must be positive")
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    output.mkdir(parents=True)

    source_path = Path(source_data).resolve()
    source_csv_path = Path(source_run_csv).resolve() if source_run_csv else None
    used: set[str] = set()
    design = [_source_row(source_path, source_index, source_csv_path)]
    for ordinal in range(design_count - 1):
        mode = "monotone_trail" if ordinal % 4 == 3 else "retraced_trail"
        design.append(
            build_generated_row(
                seed=seed,
                split="design",
                ordinal=ordinal,
                mode=mode,
                used=used,
            )
        )
    gate = []
    for ordinal in range(gate_count):
        mode = "monotone_trail" if ordinal % 3 == 2 else "retraced_trail"
        gate.append(
            build_generated_row(
                seed=seed,
                split="gate",
                ordinal=ordinal,
                mode=mode,
                used=used,
            )
        )
    regression = []
    for ordinal in range(regression_count):
        regression.append(
            build_generated_row(
                seed=seed,
                split="regression",
                ordinal=ordinal,
                mode="distance_traveled",
                used=used,
            )
        )

    split_rows = {"design": design, "gate": gate, "regression": regression}
    split_manifest: Dict[str, Any] = {}
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
            f"{primary_candidate}:{confirmation_runs}:{pilot_record_sha256}"
        ).encode()
    ).hexdigest()[:16]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "family_id": family_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "experiment_role": experiment_role,
        "mechanism": {
            "name": "parametric_path_retracing",
            "capability_gap": (
                "Distinguish the length of the geometric image/trail from total "
                "distance traveled when a parametric path reverses and retraces."
            ),
            "source_index": source_index,
            "source_problem_id": stable_problem_id(design[0]["problem"]),
        },
        "split_policy": {
            "design": "Visible to diagnosis and candidate architecture only.",
            "gate": (
                "Hidden capability target: retraced and monotone geometric-trail requests."
            ),
            "regression": "Hidden hard boundary: accumulated total-distance requests only.",
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
            "gate_target_modes": ["retraced_trail", "monotone_trail"],
            "hard_regression_modes": ["distance_traveled"],
        }
    manifest_path = output / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return manifest_path


def load_bundle_manifest(path: str | Path) -> Dict[str, Any]:
    manifest_path = Path(path).resolve()
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported failure-family manifest schema")
    if manifest.get("generator_version") not in SUPPORTED_GENERATOR_VERSIONS:
        raise ValueError("unsupported trajectory generator version")
    splits = manifest.get("splits", {})
    if set(splits) != set(SPLITS):
        raise ValueError("manifest must define design, gate, and regression splits")
    if manifest.get("generator_version") in {
        "baseline_headroom_mechanisms_v2",
        "baseline_headroom_mechanisms_v2_1",
        "baseline_headroom_mechanisms_v2_2",
    }:
        headroom = manifest.get("headroom_gate", {})
        if headroom.get("status") != "passed":
            raise ValueError("v2 mechanism family has not passed baseline headroom gating")
        audit_path = Path(str(headroom.get("audited_summary", ""))).resolve()
        if not audit_path.is_file() or file_sha256(audit_path) != headroom.get(
            "audited_summary_sha256"
        ):
            raise ValueError("v2 headroom audit is missing or its hash changed")
        with audit_path.open(encoding="utf-8") as handle:
            audit = json.load(handle)
        if manifest.get("mechanism", {}).get("name") not in audit.get(
            "accepted_mechanisms", []
        ):
            raise ValueError("v2 mechanism is not accepted by its audited screening")
    manifest["_manifest_path"] = str(manifest_path)
    manifest["_manifest_sha256"] = file_sha256(manifest_path)
    return manifest


def load_bundle_split(
    manifest: Mapping[str, Any], split: str
) -> List[Dict[str, Any]]:
    if split not in SPLITS:
        raise ValueError(f"unknown split: {split}")
    manifest_path = Path(str(manifest["_manifest_path"]))
    spec = manifest["splits"][split]
    path = (manifest_path.parent / spec["path"]).resolve()
    if file_sha256(path) != spec["sha256"]:
        raise ValueError(f"{split} split hash does not match manifest")
    rows = _load_jsonl(path)
    if len(rows) != int(spec["count"]):
        raise ValueError(f"{split} split row count does not match manifest")
    for row in rows:
        metadata = row.get("mechanism_metadata", {})
        if metadata.get("split") != split:
            raise ValueError(f"row has incorrect split metadata: {split}")
    return rows


def ensure_disjoint_splits(split_rows: Mapping[str, Iterable[Mapping[str, Any]]]) -> None:
    seen: Dict[str, str] = {}
    for split, rows in split_rows.items():
        for row in rows:
            problem_id = stable_problem_id(str(row["problem"]))
            prior = seen.get(problem_id)
            if prior is not None:
                raise ValueError(
                    f"problem leakage between {prior} and {split}: {problem_id}"
                )
            seen[problem_id] = split


def render_visible_design_cases(rows: Sequence[Mapping[str, Any]]) -> str:
    sections = [
        "## Visibility contract\n"
        "Only these design examples are visible. Hidden gate and regression "
        "problems are not supplied to diagnosis or operator architecture."
    ]
    for number, row in enumerate(rows, 1):
        metadata = row.get("mechanism_metadata", {})
        baseline = metadata.get("baseline_failure")
        evidence = ""
        if baseline:
            evidence = (
                "\n**Observed baseline failure**: "
                f"{baseline.get('extracted', '')} (score={baseline.get('score')})"
            )
        elif metadata.get("mode") == "monotone_trail":
            evidence = (
                "\n**In-scope neighboring capability case**: the interval does "
                "not cross the turning point, so geometric trail length and "
                "accumulated distance coincide. The specialist must not invent "
                "a retraced segment."
            )
        elif metadata.get("accumulated_distance_answer"):
            evidence = (
                "\n**Mechanism counterexample**: accumulated travel would be "
                f"{metadata['accumulated_distance_answer']}, while the requested "
                f"geometric trail is {metadata['geometric_trail_answer']}."
            )
        sections.append(
            f"### Visible design case {number}\n"
            f"**Question**: {row['problem']}\n"
            f"**Oracle solution**: {row['solution']}"
            f"{evidence}\n"
            f"**Case ID**: {metadata.get('case_id', stable_problem_id(str(row['problem'])))}"
        )
    return "\n\n".join(sections)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an exact trajectory-retracing failure family."
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=92017)
    parser.add_argument("--design-count", type=int, default=12)
    parser.add_argument("--gate-count", type=int, default=12)
    parser.add_argument("--regression-count", type=int, default=8)
    parser.add_argument("--source-data", default="data/datasets/math_validate.jsonl")
    parser.add_argument("--source-index", type=int, default=91)
    parser.add_argument("--source-run-csv", default="")
    parser.add_argument(
        "--experiment-role",
        choices=("development", "confirmatory"),
        default="development",
    )
    parser.add_argument("--parent-pilot-batch-id", default="")
    parser.add_argument("--primary-candidate", default="")
    parser.add_argument("--confirmation-runs", type=int, default=3)
    parser.add_argument("--pilot-record-sha256", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = generate_bundle(
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
    )
    print(f"Failure-family manifest: {manifest}")
    print("Visible to operator generation: design only")
    print("Hidden tournament target: gate")
    print("Hidden safety checks: regression")


if __name__ == "__main__":
    main()
