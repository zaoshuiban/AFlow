"""Evidence-bound semantic reviews. No model calls or caller-supplied truth flags."""
from __future__ import annotations

import ast
import re
from collections import Counter
from datetime import datetime
from fractions import Fraction

from scripts.failure_mechanism_induction import validate_response
from scripts.mechanism_scope import validate_scope
from scripts.offline_loop_protocol import digest

EXACT_CLAIM = "The final rational answer differs from the exact value of the explicitly given arithmetic expression."
VERIFIERS = {"none", "explicit_exact_arithmetic_v1"}
HYPOTHESIS_FIELDS = {"claim", "violated_constraint", "expected_intervention", "applicable_when",
                     "not_applicable_when", "success_criteria"}


def validate_protocol(protocol):
    required = {"scope", "hypothesis", "program_verifier"}
    if set(protocol) not in (required, required | {"control_scope", "sampling_policy"}):
        raise ValueError("Protocol requires scope, hypothesis and program_verifier")
    validate_scope(protocol["scope"])
    if "selector" in protocol["scope"]:
        if protocol["scope"]["selector"]["kind"] != "decimal_reasoning_v1" or "control_scope" not in protocol:
            raise ValueError("Structured targets require an explicit boundary scope and sampling policy")
    if "control_scope" in protocol:
        validate_scope(protocol["control_scope"])
        if protocol["control_scope"].get("selector", {}).get("kind") != "decimal_boundary_v1":
            raise ValueError("Expected structured boundary selector")
        if protocol["scope"].get("selector", {}).get("kind") != "decimal_reasoning_v1":
            raise ValueError("Boundary quotas require structured targets")
        if protocol["scope"]["mechanism_id"] != protocol["control_scope"]["mechanism_id"]:
            raise ValueError("Target and control mechanism IDs must agree")
        policy = protocol["sampling_policy"]
        if not isinstance(policy, dict) or set(policy) != {"one_per_template_family", "control_counts"} or policy["one_per_template_family"] is not True:
            raise ValueError("Structured sampling requires one_per_template_family=true and control_counts")
        counts = policy["control_counts"]
        if not isinstance(counts, dict) or set(counts) != set(protocol["control_scope"]["selector"]["boundary_types"]) or any(type(v) is not int or v <= 0 for v in counts.values()):
            raise ValueError("Each declared boundary type requires a positive quota")
    hypothesis = protocol["hypothesis"]
    if set(hypothesis) != HYPOTHESIS_FIELDS or any(
        not isinstance(v, str) or not v.strip() for v in hypothesis.values()
    ):
        raise ValueError("Every hypothesis field must be a nonempty string")
    if protocol["program_verifier"] not in VERIFIERS:
        raise ValueError("Unknown program verifier")
    if protocol["program_verifier"] != "none" and (
        protocol["scope"]["mechanism_id"] != "exact_arithmetic_mismatch"
        or hypothesis["claim"] != EXACT_CLAIM
    ):
        raise ValueError("Exact arithmetic verifier only supports its canonical mechanism claim")


def rational_expression(text):
    """Bounded arithmetic only: no eval, names, powers, calls or symbolic parsing."""
    if not isinstance(text, str) or not text.strip() or len(text) > 240:
        raise ValueError("Unsupported arithmetic length")
    if not re.fullmatch(r"[0-9.()+*/\- \t]+", text) or "//" in text or "**" in text:
        raise ValueError("Unsupported arithmetic syntax")
    tree = ast.parse(text.strip(), mode="eval")
    if len(list(ast.walk(tree))) > 100:
        raise ValueError("Arithmetic expression too large")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = Fraction(ast.get_source_segment(text.strip(), node))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): value = left + right
            elif isinstance(node.op, ast.Sub): value = left - right
            elif isinstance(node.op, ast.Mult): value = left * right
            else: value = left / right
        else:
            raise ValueError("Unsupported arithmetic node")
        if max(value.numerator.bit_length(), value.denominator.bit_length()) > 4096:
            raise ValueError("Arithmetic result too large")
        return value

    return visit(tree.body)


