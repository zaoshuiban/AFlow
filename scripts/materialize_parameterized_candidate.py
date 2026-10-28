"""Materialize a reusable tool from a frozen auto-generated design."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from benchmarks.math import MATHBenchmark
from scripts.operator_library import OperatorLibrary
from scripts.parameterized_tools import PARENTHESIZATION_MECHANISM, ParameterizedToolRegistry
from scripts.trajectory_failure_expander import (
    file_sha256,
    load_bundle_manifest,
    load_bundle_split,
)


CLASS_NAME = "ReusableParenthesizationIntervalDPTool"

CLASS_CODE = r'''class ReusableParenthesizationIntervalDPTool(Operator):
    _required_features = ("counting_problem", "algebra_problem")
    _blocked_features = (
        "geometry_problem", "probability_problem", "continuous_probability",
        "number_theory_problem", "parametric_curve", "trajectory_trace",
        "distance_traveled_request",
    )

    def __init__(self, llm: AsyncLLM, name: str = "ReusableParenthesizationIntervalDPTool"):
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
            return {
                "status": "declined", "response": "",
                "observation": "specialist_not_applicable:" + ";".join(reasons),
            }
        return None

    async def __call__(self, problem: str) -> dict:
        refusal = self._preflight(problem)
        if refusal:
            return refusal
        from scripts.parameterized_tools import (
            PARENTHESIZATION_MECHANISM, ParameterizedToolRegistry,
        )
        result = ParameterizedToolRegistry(
            [PARENTHESIZATION_MECHANISM]
        ).solve(problem)
        trace = {"route": "reusable_parameterized_tool", **result.to_dict()}
        if result.status == "success":
            return {
                "status": "success", "response": result.response,
                "observation": "", "tool_trace": trace,
            }
        return {
            "status": "declined", "response": "",
            "observation": result.observation or result.status,
            "tool_trace": trace,
        }'''


def materialize(
    *,
    workspace_root: Path,
    mechanism_manifest: Path,
    source_batch_manifest: Path,
    source_candidate: str,
) -> Path:
    family = load_bundle_manifest(mechanism_manifest)
    design_rows = load_bundle_split(family, "design")
    with source_batch_manifest.open(encoding="utf-8") as handle:
        source_batch = json.load(handle)
    if source_candidate not in source_batch.get("candidate_names", []):
        raise ValueError("source candidate is absent from the frozen source batch")
    expected_family = source_batch.get("batch_context", {}).get("mechanism_family", {})
    if expected_family.get("manifest_sha256") != family["_manifest_sha256"]:
        raise ValueError("source candidate and design family do not match")

    scorer = MATHBenchmark("MATH", "", "")
    registry = ParameterizedToolRegistry([PARENTHESIZATION_MECHANISM])
    validations = []
    for row in design_rows:
        result = registry.solve(str(row["problem"]))
        score, extracted = scorer.calculate_score(
            str(row["solution"]), result.response
        )
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
    if not validations or any(row["score"] != 1 for row in validations):
        raise RuntimeError("reusable tool failed visible design validation")

    library = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json",
        auto_create=False,
    )
    source_record = library.get(source_candidate) or {}
    source_artifact_sha = source_record.get("artifact", {}).get(
        "artifact_sha256", ""
    )
    if not source_artifact_sha:
        raise ValueError("source candidate artifact is missing")
    record = library.register_evolved_candidate(
        name=CLASS_NAME,
        description=(
            "Reusable exact interval-DP tool materialized from the frozen "
            "auto-generated parenthesization mechanism design."
        ),
        module="workspace.MATH.workflows.template.operator",
        skeleton_type="reusable_parameterized_tool",
        applicability_features="counting_problem,algebra_problem",
        failure_features=(
            "geometry_problem,probability_problem,continuous_probability,"
            "number_theory_problem,parametric_curve,trajectory_trace,"
            "distance_traveled_request"
        ),
        provenance={
            "kind": "materialized_parameterized_tool",
            "dataset": "MATH",
            "mechanism": PARENTHESIZATION_MECHANISM,
            "source_batch_id": source_batch["batch_id"],
            "source_batch_manifest": str(source_batch_manifest.resolve()),
            "source_batch_manifest_sha256": file_sha256(source_batch_manifest),
            "source_candidate": source_candidate,
            "source_candidate_artifact_sha256": source_artifact_sha,
            "transformation": "generate_design_once_then_compile_reusable_parameters_v1",
            "visible_design_only": True,
            "hidden_splits_opened": False,
        },
        class_code=CLASS_CODE,
        prompt_code="",
    )

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = workspace_root / "operator_evolution_batches" / batch_id
    output_dir.mkdir(parents=True, exist_ok=False)
    validation_path = output_dir / "visible_design_validation.json"
    validation_payload: Dict[str, Any] = {
        "scientific_evidence": False,
        "split": "visible_design",
        "candidate": CLASS_NAME,
        "correct": sum(row["score"] for row in validations),
        "total": len(validations),
        "rows": validations,
    }
    validation_path.write_text(
        json.dumps(validation_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "status": "pending_tournament",
        "dataset": "MATH",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_names": [CLASS_NAME],
        "designs": [
            {
                "operator_name": CLASS_NAME,
                "skeleton_type": "reusable_parameterized_tool",
                "mechanism": PARENTHESIZATION_MECHANISM,
                "source_candidate": source_candidate,
                "source_candidate_artifact_sha256": source_artifact_sha,
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
                "unknown_mechanism_fallback": "single_problem_programmer",
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
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--mechanism-manifest", required=True)
    parser.add_argument("--source-batch-manifest", required=True)
    parser.add_argument("--source-candidate", required=True)
    args = parser.parse_args()
    path = materialize(
        workspace_root=Path(args.workspace_root).resolve(),
        mechanism_manifest=Path(args.mechanism_manifest).resolve(),
        source_batch_manifest=Path(args.source_batch_manifest).resolve(),
        source_candidate=args.source_candidate,
    )
    print(f"Reusable parameterized batch: {path}")


if __name__ == "__main__":
    main()
