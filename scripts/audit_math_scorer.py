"""Local dependency and positive/negative-control checks. Never calls an LLM."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.math import MATHBenchmark
from scripts.offline_loop_protocol import atomic_json, runtime_environment


PROBES = [
    ("fraction_decimal", r"\frac{3}{4}", "0.75", 1),
    ("wrong_fraction", r"\frac{3}{4}", r"\frac{4}{3}", 0),
    ("dfrac", r"\dfrac{39}{64}", r"\frac{39}{64}", 1),
    ("nested_radical", r"5\sqrt{\frac{2}{3}}", r"\frac{5\sqrt{6}}{3}", 1),
    ("unbraced_root", r"121\sqrt{2}", r"121\sqrt2", 1),
    ("wrong_root", r"\sqrt{2}", "1.4", 0),
    ("cube_root", r"\sqrt[3]{8}", "2", 1),
    ("fourth_root", r"\sqrt[4]{16}", "2", 1),
    ("trigonometry", r"\sin\left(\frac{\pi}{6}\right)", r"\frac{1}{2}", 1),
    ("wrong_trigonometry", r"\sin\left(\frac{\pi}{6}\right)", r"\frac{\sqrt{3}}{2}", 0),
    ("logarithm", r"\log_{2}8", "3", 1),
    ("binomial", r"\binom{5}{2}", "10", 1),
    ("pi", r"2\pi", r"\pi+\pi", 1),
    ("polynomial", r"(x+1)^2", "x^2+2*x+1", 1),
    ("wrong_polynomial", r"(x+1)^2", "x^2+1", 0),
    ("factorial", r"\frac{15!}{4}", "326918592000", 1),
    ("thousands_separator", "2875125", r"2{,}875{,}125", 1),
    ("equality_chain", "326918592000", r"\frac{15!}{4}=326{,}918{,}592{,}000", 1),
    ("wrong_equality_chain", "1307674368000", "15!=42", 0),
    ("units", "100", r"100\text{ square units}", 1),
    ("degrees", "135", r"135^\circ", 1),
    ("text", r"\text{MAKE}", "MAKE", 1),
    ("wrong_text", r"\text{MAKE}", "MADE", 0),
    ("tuple_order", "(1,2)", "(2,1)", 0),
    ("matrix", r"\begin{pmatrix}\frac{1}{5}\\-\frac{18}{5}\end{pmatrix}",
     r"\begin{pmatrix}1/5\\-18/5\end{pmatrix}", 1),
    ("matrix_order", r"\begin{pmatrix}1\\2\end{pmatrix}", r"\begin{pmatrix}2\\1\end{pmatrix}", 0),
    ("empty_prediction", "17", "", 0),
    ("malformed_partial_expression", "1", "1+", 0),
]

CONTEXT_PROBES = [
    ("choice_letter_maps", "2", "A", r"Pick: \text{(A) } 2\quad\text{(B) } 3", 1),
    ("wrong_choice_letter", "2", "B", r"Pick: \text{(A) } 2\quad\text{(B) } 3", 0),
    ("incomplete_choice_block", "2", "A", "Only (A) 2 is present.", 0),
    ("plain_text_case", "orange", "Orange", "Name the color.", 1),
    ("single_variable_case_A", "a", "A", "Solve for the variable.", 0),
    ("single_variable_case_x", "x", "X", "Name the variable.", 0),
]


def dependency_snapshot() -> dict:
    return {**runtime_environment(),
            "scorer_sha256": hashlib.sha256(Path(__file__).resolve().parents[1].joinpath("benchmarks/math.py").read_bytes()).hexdigest()}


def audit() -> dict:
    scorer = MATHBenchmark("MATH", "", "")
    checks = []
    for name, reference, prediction, expected in PROBES:
        try:
            score, _ = scorer.calculate_score(r"\boxed{" + reference + "}",
                                               r"\boxed{" + prediction + "}" if prediction else "")
            checks.append({"name": name, "reference": reference, "prediction": prediction,
                           "expected": expected, "actual": score, "passed": score == expected})
        except Exception as exc:
            checks.append({"name": name, "expected": expected, "actual": None,
                           "passed": False, "error": type(exc).__name__ + ": " + str(exc)})
    for name, reference, prediction, problem, expected in CONTEXT_PROBES:
        try:
            score, _ = scorer.calculate_score(
                r"\boxed{" + reference + "}", r"\boxed{" + prediction + "}", problem=problem
            )
            checks.append({"name": name, "reference": reference, "prediction": prediction,
                           "problem": problem, "expected": expected, "actual": score,
                           "passed": score == expected})
        except Exception as exc:
            checks.append({"name": name, "expected": expected, "actual": None,
                           "passed": False, "error": type(exc).__name__ + ": " + str(exc)})
    snapshot = dependency_snapshot()
    dependency_ok = (snapshot["packages"]["sympy"] == "1.14.0"
                     and str(snapshot["packages"]["antlr4-python3-runtime"]).startswith("4.11."))
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(), **snapshot,
            "dependency_ok": dependency_ok, "checks": checks,
            "passed_checks": sum(check["passed"] for check in checks), "total_checks": len(checks),
            "passed": dependency_ok and all(check["passed"] for check in checks),
            "scope": "hand-verified positive/negative controls; not a complete scorer correctness proof"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Report exists; choose a new output name to preserve the audit trail")
    report = audit()
    atomic_json(args.output, report)
    print(json.dumps({key: report[key] for key in ("scorer_version", "packages", "dependency_ok", "passed_checks", "total_checks", "passed")}, indent=2))
    for check in report["checks"]:
        if not check["passed"]:
            print(f"FAIL {check['name']}: expected={check['expected']}, actual={check['actual']}")
    print(f"Report: {args.output.resolve()}")
    raise SystemExit(0 if report["passed"] else 2)


if __name__ == "__main__":
    main()
