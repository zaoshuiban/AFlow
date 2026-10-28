"""Versioned, question-only selectors shared by preparation and candidate guards.

Conservative English task recognizers, not a semantic parser. Never execute
Asymptote; diagrams and labels are excluded in v1. No answers or IDs are inputs.
"""
from __future__ import annotations

import re
from fractions import Fraction

from scripts.mechanism_reliability import matches as literal_matches
from scripts.mechanism_reliability import validate_scope as validate_literal_scope

FAMILIES = {"gas_proportion", "solution_mixture", "pole_similarity", "ladder_geometry",
            "equal_perimeters", "projectile_model"}
BOUNDARIES = {"given_approximation", "final_rounding"}
DECIMAL = re.compile(r"(?<![\w.])(?:\d+\.\d+|\.\d+)")
APPROX = re.compile(r"\b(?:approximat\w*|estimat\w*|closest|nearest|round(?:ed|ing)?|significant figures|measurement error)\b", re.I)
GIVEN_APPROX = re.compile(r"\b(?:approximately|approximate|estimated|measurement error)\b", re.I)
ROUNDING = re.compile(r"\b(?:nearest|round(?:ed|ing)?)\b", re.I)
OPTIONS = re.compile(r"\\(?:text|textbf|mathrm)\s*\{\s*\(?A\)?|(?m:^\s*A[.)]\s)|\(A\)")


def validate_scope(scope):
    if "selector" not in scope:
        return validate_literal_scope(scope)
    if set(scope) != {"mechanism_id", "selector"} or not isinstance(scope["mechanism_id"], str) or not scope["mechanism_id"].strip():
        raise ValueError("Structured scope requires mechanism_id and selector only")
    s = scope["selector"]
    if not isinstance(s, dict) or s.get("kind") not in {"decimal_reasoning_v1", "decimal_boundary_v1"}:
        raise ValueError("Unknown structured selector version")
    field, allowed = ("families", FAMILIES) if s["kind"] == "decimal_reasoning_v1" else ("boundary_types", BOUNDARIES)
    if set(s) != {"kind", "min_distinct_decimals", field}:
        raise ValueError("Unknown or missing structured selector field")
    if type(s["min_distinct_decimals"]) is not int or not 2 <= s["min_distinct_decimals"] <= 10:
        raise ValueError("Require 2..10 distinct decimal values")
    values = s[field]
    if not isinstance(values, list) or not values or any(not isinstance(v, str) or v not in allowed for v in values) or len(set(values)) != len(values):
        raise ValueError("Invalid selector families or boundary types")


def question_features(problem):
    # Blank source spans instead of concatenating text: token offsets refer to
    # the original question. Unsupported/malformed blocks fail closed.
    text = problem
    malformed = text.count("[asy]") != text.count("[/asy]")
    for pattern in (r"\[asy\].*?\[/asy\]", r"\\(?:hspace|vspace)\*?\{[^}]*\}"):
        text = re.sub(pattern, lambda m: " " * len(m[0]), text, flags=re.S)
    if "[asy]" in text or "[/asy]" in text:
        malformed = True
    # Examples/notes and answer choices cannot supply input values.
    cuts = [m.start() for pattern in (OPTIONS, re.compile(r"\b(?:For example|For instance|You should enter)\b|\bNote\s*:", re.I))
            if (m := pattern.search(text))]
    if cuts:
        text = text[:min(cuts)] + " " * (len(text) - min(cuts))
    tokens = [{"text": m[0], "start": m.start(), "end": m.end(), "source": "problem_prose"}
              for m in DECIMAL.finditer(text)]
    numbers = {Fraction(t["text"]) for t in tokens if len(t["text"]) <= 100}
    lower = text.lower()
    families = []
    if re.search(r"inversely proportional", lower) and all(w in lower for w in ("pressure", "volume")):
        families.append("gas_proportion")
    if re.search(r"mix(?:ing|ture)?", lower) and "solution" in lower and re.search(r"liters?|litres?", lower):
        families.append("solution_mixture")
    if "pole" in lower and "cable" in lower and "meters" in lower and "tall" in lower:
        families.append("pole_similarity")
    if "ladder" in lower and "wall" in lower and "meters" in lower:
        families.append("ladder_geometry")
    if "equal perimeters" in lower and "area" in lower and "lengths" in lower:
        families.append("equal_perimeters")
    if "equation" in lower and "describes the height" in lower and re.search(r"(?:projectile|ball)", lower) and "seconds" in lower:
        families.append("projectile_model")
    boundary = None
    if GIVEN_APPROX.search(text) and re.search(r"liters?|litres?|pints?|meters?|feet|inches", lower):
        boundary = "given_approximation"
    elif ROUNDING.search(text) and re.search(r"cost|price|charges|sells|average|per|ratio|volume", lower):
        boundary = "final_rounding"
    return {"decimal_tokens": tokens, "distinct_decimal_values": len(numbers),
            "recognized_families": families, "boundary_type": boundary,
            "has_approximation_instruction": bool(APPROX.search(text)), "malformed_diagram": malformed,
            "limitation": "Conservative prose recognizer; diagram labels and unsupported wordings excluded; semantics require review"}