def boxed_rational(text):
    # Require one unambiguous final boxed scalar. Do not search for a convenient
    # number in prose or silently use a partial mathematical expression.
    boxes = re.findall(r"\\boxed\{(\\frac\{-?\d+\}\{\d+\}|-?\d+(?:\.\d+)?(?:/\d+)?)\}", text)
    if len(boxes) != 1 or text.count("\\boxed") != 1:
        raise ValueError("Expected one boxed rational")
    value = boxes[0]
    fraction = re.fullmatch(r"\\frac\{(-?\d+)\}\{(\d+)\}", value)
    return Fraction(int(fraction[1]), int(fraction[2])) if fraction else Fraction(value)


def program_check(protocol, case, analysis):
    result = {"verifier": protocol["program_verifier"], "status": "not_covered"}
    if result["verifier"] == "none":
        return result
    problem = re.fullmatch(r"Compute exactly:\s*(.*?)\s*\.", case["problem"], re.S)
    if not problem or analysis["mechanism_id"] != "exact_arithmetic_mismatch":
        return result
    try:
        expected = rational_expression(problem[1])
        reference = boxed_rational(case["reference_solution"])
        run = next(r for r in case["failed_runs"] if r["run"] == analysis["run"])
        predicted = boxed_rational(run["prediction"])
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return result
    result["witness"] = {"expression": problem[1], "computed_exact": str(expected),
                         "reference": str(reference), "predicted": str(predicted),
                         "verified_claim": EXACT_CLAIM,
                         "case_sha256": digest(case), "analysis_sha256": digest(analysis)}
    if reference != expected or predicted == expected:
        result.update(status="contradiction", reason="Reference is incorrect or saved answer is exact")
    else:
        result.update(status="program_verified",
                      limitation="Verifies only the canonical final-value mismatch, not the free-text explanation or causality")
    return result


def review_subject(manifest, observations, packet, diagnostic):
    return digest({"manifest": digest(manifest), "observations": digest(observations),
                   "packet": digest(packet), "diagnostic": digest(diagnostic)})


def review_template(subject, checked):
    def pending():
        return {"decision": "pending", "reviewer": "", "reviewed_at": "", "rationale": "",
                "reference_check": "", "boundary_check": ""}
    return {"schema_version": 1, "subject_sha256": subject, "scope_review": pending(),
            "analyses": [{"problem_id": a["problem_id"], "run": a["run"],
                          "analysis_sha256": digest(a), **pending()}
                         for a in checked["accepted_analyses"]]}


