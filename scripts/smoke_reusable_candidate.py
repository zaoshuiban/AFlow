"""No-LLM plumbing smoke test for one archived reusable candidate."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from benchmarks.math import MATHBenchmark
from scripts.operator_library import OperatorLibrary
from scripts.operator_tournament import OperatorTournament
from scripts.trajectory_failure_expander import (
    load_bundle_manifest,
    load_bundle_split,
)


async def smoke(
    *, workspace_root: Path, mechanism_manifest: Path, batch_manifest: Path,
    output_dir: Path, count: int,
) -> Dict[str, Any]:
    if count <= 0:
        raise ValueError("smoke count must be positive")
    if output_dir.exists():
        raise FileExistsError(f"smoke output already exists: {output_dir}")
    family = load_bundle_manifest(mechanism_manifest)
    batch = json.loads(batch_manifest.read_text(encoding="utf-8"))
    if batch.get("status") != "pending_tournament":
        raise ValueError("candidate batch is not pending tournament")
    expected = batch.get("batch_context", {}).get("mechanism_family", {})
    if expected.get("manifest_sha256") != family["_manifest_sha256"]:
        raise ValueError("candidate batch and mechanism family do not match")
    names = list(batch.get("candidate_names", []))
    if len(names) != 1:
        raise ValueError("reusable smoke expects exactly one candidate")
    candidate = names[0]
    record = OperatorLibrary(
        workspace_root / "operator_library" / "operators.json", auto_create=False
    ).get(candidate)
    if not record or record.get("status") != "candidate":
        raise ValueError("archived candidate record is unavailable")
    if record.get("artifact", {}).get("artifact_sha256") != batch.get(
        "candidate_artifact_sha256"
    ):
        raise ValueError("candidate artifact changed after materialization")

    # Visibility boundary: only the design split is opened here.
    rows = load_bundle_split(family, "design")[:count]
    if len(rows) != count:
        raise ValueError("smoke count exceeds visible design rows")
    runner = OperatorTournament(
        library_path=workspace_root / "operator_library" / "operators.json",
        template_dir=workspace_root / "workflows" / "template",
        llm_config=None,
    )
    factory = runner._load_candidate_factory(candidate)
    operator = factory(None)
    scorer = MATHBenchmark("MATH", "", "")
    details = []
    for row in rows:
        metadata = row["mechanism_metadata"]
        try:
            result = await operator(str(row["problem"]))
            prediction = str(result.get("response", ""))
            status = str(result.get("status", ""))
            error = ""
            trace = result.get("tool_trace", {})
        except Exception as exc:
            prediction = ""
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            trace = {}
        score, extracted = scorer.calculate_score(str(row["solution"]), prediction)
        details.append(
            {
                "problem_id": metadata["problem_id"],
                "candidate": candidate,
                "status": status,
                "score": int(score),
                "extracted": extracted,
                "error": error,
                "tool_trace": trace,
            }
        )
    output_dir.mkdir(parents=True)
    details_path = output_dir / "smoke_details.jsonl"
    details_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in details),
        encoding="utf-8",
    )
    summary = {
        "schema_version": 1,
        "experiment_role": "visible_design_no_llm_plumbing_smoke",
        "scientific_evidence": False,
        "hidden_splits_opened": False,
        "llm_invocations": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate": candidate,
        "correct": sum(row["score"] for row in details),
        "total": len(details),
        "errors": sum(bool(row["error"]) for row in details),
        "details": str(details_path.resolve()),
    }
    summary_path = output_dir / "smoke_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {**summary, "summary": str(summary_path.resolve())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default="workspace/MATH")
    parser.add_argument("--mechanism-manifest", required=True)
    parser.add_argument("--batch-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--count", type=int, default=2)
    args = parser.parse_args()
    report = asyncio.run(
        smoke(
            workspace_root=Path(args.workspace_root).resolve(),
            mechanism_manifest=Path(args.mechanism_manifest).resolve(),
            batch_manifest=Path(args.batch_manifest).resolve(),
            output_dir=Path(args.output_dir).resolve(),
            count=args.count,
        )
    )
    print(f"Reusable candidate smoke summary: {report['summary']}")
    print(
        f"{report['candidate']}: correct={report['correct']}/{report['total']}, "
        f"errors={report['errors']}, LLM calls={report['llm_invocations']}"
    )
    print("Smoke uses visible design rows only and is not scientific evidence.")


if __name__ == "__main__":
    main()