def classify(scope, problem):
    validate_scope(scope)
    if "selector" not in scope:
        return {"matches": literal_matches(scope, problem), "selector": "literal_v1", "template_family": None}
    s = scope["selector"]
    f = question_features(problem)
    enough = f["distinct_decimal_values"] >= s["min_distinct_decimals"] and not f["malformed_diagram"]
    if s["kind"] == "decimal_reasoning_v1":
        recognized = sorted(set(f["recognized_families"]) & set(s["families"]))
        family = recognized[0] if recognized else None
        match = enough and bool(family) and not f["has_approximation_instruction"]
    else:
        family = f["boundary_type"]
        match = enough and family in s["boundary_types"]
    return {"matches": bool(match), "selector": s["kind"], "template_family": family, **f}


def matches(scope, problem):
    return classify(scope, problem)["matches"]


def select_pools(protocol, candidates, target_count, control_count):
    """Input order is seeded by prepare; quotas never fall back to random controls."""
    target_info = {r["problem_id"]: classify(protocol["scope"], r["problem"]) for r in candidates}
    targets = [r for r in candidates if target_info[r["problem_id"]]["matches"]]
    controls_scope = protocol.get("control_scope")
    control_info = {r["problem_id"]: classify(controls_scope, r["problem"]) for r in candidates} if controls_scope else {}
    controls = [r for r in candidates if not target_info[r["problem_id"]]["matches"]
                and (not controls_scope or control_info[r["problem_id"]]["matches"])]
    if controls_scope and any(target_info[pid]["matches"] and info["matches"] for pid, info in control_info.items()):
        raise ValueError("Target and boundary scopes overlap")
    available = {"target": len(targets), "control": len(controls)}
    policy = protocol.get("sampling_policy")
    if policy:
        if sum(policy["control_counts"].values()) != control_count:
            raise ValueError("Control count differs from frozen boundary quotas")
        selected, seen = [], set()
        for row in targets:
            family = target_info[row["problem_id"]]["template_family"]
            if family not in seen:
                selected.append(row)
                seen.add(family)
        targets = selected
        selected = []
        for kind, count in sorted(policy["control_counts"].items()):
            pool = [r for r in controls if control_info[r["problem_id"]]["template_family"] == kind]
            if len(pool) < count:
                raise ValueError(f"Insufficient boundary controls for {kind}: need {count}, have {len(pool)}")
            selected.extend(pool[:count])
        controls = selected
    if len(targets) < target_count or len(controls) < control_count:
        raise ValueError(f"Insufficient unused samples after family/role filtering: targets={len(targets)}, controls={len(controls)}")
    targets, controls = targets[:target_count], controls[:control_count]
    reasons = [{"problem_id": r["problem_id"], "role": role,
                "selection": (target_info if role == "target" else control_info).get(r["problem_id"], {"matches": True, "selector": "outside_target"})}
               for role, rows in (("target", targets), ("control", controls)) for r in rows]
    return targets, controls, {"available_before_family_cap": available, "selected": reasons,
                              "answers_used_for_selection": False, "policy": policy}
