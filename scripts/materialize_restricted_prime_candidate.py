"""Compile a frozen restricted-prime design into a reusable exact candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from benchmarks.math import MATHBenchmark
from scripts.operator_library import OperatorLibrary
from scripts.restricted_prime_tool import (
    RESTRICTED_DIGIT_PRIME_MECHANISM,
    RestrictedDigitPrimeEnumerationTool,
)
from scripts.trajectory_failure_expander import (
    file_sha256,
    load_bundle_manifest,
    load_bundle_split,
)


CLASS_NAME = "ReusableRestrictedDigitPrimeEnumerator"
RULE_NAME = "restricted_digit_prime_enumeration_v1"


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _class_code(runtime_hash: str, revision: int) -> str:
    return f'''class {CLASS_NAME}(Operator):
    operator_revision = {revision}
    tool_runtime_sha256 = "{runtime_hash}"
    _required_features = ("{RESTRICTED_DIGIT_PRIME_MECHANISM}",)
    _blocked_features = (
        "probability_problem", "continuous_probability", "geometry_problem",
        "algebra_problem", "people_seating", "circular_seating",
        "parametric_curve", "trajectory_trace", "distance_traveled_request",
    )

    def __init__(self, llm: AsyncLLM, name: str = "{CLASS_NAME}"):
        super().__init__(llm, name)

    def _preflight(self, problem: str):
        from scripts.operator_library import ProblemFeatureExtractor
        context = ProblemFeatureExtractor().extract(problem)
        missing = sorted(set(self._required_features) - set(context.features))
        blocked = sorted(set(self._blocked_features) & set(context.features))
        if missing or blocked:
            reasons = []
            if missing:
                reasons.append("missing=" + ",".join(missing))
            if blocked:
                reasons.append("blocked=" + ",".join(blocked))
            return {{
                "status": "declined", "response": "",
                "observation": "specialist_not_applicable:" + ";".join(reasons),
            }}
        return None

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        from scripts.restricted_prime_tool import RestrictedDigitPrimeEnumerationTool
        result = RestrictedDigitPrimeEnumerationTool.solve(problem)
        trace = {{
            "route": "reusable_restricted_prime_tool_v{revision}",
            "operator_revision": self.operator_revision,
            "tool_runtime_sha256": self.tool_runtime_sha256,
            **result.to_dict(),
        }}
        if result.status == "success":
            return {{
                "status": "success", "response": result.response,
                "observation": "", "tool_trace": trace,
            }}
        return {{
            "status": "declined", "response": "",
            "observation": result.observation or result.status,
            "tool_trace": trace,
        }}'''


def materialize(
    *, workspace_root: Path, mechanism_manifest: Path,
    source_batch_manifest: Path, source_candidate: str, output_dir: Path,
    development_data: Path | None = None, development_index: int | None = None,
    frozen_capability_audit: Path | None = None,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"reusable candidate output already exists: {output_dir}")
    family = load_bundle_manifest(mechanism_manifest)
    if family.get("mechanism", {}).get("name") != RESTRICTED_DIGIT_PRIME_MECHANISM:
        raise ValueError("mechanism family is not restricted-digit prime enumeration")
    # This is the only split opened during materialization.
    design_rows = load_bundle_split(family, "design")
    source_batch = json.loads(source_batch_manifest.read_text(encoding="utf-8"))
    if source_batch.get("status") not in {"pending_tournament", "ranked_not_promoted"}:
        raise ValueError("source batch is not a frozen candidate batch")
    frozen_audit: Dict[str, Any] | None = None
    if frozen_capability_audit is not None:
        frozen_audit = json.loads(frozen_capability_audit.read_text(encoding="utf-8"))
        hashes = frozen_audit.get("hashes", {})
        capability_root = frozen_capability_audit.parent
        expected_files = {
            "tournament_summary_sha256": capability_root / "tournament_summary.json",
            "tournament_details_sha256": capability_root / "tournament_details.jsonl",
            "batch_manifest_sha256": capability_root / "manifest.json",
        }
        if frozen_audit.get("status") != "completed_frozen" or frozen_audit.get(
            "result", {}
        ).get("passed") is not True:
            raise ValueError("source capability audit is not frozen and passing")
        for key, path in expected_files.items():
            if not path.is_file() or file_sha256(path) != hashes.get(key):
                raise ValueError(f"frozen capability source changed: {key}")
    if source_candidate not in source_batch.get("candidate_names", []):
        raise ValueError("source candidate is absent from the source batch")
    expected_family = source_batch.get("batch_context", {}).get("mechanism_family", {})
    if expected_family.get("manifest_sha256") != family["_manifest_sha256"]:
        raise ValueError("source candidate and mechanism family do not match")
    design = next(
        (
            item for item in source_batch.get("designs", [])
            if item.get("operator_name") == source_candidate
        ),
        None,
    )
    if not design or design.get("skeleton_type") != "programmer_step":
        raise ValueError("source design is not the frozen exact-computation candidate")
    mechanism_text = " ".join(
        str(design.get(key, ""))
        for key in ("mechanism", "prompt_1", "expected_advantage")
    ).lower()
    required_concepts = ("permutation", "leading zero", "primality", "deduplic")
    if any(concept not in mechanism_text for concept in required_concepts):
        raise ValueError("source design lacks required reusable-algorithm concepts")

    visible_rows = list(design_rows)
    if (development_data is None) != (development_index is None):
        raise ValueError("development data and index must be supplied together")
    if development_data is not None and development_index is not None:
        development_rows = [
            json.loads(line)
            for line in development_data.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if development_index < 0 or development_index >= len(development_rows):
            raise ValueError("development index is outside the dataset")
        original = dict(development_rows[development_index])
        original["mechanism_metadata"] = {
            "family": RESTRICTED_DIGIT_PRIME_MECHANISM,
            "problem_id": hashlib.sha256(
                " ".join(str(original["problem"]).lower().split()).encode()
            ).hexdigest()[:16],
            "origin": "original_119_development_case",
            "source_index": development_index,
            "visible_to_operator_generator": True,
        }
        visible_rows.append(original)

    scorer = MATHBenchmark("MATH", "", "")
    validations = []
    for row in visible_rows:
        result = RestrictedDigitPrimeEnumerationTool.solve(str(row["problem"]))
        score, extracted = scorer.calculate_score(str(row["solution"]), result.response)
        validations.append(
            {
                "problem_id": row["mechanism_metadata"]["problem_id"],
                "status": result.status,
                "score": int(score),
                "extracted": extracted,
                "elapsed_ms": result.elapsed_ms,
                "parameters": result.parameters,
            }
        )
    if not validations or any(
        row["status"] != "success" or row["score"] != 1 for row in validations
    ):
        raise RuntimeError("reusable prime tool failed visible design validation")

    runtime_path = Path(__file__).with_name("restricted_prime_tool.py")
    runtime_hash = file_sha256(runtime_path)
    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    )
    source_record = library.get(source_candidate) or {}
    source_artifact_hash = source_record.get("artifact", {}).get("artifact_sha256", "")
    if not source_artifact_hash:
        raise ValueError("source candidate artifact is missing")
    prior = library.get(CLASS_NAME)
    revision = int(prior.get("version", 0)) + 1 if prior else 1
    record = library.register_evolved_candidate(
        name=CLASS_NAME,
        description=(
            "Reusable exact restricted-digit prime enumerator compiled from the "
            "frozen auto-generated exhaustive-enumeration design."
        ),
        module="workspace.MATH.workflows.template.operator",
        skeleton_type=f"reusable_parameterized_tool_prime_v{revision}",
        applicability_features=RESTRICTED_DIGIT_PRIME_MECHANISM,
        failure_features=(
            "probability_problem,continuous_probability,geometry_problem,"
            "algebra_problem,people_seating,circular_seating,parametric_curve,"
            "trajectory_trace,distance_traveled_request"
        ),
        provenance={
            "kind": "materialized_parameterized_tool",
            "dataset": "MATH",
            "mechanism": RESTRICTED_DIGIT_PRIME_MECHANISM,
            "source_batch_id": source_batch["batch_id"],
            "source_batch_manifest": str(source_batch_manifest.resolve()),
            "source_batch_manifest_sha256": file_sha256(source_batch_manifest),
            "source_candidate": source_candidate,
            "source_candidate_artifact_sha256": source_artifact_hash,
            "transformation": "frozen_design_to_reusable_parameterized_tool_v1",
            "visible_design_only": True,
            "hidden_splits_opened": False,
            "tool_runtime_sha256": runtime_hash,
            "operator_revision": revision,
            "frozen_capability_audit": (
                str(frozen_capability_audit.resolve())
                if frozen_capability_audit is not None else None
            ),
            "frozen_capability_audit_sha256": (
                file_sha256(frozen_capability_audit)
                if frozen_capability_audit is not None else None
            ),
            "development_index": development_index,
        },
        class_code=_class_code(runtime_hash, revision),
        prompt_code="",
    )
    artifact = dict(record["artifact"])
    artifact["runtime_dependency"] = "scripts/restricted_prime_tool.py"
    artifact["runtime_dependency_sha256"] = runtime_hash
    library.upsert(
        {"name": CLASS_NAME, "artifact": artifact},
        audit_event="restricted_prime_runtime_dependency_bound",
    )
    forbidden = (library.get(CLASS_NAME) or {})["applicability_rules"][0].get(
        "forbidden_features", []
    )
    library.upsert(
        {
            "name": CLASS_NAME,
            "applicability_rules": [
                {
                    "name": f"restricted_digit_prime_enumeration_v{revision}",
                    "description": (
                        "Exact count of primes formed from a finite digit set without "
                        "repetition or leading zero."
                    ),
                    "required_features": [RESTRICTED_DIGIT_PRIME_MECHANISM],
                    "forbidden_features": forbidden,
                    "weight": 0.84,
                    "min_observations": 6,
                    "min_net_wins": 2,
                    "min_unique_problems": 2,
                    "min_unique_winning_problems": 2,
                }
            ],
        },
        audit_event="restricted_prime_routing_scope_frozen_before_hidden_gate",
    )
    record = library.get(CLASS_NAME)
    output_dir.mkdir(parents=True)
    validation_path = output_dir / "visible_design_validation.json"
    validation_path.write_text(
        json.dumps(
            {
                "scientific_evidence": False,
                "split": "visible_design",
                "candidate": CLASS_NAME,
                "correct": sum(row["score"] for row in validations),
                "total": len(validations),
                "rows": validations,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "batch_id": output_dir.name,
        "status": "pending_tournament",
        "dataset": "MATH",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_names": [CLASS_NAME],
        "designs": [
            {
                "operator_name": CLASS_NAME,
                "skeleton_type": f"reusable_parameterized_tool_prime_v{revision}",
                "mechanism": RESTRICTED_DIGIT_PRIME_MECHANISM,
                "source_candidate": source_candidate,
                "source_candidate_artifact_sha256": source_artifact_hash,
            }
        ],
        "batch_context": {
            "evaluation_protocol": "hidden_mechanism_gate_v1",
            "primary_metric": "correct_within_timeout",
            "operator_track": "tool_augmented",
            "operator_generation_policy": {
                "execution_mode": "tool_augmented",
                "execution_submode": "reusable_parameterized_tool",
                "runtime_code_generation": False,
                "unknown_mechanism_fallback": "generic_two_stage",
            },
            "mechanism_family": {
                "family_id": family["family_id"],
                "manifest_path": family["_manifest_path"],
                "manifest_sha256": family["_manifest_sha256"],
                "generator_version": family["generator_version"],
                "visible_split": "design",
                "design_sha256": family["splits"]["design"]["sha256"],
                "design_count": family["splits"]["design"]["count"],
                "hidden_gate_count": family["splits"]["gate"]["count"],
                "hidden_regression_count": family["splits"]["regression"]["count"],
            },
        },
        "source_batch": {
            "batch_id": source_batch["batch_id"],
            "manifest": str(source_batch_manifest.resolve()),
            "manifest_sha256": file_sha256(source_batch_manifest),
        },
        "visible_design_validation": {
            "path": str(validation_path.resolve()),
            "sha256": file_sha256(validation_path),
            "correct": len(validations),
            "total": len(validations),
        },
        "candidate_artifact_sha256": record["artifact"]["artifact_sha256"],
        "operator_record_sha256": _json_hash(record),
        "operator_version": revision,
        "tool_runtime_sha256": runtime_hash,
        "promotion_allowed": False,
        "installed": False,
        "routed": False,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default="workspace/MATH")
    parser.add_argument("--mechanism-manifest", required=True)
    parser.add_argument("--source-batch-manifest", required=True)
    parser.add_argument("--source-candidate", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--development-data")
    parser.add_argument("--development-index", type=int)
    parser.add_argument("--frozen-capability-audit")
    args = parser.parse_args()
    path = materialize(
        workspace_root=Path(args.workspace_root).resolve(),
        mechanism_manifest=Path(args.mechanism_manifest).resolve(),
        source_batch_manifest=Path(args.source_batch_manifest).resolve(),
        source_candidate=args.source_candidate,
        output_dir=Path(args.output_dir).resolve(),
        development_data=(
            Path(args.development_data).resolve() if args.development_data else None
        ),
        development_index=args.development_index,
        frozen_capability_audit=(
            Path(args.frozen_capability_audit).resolve()
            if args.frozen_capability_audit else None
        ),
    )
    print(f"Reusable restricted-prime batch: {path.resolve()}")
    print("Candidate is archived only; it has not been installed or routed.")


if __name__ == "__main__":
    main()
