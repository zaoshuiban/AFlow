# -*- coding: utf-8 -*-
"""Persistent, evidence-aware library for auto-evolved workflow operators.

The library deliberately separates three concerns that were previously mixed
inside ``operator.json``:

* capability metadata: what an operator is intended to do;
* routing safety: applicability rules and known failure boundaries;
* empirical evidence: where the operator won, tied, or lost against a named
  baseline.

Routing is deterministic and auditable.  An LLM is never asked to decide
whether an operator is safe to invoke.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


SCHEMA_VERSION = 1
ACTIVE_STATUSES = {"experimental", "admitted", "active"}
LIFECYCLE_STATUSES = ACTIVE_STATUSES | {"candidate", "rejected", "retired"}

# Features that an evolved operator is allowed to declare in generated
# metadata.  Keeping this vocabulary closed makes routing rules reviewable and
# prevents arbitrary Python expressions from being evaluated at runtime.
KNOWN_FEATURES = {
    "counting_problem",
    "probability_problem",
    "arrangement_problem",
    "finite_object_arrangement",
    "distinct_objects",
    "explicit_rotation_equivalence",
    "explicit_reflection_equivalence",
    "explicit_symmetry_equivalence",
    "finite_geometric_layout",
    "group_action_candidate",
    "burnside_safe",
    "people_seating",
    "circular_seating",
    "rotation_already_quotiented",
    "continuous_probability",
    "geometry_problem",
    "algebra_problem",
    "number_theory_problem",
    "parametric_curve",
    "trajectory_trace",
    "path_retracing_candidate",
    "distance_traveled_request",
    "parenthesization_distinct_value_dedup",
    "restricted_digit_prime_enumeration",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_problem_id(problem: str) -> str:
    normalized = " ".join(str(problem).lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def semantic_python_sha256(source: str) -> str:
    """Hash executable Python semantics while ignoring comments/formatting."""
    if not source:
        return ""
    tree = ast.parse(source)
    canonical = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def combined_artifact_sha256(class_code: str, prompt_code: str) -> str:
    payload = (
        semantic_python_sha256(class_code)
        + ":"
        + semantic_python_sha256(prompt_code)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_feature_list(value: Any) -> List[str]:
    """Parse a list or comma-separated feature string into known features."""
    if value is None:
        return []
    if isinstance(value, str):
        values = re.split(r"[,;\n]", value)
    elif isinstance(value, Iterable):
        values = list(value)
    else:
        values = [value]
    result = []
    for item in values:
        feature = str(item).strip().lower()
        if feature and feature in KNOWN_FEATURES and feature not in result:
            result.append(feature)
    return result


def raw_feature_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = re.split(r"[,;\n]", value)
    elif isinstance(value, Iterable):
        values = list(value)
    else:
        values = [value]
    return [str(item).strip().lower() for item in values if str(item).strip()]


@dataclass(frozen=True)
class ProblemContext:
    problem: str
    features: Set[str]
    matched_signals: Dict[str, List[str]] = field(default_factory=dict)

    def has_all(self, features: Sequence[str]) -> bool:
        return set(features).issubset(self.features)

    def has_any(self, features: Sequence[str]) -> bool:
        return bool(set(features) & self.features)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "problem_id": stable_problem_id(self.problem),
            "features": sorted(self.features),
            "matched_signals": self.matched_signals,
        }


class ProblemFeatureExtractor:
    """Rule-based feature extractor used by the safe operator router."""

    _PATTERNS: Dict[str, Sequence[str]] = {
        "counting_problem": (
            r"\bhow many\b",
            r"\bnumber of ways\b",
            r"\bcount(?:ing)?\b",
            r"\barrang(?:e|ement|ements|ing)\b",
            r"\bpermut(?:e|ation|ations)\b",
            r"\bcombin(?:ation|ations|atorial)\b",
        ),
        "probability_problem": (
            r"\bprobability\b",
            r"\brandom(?:ly)?\b",
            r"\bexpected value\b",
        ),
        "arrangement_problem": (
            r"\barrang(?:e|ement|ements|ing)\b",
            r"\bways? (?:can|to)\b.*\b(?:sit|place|put|arrange)\b",
            r"\bways? (?:are|is) there to\b.*\b(?:sit|place|put|arrange|color)\b",
            r"\bseatings?\b",
            r"\bcolorings?\b",
        ),
        "distinct_objects": (
            r"\bdistinct\b",
            r"\ball (?:are )?different\b",
            r"\bdifferent colors?\b",
            r"\bdifferently colored\b",
            r"\bunique\b",
        ),
        "finite_geometric_layout": (
            r"\b\d+\s*[x×\\times]+\s*\d+\s+grid\b",
            r"\bgrid\b",
            r"\bboard\b",
            r"\bhexagon(?:al)?\b",
            r"\bvertices\b",
            r"\bnecklace\b",
            r"\bbracelet\b",
        ),
        "people_seating": (
            r"\bpeople\b.*\b(?:sit|seat|seating)\b",
            r"\bpersons?\b.*\b(?:sit|seat|seating)\b",
            r"\b(?:sit|seat|seating)\b.*\bpeople\b",
        ),
        "circular_seating": (
            r"\b(?:sit|seated|seating)\b.*\b(?:round|circular) table\b",
            r"\baround (?:a |the )?(?:round|circular)?\s*table\b",
            r"\bcircular seating\b",
        ),
        "continuous_probability": (
            r"\bpoints? (?:are )?chosen randomly\b",
            r"\barrival times?\b",
            r"\buniformly (?:at random )?(?:on|in|from)\b",
            r"\bgeometric probability\b",
        ),
        "geometry_problem": (
            r"\btriangle\b",
            r"\bangle\b",
            r"\bpolygon\b",
            r"\barea\b",
            r"\bperimeter\b",
            r"\bcoordinate geometry\b",
        ),
        "algebra_problem": (
            r"\bpolynomial\b",
            r"\bequation\b",
            r"\binequalit(?:y|ies)\b",
            r"\bfunction\b",
            r"\barithmetic expression\b",
            r"\bexpression\b.{0,100}\b(?:operators?|parenthesiz)",
            r"\bparenthesiz(?:e|ed|ation|ations|ing)\b.{0,120}\bexpression\b",
        ),
        "number_theory_problem": (
            r"\bprime\b",
            r"\bdivisib(?:le|ility)\b",
            r"\bmod(?:ulo|ular)?\b",
            r"\bgcd\b",
            r"\blcm\b",
        ),
        "parametric_curve": (
            r"\bparametric (?:curve|path|equation)",
            r"\bposition (?:at time|is).{0,80}\(x\s*,\s*y\)",
            r"\(x\s*,\s*y\)\s*=.{0,160}\bt\b",
            r"\bx\s*\(t\)\s*=.{0,100}\by\s*\(t\)\s*=",
        ),
        "trajectory_trace": (
            r"\bpaint trail\b",
            r"\bink trail\b",
            r"\btrail (?:left|drawn|marked)\b",
            r"\b(?:traces?|draws?) (?:a |the )?(?:geometric )?(?:curve|path)\b",
            r"\bgeometric curve visible\b",
        ),
        "distance_traveled_request": (
            r"\btotal distance (?:it |the \w+ )?(?:travels?|traveled|travelled)\b",
            r"\bdistance (?:it |the \w+ )?(?:travels?|traveled|travelled)\b",
            r"\bincluding portions traversed more than once\b",
        ),
    }

    _ROTATION_EQUIVALENCE = (
        r"rotations?.{0,80}(?:considered|treated).{0,30}(?:same|equivalent|not distinct)",
        r"rotations?.{0,30}(?:as )?not distinct",
        r"rotate.{0,60}(?:same|equivalent)",
        r"rotating.{0,60}(?:does not|doesn't|do not|don't) change",
        r"(?:same|equivalent).{0,45}rotations?",
    )
    _REFLECTION_EQUIVALENCE = (
        r"reflections?.{0,80}(?:considered|treated).{0,30}(?:same|equivalent|not distinct)",
        r"reflect.{0,60}(?:same|equivalent)",
        r"flipping.{0,60}(?:does not|doesn't|do not|don't) change",
        r"(?:same|equivalent).{0,45}reflections?",
    )

    @staticmethod
    def _matches(text: str, patterns: Sequence[str]) -> List[str]:
        return [pattern for pattern in patterns if re.search(pattern, text, re.I | re.S)]

    @staticmethod
    def _term_is_positive(text: str, aliases: Sequence[str]) -> bool:
        """Resolve local negation such as 'but not reflections'."""
        for alias in aliases:
            for match in re.finditer(alias, text, re.I):
                prefix = text[max(0, match.start() - 18):match.start()]
                suffix = text[match.end():match.end() + 100]
                local_clause = text[match.start():match.end() + 130]
                if re.search(r"(?:\bnot|\bbut\s+not)\s*$", prefix, re.I):
                    continue
                if re.search(
                    r"^(?:\s+and\s+(?:rotations?|reflections?))?"
                    r".{0,30}(?:"
                    r"(?:are\s+)?not\s+(?:(?:considered|treated|counted)\s+)?"
                    r"(?:as\s+)?(?:the\s+)?(?:same|equivalent)"
                    r"|(?:are\s+)?(?:considered|treated|counted)\s+"
                    r"(?:as\s+)?distinct"
                    r")",
                    local_clause,
                    re.I | re.S,
                ):
                    continue
                if re.match(r"\s+(?:as\s+)?distinct\b", suffix, re.I):
                    continue
                positive_suffix = re.search(
                    r"(?:and(?:/or)?\s+(?:rotations?|reflections?)\s+)?"
                    r".{0,45}(?:considered|treated|counted).{0,25}"
                    r"(?:same|equivalent|not distinct)",
                    suffix,
                    re.I | re.S,
                )
                if positive_suffix or re.search(
                    r"^.{0,20}(?:as\s+)?not distinct\b", suffix, re.I | re.S
                ) or re.search(
                    r"^.{0,60}(?:does not|doesn't|do not|don't) change",
                    suffix,
                    re.I | re.S,
                ) or re.search(r"(?:same|equivalent).{0,35}$", prefix, re.I | re.S):
                    return True
        return False

    def extract(self, problem: str) -> ProblemContext:
        text = " ".join(str(problem).lower().split())
        features: Set[str] = set()
        signals: Dict[str, List[str]] = {}

        for feature, patterns in self._PATTERNS.items():
            matched = self._matches(text, patterns)
            if matched:
                features.add(feature)
                signals[feature] = matched

        rotation_matches = self._matches(text, self._ROTATION_EQUIVALENCE)
        if rotation_matches and self._term_is_positive(text, (r"rotations?", r"rotating", r"rotate")):
            features.add("explicit_rotation_equivalence")
            signals["explicit_rotation_equivalence"] = rotation_matches

        reflection_matches = self._matches(text, self._REFLECTION_EQUIVALENCE)
        if reflection_matches and self._term_is_positive(text, (r"reflections?", r"flipping", r"reflect")):
            features.add("explicit_reflection_equivalence")
            signals["explicit_reflection_equivalence"] = reflection_matches

        if features & {
            "explicit_rotation_equivalence",
            "explicit_reflection_equivalence",
        }:
            features.add("explicit_symmetry_equivalence")

        if "arrangement_problem" in features and (
            "counting_problem" in features or "probability_problem" not in features
        ):
            features.add("finite_object_arrangement")

        if (
            "explicit_symmetry_equivalence" in features
            and "finite_object_arrangement" in features
        ):
            features.add("group_action_candidate")

        if "circular_seating" in features and (
            "explicit_rotation_equivalence" in features
            or re.search(r"rotations?.{0,40}(?:not distinct|same)", text, re.I)
        ):
            features.add("rotation_already_quotiented")

        if (
            "group_action_candidate" in features
            and "distinct_objects" in features
            and "finite_geometric_layout" in features
            and "circular_seating" not in features
            and "continuous_probability" not in features
        ):
            features.add("burnside_safe")

        if (
            "parametric_curve" in features
            and "trajectory_trace" in features
            and re.search(
                r"(?:t\s*[+-]\s*(?:\d+|\\frac\{\d+\}\{\d+\}))"
                r"\s*\)?\s*\^?\s*\{?2\}?|t\s*\^\s*\{?2\}?",
                text,
            )
        ):
            features.add("path_retracing_candidate")

        # Keep routing and execution on one versioned semantic detector.
        from scripts.parameterized_tools import ParenthesizationDistinctValueTool

        if ParenthesizationDistinctValueTool.detect(str(problem)):
            features.add("parenthesization_distinct_value_dedup")
            signals["parenthesization_distinct_value_dedup"] = [
                "parameterized_tool_detector_v2"
            ]

        from scripts.restricted_prime_tool import RestrictedDigitPrimeEnumerationTool

        if RestrictedDigitPrimeEnumerationTool.detect(str(problem)):
            features.add("restricted_digit_prime_enumeration")
            signals["restricted_digit_prime_enumeration"] = [
                "restricted_prime_parameterized_detector_v1"
            ]

        return ProblemContext(problem=str(problem), features=features, matched_signals=signals)


@dataclass
class RetrievalCandidate:
    operator_name: str
    score: float
    matched_rule: str
    reasons: List[str]
    evidence_summary: Dict[str, int]
    eligible: bool = True
    evidence_gate_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operator_name": self.operator_name,
            "score": self.score,
            "matched_rule": self.matched_rule,
            "reasons": self.reasons,
            "evidence_summary": self.evidence_summary,
            "eligible": self.eligible,
            "evidence_gate_reasons": self.evidence_gate_reasons,
        }


@dataclass
class RetrievalDecision:
    context: ProblemContext
    selected: Optional[RetrievalCandidate]
    candidates: List[RetrievalCandidate]
    blocked: List[Dict[str, Any]]
    threshold: float
    library_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context": self.context.to_dict(),
            "selected": self.selected.to_dict() if self.selected else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "blocked": self.blocked,
            "threshold": self.threshold,
            "library_error": self.library_error,
        }


class OperatorLibrary:
    """JSON-backed operator registry with an append-only evidence audit log."""

    def __init__(
        self,
        path: os.PathLike[str] | str,
        audit_path: os.PathLike[str] | str | None = None,
        auto_create: bool = True,
    ):
        self.path = Path(path)
        self.audit_path = Path(audit_path) if audit_path else self.path.with_name("events.jsonl")
        self.extractor = ProblemFeatureExtractor()
        self.auto_create = auto_create
        if auto_create and not self.path.exists():
            self._write(self._empty_data())

    @staticmethod
    def _empty_data() -> Dict[str, Any]:
        now = utc_now()
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": now,
            "updated_at": now,
            "operators": {},
        }

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            if not self.auto_create:
                raise FileNotFoundError(f"Operator library not found: {self.path}")
            return self._empty_data()
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported operator library schema: {data.get('schema_version')}"
            )
        if not isinstance(data.get("operators"), dict):
            raise ValueError("operator library must contain an 'operators' object")
        for key, record in data["operators"].items():
            if not isinstance(record, dict):
                raise ValueError(f"operator record {key!r} must be an object")
            if str(record.get("name", "")) != str(key):
                raise ValueError(
                    f"operator record {key!r} must contain the same name"
                )
            if record.get("status", "candidate") not in LIFECYCLE_STATUSES:
                raise ValueError(
                    f"operator record {key!r} has an invalid lifecycle status"
                )
            for field_name in (
                "applicability_rules",
                "failure_boundaries",
                "evidence",
            ):
                if not isinstance(record.get(field_name, []), list):
                    raise ValueError(
                        f"operator record {key!r} field {field_name!r} "
                        "must be a list"
                    )
            if not isinstance(record.get("artifact", {}), dict):
                raise ValueError(
                    f"operator record {key!r} field 'artifact' must be an object"
                )
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["schema_version"] = SCHEMA_VERSION
        data["updated_at"] = utc_now()
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _audit(self, event: Dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": utc_now(), **event}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def list_records(self, statuses: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
        records = list(self.load()["operators"].values())
        if statuses is not None:
            records = [record for record in records if record.get("status") in statuses]
        return copy.deepcopy(records)

    def get(self, operator_name: str) -> Optional[Dict[str, Any]]:
        record = self.load()["operators"].get(operator_name)
        return copy.deepcopy(record) if record else None

    def upsert(
        self,
        record: Dict[str, Any],
        audit_event: str = "operator_upserted",
        *,
        replace_evidence: bool = False,
    ) -> Dict[str, Any]:
        name = str(record.get("name") or record.get("class_name") or "").strip()
        if not name:
            raise ValueError("operator record requires name or class_name")
        data = self.load()
        existing = data["operators"].get(name, {})
        merged = copy.deepcopy(existing)
        for key, value in record.items():
            if key == "evidence" and existing.get("evidence") and not replace_evidence:
                combined = list(existing["evidence"]) + list(value or [])
                deduplicated = []
                seen = set()
                for item in combined:
                    item_key = item.get("id") or hashlib.sha256(
                        json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")
                    ).hexdigest()[:16]
                    if item_key in seen:
                        continue
                    seen.add(item_key)
                    deduplicated.append(item)
                merged[key] = deduplicated
            else:
                merged[key] = copy.deepcopy(value)
        merged.setdefault("name", name)
        merged.setdefault("class_name", name)
        merged.setdefault("status", "candidate")
        if merged["status"] not in LIFECYCLE_STATUSES:
            raise ValueError(f"Unknown operator status: {merged['status']!r}")
        merged.setdefault("version", 1)
        merged.setdefault("description", "")
        merged.setdefault("module", "")
        merged.setdefault("applicability_rules", [])
        merged.setdefault("failure_boundaries", [])
        merged.setdefault("evidence", [])
        merged.setdefault("provenance", {})
        merged.setdefault("created_at", existing.get("created_at", utc_now()))
        merged["updated_at"] = utc_now()
        data["operators"][name] = merged
        self._write(data)
        self._audit({"event": audit_event, "operator_name": name})
        return copy.deepcopy(merged)

    def set_status(self, operator_name: str, status: str, reason: str = "") -> None:
        if status not in LIFECYCLE_STATUSES:
            raise ValueError(
                f"Unknown operator status {status!r}; expected one of "
                f"{sorted(LIFECYCLE_STATUSES)}"
            )
        data = self.load()
        if operator_name not in data["operators"]:
            raise KeyError(operator_name)
        data["operators"][operator_name]["status"] = status
        data["operators"][operator_name]["status_reason"] = reason
        data["operators"][operator_name]["updated_at"] = utc_now()
        self._write(data)
        self._audit(
            {
                "event": "operator_status_changed",
                "operator_name": operator_name,
                "status": status,
                "reason": reason,
            }
        )

    def record_evidence(self, operator_name: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        data = self.load()
        if operator_name not in data["operators"]:
            raise KeyError(operator_name)
        record = data["operators"][operator_name]
        item = copy.deepcopy(evidence)
        item.setdefault("operator_version", record.get("version", 1))
        item.setdefault(
            "artifact_sha256",
            record.get("artifact", {}).get("artifact_sha256", ""),
        )
        item.setdefault("routing_eligible", False)
        item.setdefault("protocol", "unspecified")
        item.setdefault("id", hashlib.sha256(
            json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16])
        item.setdefault("timestamp", utc_now())
        item.setdefault("wins", 0)
        item.setdefault("ties", 0)
        item.setdefault("losses", 0)
        for key in ("wins", "ties", "losses"):
            try:
                item[key] = int(item[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"evidence {key} must be an integer") from exc
            if item[key] < 0:
                raise ValueError(f"evidence {key} must be non-negative")
        for count_key in ("unique_problems", "unique_winning_problems"):
            if item.get(count_key) is None:
                continue
            try:
                item[count_key] = int(item[count_key])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"evidence {count_key} must be an integer"
                ) from exc
            if item[count_key] < 0:
                raise ValueError(
                    f"evidence {count_key} must be non-negative"
                )
        for existing in record.get("evidence", []):
            if existing.get("id") == item["id"]:
                return copy.deepcopy(existing)
        record.setdefault("evidence", []).append(item)
        record["updated_at"] = utc_now()
        self._write(data)
        self._audit(
            {
                "event": "operator_evidence_recorded",
                "operator_name": operator_name,
                "evidence_id": item["id"],
                "scope": item.get("scope", "overall"),
                "wins": item["wins"],
                "ties": item["ties"],
                "losses": item["losses"],
            }
        )
        return item

    @staticmethod
    def _boundary_match(boundary: Dict[str, Any], context: ProblemContext) -> bool:
        all_raw = raw_feature_list(boundary.get("features_all", []))
        any_raw = raw_feature_list(boundary.get("features_any", []))
        if (set(all_raw) | set(any_raw)) - KNOWN_FEATURES:
            return True  # invalid safety metadata fails closed
        all_features = parse_feature_list(all_raw)
        any_features = parse_feature_list(any_raw)
        if all_features and not context.has_all(all_features):
            return False
        if any_features and not context.has_any(any_features):
            return False
        if not all_features and not any_features:
            return False
        return True

    @staticmethod
    def _rule_match(rule: Dict[str, Any], context: ProblemContext) -> bool:
        required_raw = raw_feature_list(rule.get("required_features", []))
        any_raw = raw_feature_list(rule.get("any_features", []))
        forbidden_raw = raw_feature_list(rule.get("forbidden_features", []))
        if (set(required_raw) | set(any_raw) | set(forbidden_raw)) - KNOWN_FEATURES:
            return False
        required = parse_feature_list(required_raw)
        any_features = parse_feature_list(any_raw)
        forbidden = parse_feature_list(forbidden_raw)
        if required and not context.has_all(required):
            return False
        if any_features and not context.has_any(any_features):
            return False
        if forbidden and context.has_any(forbidden):
            return False
        return bool(required or any_features)

    @staticmethod
    def _evidence_is_routing_eligible(
        record: Dict[str, Any], item: Dict[str, Any], scope: str
    ) -> bool:
        artifact_sha = record.get("artifact", {}).get("artifact_sha256", "")
        protocol = str(item.get("protocol", ""))
        return bool(
            item.get("scope") == scope
            and item.get("routing_eligible") is True
            and item.get("operator_version") == record.get("version", 1)
            and artifact_sha
            and item.get("artifact_sha256") == artifact_sha
            and protocol.startswith(
                ("paired_equal_call", "paired_correct_within_timeout")
            )
            and item.get("baseline") == "generic_two_stage"
        )

    @classmethod
    def _evidence_summary(cls, record: Dict[str, Any], scope: str) -> Dict[str, int]:
        summary = {
            "wins": 0,
            "ties": 0,
            "losses": 0,
            "observations": 0,
            "unique_problems": 0,
            "unique_winning_problems": 0,
            "excluded_evidence": 0,
        }
        evidence = record.get("evidence", [])
        selected = [
            item
            for item in evidence
            if cls._evidence_is_routing_eligible(record, item, scope)
        ]
        summary["excluded_evidence"] = len(evidence) - len(selected)
        problem_ids = set()
        winning_problem_ids = set()
        declared_unique_counts = []
        declared_unique_winning_counts = []
        for item in selected:
            for key in ("wins", "ties", "losses"):
                summary[key] += int(item.get(key, 0) or 0)
            if item.get("problem_id"):
                problem_ids.add(str(item["problem_id"]))
                if int(item.get("wins", 0) or 0) > 0:
                    winning_problem_ids.add(str(item["problem_id"]))
            if item.get("unique_problems") is not None:
                try:
                    declared_unique_counts.append(
                        max(0, int(item.get("unique_problems", 0) or 0))
                    )
                except (TypeError, ValueError):
                    declared_unique_counts.append(0)
            if item.get("unique_winning_problems") is not None:
                try:
                    declared_unique_winning_counts.append(
                        max(
                            0,
                            int(item.get("unique_winning_problems", 0) or 0),
                        )
                    )
                except (TypeError, ValueError):
                    declared_unique_winning_counts.append(0)
        summary["observations"] = summary["wins"] + summary["ties"] + summary["losses"]
        summary["unique_problems"] = max(
            [len(problem_ids), *declared_unique_counts], default=0
        )
        summary["unique_winning_problems"] = max(
            [len(winning_problem_ids), *declared_unique_winning_counts],
            default=0,
        )
        return summary

    @staticmethod
    def _rule_weight(rule: Dict[str, Any]) -> float:
        try:
            value = float(rule.get("weight", 0.65))
        except (TypeError, ValueError):
            return -1.0
        return value if 0.0 <= value <= 1.0 else -1.0

    def retrieve(
        self,
        problem: str,
        top_k: int = 3,
        min_score: float = 0.70,
    ) -> RetrievalDecision:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not 0.0 <= min_score <= 1.0:
            raise ValueError("min_score must be between 0 and 1")
        context = self.extractor.extract(problem)
        candidates: List[RetrievalCandidate] = []
        blocked: List[Dict[str, Any]] = []

        for record in self.list_records(statuses=ACTIVE_STATUSES):
            name = record["name"]
            matched_boundaries = [
                boundary
                for boundary in record.get("failure_boundaries", [])
                if self._boundary_match(boundary, context)
            ]
            hard_boundaries = [
                boundary for boundary in matched_boundaries if boundary.get("hard_block", True)
            ]
            if hard_boundaries:
                blocked.append(
                    {
                        "operator_name": name,
                        "boundaries": [
                            {
                                "name": boundary.get("name", "unnamed"),
                                "reason": boundary.get("reason", "known failure boundary"),
                            }
                            for boundary in hard_boundaries
                        ],
                    }
                )
                continue

            matching_rules = [
                rule
                for rule in record.get("applicability_rules", [])
                if self._rule_match(rule, context)
            ]
            if not matching_rules:
                continue

            rule = max(matching_rules, key=self._rule_weight)
            rule_name = rule.get("name", "default")
            evidence = self._evidence_summary(record, rule_name)
            decisive = evidence["wins"] + evidence["losses"]
            net = (evidence["wins"] - evidence["losses"]) / (decisive + 2)
            volume = min(1.0, evidence["observations"] / 20.0)
            score = self._rule_weight(rule) + 0.15 * net + 0.05 * volume
            score = max(0.0, min(1.0, score))

            # Every routable lifecycle state remains evidence-gated. Human
            # promotion cannot bypass revision-bound paired evidence.
            default_min_observations = 6
            default_min_net_wins = 2
            default_min_unique_problems = 2
            default_min_unique_winning_problems = 2
            evidence_gate_reasons = []
            try:
                min_observations = int(
                    rule.get("min_observations", default_min_observations)
                )
                if min_observations < 0:
                    raise ValueError
            except (TypeError, ValueError):
                min_observations = 10**9
                evidence_gate_reasons.append("invalid min_observations metadata")
            try:
                min_net_wins = int(
                    rule.get("min_net_wins", default_min_net_wins)
                )
                if min_net_wins < 0:
                    raise ValueError
            except (TypeError, ValueError):
                min_net_wins = 10**9
                evidence_gate_reasons.append("invalid min_net_wins metadata")
            try:
                min_unique_problems = int(
                    rule.get(
                        "min_unique_problems", default_min_unique_problems
                    )
                )
                if min_unique_problems < 0:
                    raise ValueError
            except (TypeError, ValueError):
                min_unique_problems = 10**9
                evidence_gate_reasons.append(
                    "invalid min_unique_problems metadata"
                )
            try:
                min_unique_winning_problems = int(
                    rule.get(
                        "min_unique_winning_problems",
                        default_min_unique_winning_problems,
                    )
                )
                if min_unique_winning_problems < 0:
                    raise ValueError
            except (TypeError, ValueError):
                min_unique_winning_problems = 10**9
                evidence_gate_reasons.append(
                    "invalid min_unique_winning_problems metadata"
                )
            if self._rule_weight(rule) < 0:
                evidence_gate_reasons.append("invalid rule weight metadata")
            if evidence["observations"] < min_observations:
                evidence_gate_reasons.append(
                    f"requires at least {min_observations} scoped observations"
                )
            observed_net_wins = evidence["wins"] - evidence["losses"]
            if observed_net_wins < min_net_wins:
                evidence_gate_reasons.append(
                    f"requires at least {min_net_wins} scoped net wins"
                )
            if evidence["unique_problems"] < min_unique_problems:
                evidence_gate_reasons.append(
                    f"requires at least {min_unique_problems} unique scoped problems"
                )
            if (
                evidence["unique_winning_problems"]
                < min_unique_winning_problems
            ):
                evidence_gate_reasons.append(
                    "requires at least "
                    f"{min_unique_winning_problems} unique winning problems"
                )
            reasons = [
                f"matched applicability rule '{rule_name}'",
                f"required features present: {', '.join(parse_feature_list(rule.get('required_features', [])))}",
                f"evidence W/T/L={evidence['wins']}/{evidence['ties']}/{evidence['losses']}",
            ]
            candidates.append(
                RetrievalCandidate(
                    operator_name=name,
                    score=score,
                    matched_rule=rule_name,
                    reasons=reasons,
                    evidence_summary=evidence,
                    eligible=not evidence_gate_reasons,
                    evidence_gate_reasons=evidence_gate_reasons,
                )
            )

        candidates.sort(
            key=lambda item: (not item.eligible, -item.score, item.operator_name)
        )
        candidates = candidates[:top_k]
        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.eligible and candidate.score >= min_score
            ),
            None,
        )
        return RetrievalDecision(
            context=context,
            selected=selected,
            candidates=candidates,
            blocked=blocked,
            threshold=min_score,
        )

    def register_evolved_candidate(
        self,
        *,
        name: str,
        description: str,
        module: str,
        skeleton_type: str,
        applicability_features: Any = None,
        failure_features: Any = None,
        provenance: Optional[Dict[str, Any]] = None,
        class_code: str = "",
        prompt_code: str = "",
    ) -> Dict[str, Any]:
        required = parse_feature_list(applicability_features)
        failures = parse_feature_list(failure_features)
        rules = []
        if required:
            rules.append(
                {
                    "name": "generated_applicability",
                    "description": "Applicability metadata proposed during operator evolution.",
                    "required_features": required,
                    "forbidden_features": failures,
                    "weight": 0.65,
                    "min_observations": 6,
                    "min_net_wins": 2,
                    "min_unique_problems": 2,
                    "min_unique_winning_problems": 2,
                }
            )
        boundaries = []
        if failures:
            boundaries.append(
                {
                    "name": "generated_failure_boundary",
                    "features_any": failures,
                    "hard_block": True,
                    "reason": "Failure boundary proposed during operator evolution.",
                }
            )
        prompt_names = re.findall(
            r"^([A-Z][A-Z0-9_]*_PROMPT)\s*=", prompt_code, re.M
        )
        artifact = {
            "class_code": class_code,
            "prompt_code": prompt_code,
            "class_sha256": semantic_python_sha256(class_code),
            "prompt_sha256": semantic_python_sha256(prompt_code),
            "artifact_sha256": combined_artifact_sha256(class_code, prompt_code),
            "prompt_names": prompt_names,
        }
        existing = self.get(name) or {}
        old_digest = existing.get("artifact", {}).get("artifact_sha256", "")
        revision_changed = bool(existing and old_digest != artifact["artifact_sha256"])
        version = int(existing.get("version", 0) or 0) + int(revision_changed)
        if not existing:
            version = 1
        historical_versions = copy.deepcopy(
            existing.get("historical_versions", [])
        )
        if revision_changed:
            historical_versions.append(
                {
                    "version": existing.get("version", 1),
                    "status": existing.get("status", "candidate"),
                    "artifact": existing.get("artifact", {}),
                    "applicability_rules": existing.get(
                        "applicability_rules", []
                    ),
                    "failure_boundaries": existing.get(
                        "failure_boundaries", []
                    ),
                    "evidence": existing.get("evidence", []),
                    "provenance": existing.get("provenance", {}),
                    "archived_at": utc_now(),
                }
            )
        payload = {
            "name": name,
            "class_name": name,
            "module": module,
            "version": version,
            "description": description,
            "status": "candidate",
            "status_reason": "New artifact revision; paired evidence required.",
            "skeleton_type": skeleton_type,
            "applicability_rules": rules,
            "failure_boundaries": boundaries,
            "evidence": [] if revision_changed else existing.get("evidence", []),
            "historical_versions": historical_versions,
            "provenance": provenance or {},
            "artifact": artifact,
        }
        return self.upsert(
            payload,
            audit_event="evolved_candidate_registered",
            replace_evidence=revision_changed,
        )

    def _artifact_text(self, artifact: Dict[str, Any], inline_key: str, path_key: str) -> str:
        inline = artifact.get(inline_key, "")
        if inline:
            return str(inline)
        path_value = artifact.get(path_key, "")
        if not path_value:
            return ""
        path = Path(path_value)
        candidates = [path] if path.is_absolute() else [Path.cwd() / path]
        if not path.is_absolute():
            candidates.extend(parent / path for parent in self.path.parents)
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        return ""

    @staticmethod
    def _replace_class_definition(
        source: str, class_name: str, class_code: str
    ) -> str:
        tree = ast.parse(source)
        matches = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ]
        if not matches:
            marker = f"# ========== Restored library operator: {class_name} =========="
            return source.rstrip() + f"\n\n{marker}\n\n" + class_code.strip() + "\n"
        node = matches[-1]
        lines = source.splitlines(keepends=True)
        replacement = class_code.strip() + "\n"
        lines[node.lineno - 1:node.end_lineno] = [replacement]
        return "".join(lines)

    @staticmethod
    def _replace_prompt_definitions(source: str, prompt_code: str) -> str:
        prompt_names = set(
            re.findall(r"^([A-Z][A-Z0-9_]*_PROMPT)\s*=", prompt_code, re.M)
        )
        if not prompt_names:
            return source
        tree = ast.parse(source)
        ranges = []
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {
                target.id for target in targets if isinstance(target, ast.Name)
            }
            if names & prompt_names:
                ranges.append((node.lineno - 1, node.end_lineno))
        lines = source.splitlines(keepends=True)
        for start, end in reversed(ranges):
            del lines[start:end]
        marker = "# ========== Restored revision-bound library prompts =========="
        lines = [line for line in lines if line.strip() != marker]
        return "".join(lines).rstrip() + f"\n\n{marker}\n\n" + prompt_code.strip() + "\n"

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def install_artifact(self, operator_name: str, template_dir: os.PathLike[str] | str) -> bool:
        """Restore an archived operator fragment into a mutable template.

        This is an explicit installation operation, not a routing side effect.
        It allows the persistent library to survive optimizer template rollback.
        """
        record = self.get(operator_name)
        if not record:
            raise KeyError(operator_name)
        artifact = record.get("artifact", {})
        class_code = self._artifact_text(artifact, "class_code", "class_code_path")
        prompt_code = self._artifact_text(artifact, "prompt_code", "prompt_code_path")
        if not class_code:
            raise ValueError(f"No archived class code for {operator_name}")
        actual_class_hash = semantic_python_sha256(class_code)
        actual_prompt_hash = semantic_python_sha256(prompt_code)
        actual_artifact_hash = combined_artifact_sha256(class_code, prompt_code)
        for key, actual in (
            ("class_sha256", actual_class_hash),
            ("prompt_sha256", actual_prompt_hash),
            ("artifact_sha256", actual_artifact_hash),
        ):
            expected = artifact.get(key, "")
            # A reusable deterministic tool may intentionally have no prompt;
            # its semantic prompt hash is therefore the empty string.  Class
            # and combined artifact hashes must always remain non-empty.
            missing_required_hash = key != "prompt_sha256" and not expected
            if missing_required_hash or expected != actual:
                raise ValueError(
                    f"Archived artifact integrity check failed for {operator_name}: {key}"
                )

        template_path = Path(template_dir)
        operator_path = template_path / "operator.py"
        prompt_path = template_path / "op_prompt.py"
        json_path = template_path / "operator.json"
        if not operator_path.exists() or not prompt_path.exists() or not json_path.exists():
            raise FileNotFoundError(f"Incomplete operator template: {template_path}")

        original_operator = operator_path.read_text(encoding="utf-8")
        original_prompt = prompt_path.read_text(encoding="utf-8")
        original_json = json_path.read_text(encoding="utf-8")
        new_operator = self._replace_class_definition(
            original_operator, operator_name, class_code
        )
        new_prompt = self._replace_prompt_definitions(original_prompt, prompt_code)
        with json_path.open("r", encoding="utf-8") as handle:
            operator_json = json.load(handle)
        operator_json[operator_name] = {
            "description": record.get("description", ""),
            "interface": f"{operator_name.lower()}(problem: str) -> dict with key 'response' of type str",
            "auto_evolved": True,
            "skeleton_type": record.get("skeleton_type", "unknown"),
            "library_status": record.get("status", "candidate"),
            "contextual_routing": True,
            "operator_version": record.get("version", 1),
            "artifact_sha256": actual_artifact_hash,
        }
        new_json = json.dumps(operator_json, indent=4, ensure_ascii=False) + "\n"
        compile(new_operator, str(operator_path), "exec")
        compile(new_prompt, str(prompt_path), "exec")
        try:
            self._atomic_write_text(operator_path, new_operator)
            self._atomic_write_text(prompt_path, new_prompt)
            self._atomic_write_text(json_path, new_json)
        except Exception:
            self._atomic_write_text(operator_path, original_operator)
            self._atomic_write_text(prompt_path, original_prompt)
            self._atomic_write_text(json_path, original_json)
            raise
        self._audit(
            {
                "event": "operator_artifact_installed",
                "operator_name": operator_name,
                "template_dir": str(template_path.resolve()),
            }
        )
        return True

    def install_routable_artifacts(
        self, template_dir: os.PathLike[str] | str
    ) -> List[str]:
        """Restore every routable library artifact into a mutable template."""
        installed = []
        for record in self.list_records(statuses=ACTIVE_STATUSES):
            artifact = record.get("artifact", {})
            if not artifact.get("class_code") and not artifact.get("class_code_path"):
                continue
            self.install_artifact(record["name"], template_dir)
            installed.append(record["name"])
        return installed