def validate_attestation(record):
    if record.get("decision") not in {"pending", "confirm", "reject"}:
        raise ValueError("Invalid human review decision")
    if record["decision"] == "pending":
        return
    for field in ("reviewer", "reviewed_at", "rationale", "reference_check", "boundary_check"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"Human review requires {field}")
    date = datetime.fromisoformat(record["reviewed_at"].replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("Human review time requires timezone")


def evaluate_review(manifest, observations, packet, diagnostic, review=None):
    """Recompute references, checks and statistics; never trust imported flags."""
    protocol, config = manifest["protocol"], manifest["config"]
    validate_protocol(protocol)
    subject = review_subject(manifest, observations, packet, diagnostic)
    checked = validate_response(packet, diagnostic["response"], config["min_stable_problems"])
    accepted = {(a["problem_id"], a["run"]): a for a in checked["accepted_analyses"]}
    attestations = {}
    if review is not None:
        if review.get("schema_version") != 1 or review.get("subject_sha256") != subject:
            raise ValueError("Review is stale or bound to different evidence")
        validate_attestation(review["scope_review"])
        for entry in review["analyses"]:
            key = (entry["problem_id"], entry["run"])
            if key in attestations or key not in accepted or entry.get("analysis_sha256") != digest(accepted[key]):
                raise ValueError("Review analysis changed, duplicated or unknown")
            validate_attestation(entry)
            attestations[key] = entry
    scope = protocol["scope"]
    rows = manifest["sample_index"]
    target_ids = {r["problem_id"] for r in rows if r["role"] == "target"}
    expected_keys = {(r["problem_id"], n) for r in rows for n in range(1, config["discovery_runs"] + 1)}
    if len(observations) != len(expected_keys) or {(o["problem_id"], o["run"]) for o in observations} != expected_keys:
        raise ValueError("Incomplete or duplicate baseline observations")
    for o in observations:
        if type(o["run"]) is not int or (not o.get("error") and type(o["score"]) is not int) or o["score"] not in (0, 1):
            raise ValueError("Invalid baseline observation")
    valid = [o for o in observations if o["problem_id"] in target_ids and not o.get("error")]
    wrong = [o for o in valid if o["score"] == 0]
    wrong_counts = Counter(o["problem_id"] for o in wrong)
    cases = {c["problem_id"]: c for c in packet["cases"]}
    observed = {(o["problem_id"], o["run"]): o for o in observations}
    if len(cases) != len(packet["cases"]):
        raise ValueError("Duplicate diagnostic case")
    for case in cases.values():
        for failed in case["failed_runs"]:
            item = observed.get((case["problem_id"], failed["run"]))
            if not item or item.get("error") or item["score"] != 0 or item["prediction"] != failed["prediction"]:
                raise ValueError("Diagnostic evidence differs from baseline observation")
    details, supported = [], Counter()
    refuted = review is not None and review["scope_review"]["decision"] == "reject"
    for key, analysis in accepted.items():
        proof = program_check(protocol, cases[key[0]], analysis)
        attestation = attestations.get(key)
        relevant = analysis["mechanism_id"] == scope["mechanism_id"]
        contradiction = analysis["constraint_review"]["status"] == "contradiction" or proof["status"] == "contradiction"
        rejected = attestation is not None and attestation["decision"] == "reject"
        human = attestation is not None and attestation["decision"] == "confirm"
        status = ("contradicted" if contradiction else "human_rejected" if rejected else
                  "program_verified" if proof["status"] == "program_verified" else
                  "human_confirmed" if human else "reference_valid")
        # A contradictory target-mechanism explanation on a control is also a
        # counterexample; it cannot be hidden by counting only target rows.
        if relevant and (contradiction or rejected):
            refuted = True
        if relevant and key[0] in target_ids and status in {"human_confirmed", "program_verified"}:
            supported[key[0]] += 1
        details.append({"problem_id": key[0], "run": key[1], "mechanism_id": analysis["mechanism_id"],
                        "analysis_sha256": digest(analysis), "reference_valid": True,
                        "status": status, "program_check": proof, "human_review": attestation,
                        "constraint_review": analysis["constraint_review"]})
    stable = sorted(pid for pid, n in wrong_counts.items() if n >= config["min_wrong_repeats"])
    semantic = sorted(pid for pid, n in supported.items() if n >= config["min_wrong_repeats"])
    rate = len(wrong) / len(valid) if valid else None
    statistical = len(stable) >= config["min_stable_problems"] and rate is not None and rate >= config["min_error_rate"]
    # Whole hypothesis/boundary approval remains human: a numeric witness only
    # proves its canonical local claim, never an intervention or routing domain.
    scope_confirmed = review is not None and review["scope_review"]["decision"] == "confirm"
    eligible = not refuted and statistical and scope_confirmed and len(semantic) >= config["min_stable_problems"]
    return {"schema_version": 1, "subject_sha256": subject, "review_sha256": digest(review),
            "status": "refuted" if refuted else "generation_authorized" if eligible else "insufficient_evidence",
            "eligible_for_generation": eligible, "promotion_authorized": False,
            "scope_confirmed": scope_confirmed, "target_valid_observations": len(valid),
            "target_wrong_observations": len(wrong), "observed_error_rate": rate,
            "execution_errors": sum(bool(o.get("error")) for o in observations),
            "stable_problem_ids": stable, "semantically_supported_problem_ids": semantic,
            "statistical_threshold_met": statistical, "analyses": details,
            "excluded_analyses": checked["excluded_analyses"],
            "status_counts": dict(Counter(a["status"] for a in details)),
            "human_review_limit": "Named user-supplied attestation; identity and mathematical correctness are not authenticated"}
