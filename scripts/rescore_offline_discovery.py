"""Re-score a frozen offline-loop discovery run without model/API calls.

The source run is treated as immutable evidence.  Results are written to a
new, non-overwritable audit directory outside the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.math import MATHBenchmark
from scripts.offline_loop_protocol import LoopConfig, atomic_json, digest, file_hash, read_json
from scripts.offline_operator_loop import select_mechanism


def tree_hashes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): file_hash(path)
            for path in sorted(path for path in root.rglob("*") if path.is_file())}


def mapping_digest(value: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def checkpoint_output(path: Path) -> dict:
    wrapper = read_json(path)
    if not isinstance(wrapper, dict) or "output" not in wrapper:
        raise ValueError(f"Malformed checkpoint: {path}")
    if digest(wrapper["output"]) != wrapper.get("output_sha256"):
        raise ValueError(f"Checkpoint output hash mismatch: {path}")
    return wrapper["output"]


def score_reason(scorer: MATHBenchmark, problem: str, reference: str,
                 prediction: str, old_score: int, new_score: int) -> tuple[str, dict]:
    expected = scorer.extract_model_answer(reference)
    predicted = scorer.extract_model_answer(prediction)
    expected_resolved, expected_label = scorer.resolve_choice_answer(expected, problem)
    predicted_resolved, predicted_label = scorer.resolve_choice_answer(predicted, problem)
    context = {
        "predicted_extracted": predicted,
        "reference_extracted": expected,
        "predicted_choice_label": predicted_label,
        "predicted_resolved": predicted_resolved if predicted_label else None,
        "reference_choice_label": expected_label,
        "reference_resolved": expected_resolved if expected_label else None,
    }
    if old_score == new_score:
        return "unchanged", context
    if predicted_label or expected_label:
        return "choice_label_mapping", context
    if re.search(r"\\boxed\s+[0-9A-Za-z](?=\s*\$)", reference + prediction):
        return "unbraced_boxed_token", context
    if r"\$" in expected + predicted:
        return "numeric_currency_prefix", context
    if "gallon" in expected + predicted:
        return "terminal_gallon_unit", context
    if re.search(r"_[0-9A-Za-z]", expected + predicted):
        return "single_token_subscript", context
    if len(scorer._answer_collection(reference)) > 1:
        return "unordered_answer_collection", context
    predicted_normalized = scorer.normalize_answer(predicted)
    expected_normalized = scorer.normalize_answer(expected)
    if (predicted_normalized != expected_normalized
            and predicted_normalized.casefold() == expected_normalized.casefold()):
        return "plain_text_case_normalization", context
    return "other_scorer_change", context


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _human_audit(summary: dict, rows: list[dict], problem_map: dict[str, dict]) -> str:
    changed = [row for row in rows if row["original_score"] != row["rescored_score"]]
    remaining_ids = summary["remaining_stable_problem_ids"]
    lines = [
        "# 冻结发现运行离线复评",
        "",
        f"- 原状态：`{summary['original_status']}`；旁路复评不修改该状态。",
        f"- 评分器：`{summary['original_scorer_version']}` → `{summary['scorer_version']}`。",
        f"- 已保存观察：{summary['counts']['observations']}；执行异常：{summary['counts']['execution_errors']}。",
        f"- 分数翻转：{summary['counts']['score_changes']}；模型/API 调用：0。",
        f"- 正确观察：{summary['counts']['original_correct']} → {summary['counts']['rescored_correct']}；"
        f"错误观察：{summary['counts']['original_wrong']} → {summary['counts']['rescored_wrong']}。",
        f"- 原运行全部 {summary['source_hashes_before']['all_run_file_count']} 个文件哈希保持不变。",
        f"- 复评后稳定失败题：{summary['rescored_screen']['stable_problem_count']}；"
        f"满足原机制门槛的分组：{'是' if summary['rescored_screen']['selected'] else '否'}。",
        "",
        "## 由评分规则修正的观察",
        "",
    ]
    if not changed:
        lines.append("无。")
    else:
        for item in changed:
            lines.append(
                f"- `{item['problem_id']}` 第 {item['run']} 次："
                f"{item['original_score']}→{item['rescored_score']}，"
                f"原因 `{item['change_reason']}`，最终答案 `{item['predicted_extracted']}`。"
            )
    lines.extend(["", "## 复评后仍稳定失败的题目", ""])
    if not remaining_ids:
        lines.append("无。")
    else:
        for problem_id in remaining_ids:
            item = problem_map[problem_id]
            lines.append(f"- `{problem_id}`：{item['problem']}")
    lines.extend([
        "", "## 解释边界", "",
        "本报告只对冻结回答重新判分，不证明剩余失败具有共同因果机制。"
        "含图题、接口异常和评分歧义应分别审计；已归档运行不能据此恢复或继续生成。",
        "",
    ])
    return "\n".join(lines)


def rescore_discovery(run_dir: str | Path, output_dir: str | Path) -> tuple[Path, dict]:
    root = Path(run_dir).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Audit output already exists: {output}")
    if output == root or root in output.parents:
        raise ValueError("Audit output must be outside the frozen run")
    if not root.is_dir():
        raise FileNotFoundError(root)

    before = tree_hashes(root)
    request_files_before = len(list((root / "requests").glob("*.json"))) if (root / "requests").is_dir() else 0
    manifest = read_json(root / "manifest.json")
    original_summary = read_json(root / "summary.json")
    original_screen = checkpoint_output(root / "checkpoints/screen.json")
    rows = read_json(root / "inputs/discovery.json")
    config = LoopConfig(**manifest["config"])
    if manifest.get("pipeline") != "aflow_offline_loop_v1":
        raise ValueError("Not an offline-loop v1 run")
    if len(rows) != manifest["counts"]["discovery"]:
        raise ValueError("Discovery pool count does not match frozen manifest")
    problem_map = {row["problem_id"]: row for row in rows}
    if len(problem_map) != len(rows):
        raise ValueError("Duplicate discovery problem IDs")

    scorer = MATHBenchmark("MATH", "", str(output))
    details = []
    observations = []
    for item in rows:
        problem_id = item["problem_id"]
        for repeat in range(1, config.discovery_runs + 1):
            source = root / "checkpoints/discovery/baseline" / problem_id / f"{repeat}.json"
            saved = checkpoint_output(source)
            error = str(saved.get("error", ""))
            original_score = int(saved["score"])
            if error:
                rescored_score, rescored_extracted = original_score, str(saved.get("extracted", ""))
                reason, context = "execution_error_excluded", {
                    "predicted_extracted": rescored_extracted,
                    "reference_extracted": scorer.extract_model_answer(item["solution"]),
                    "predicted_choice_label": None, "predicted_resolved": None,
                    "reference_choice_label": None, "reference_resolved": None,
                }
            else:
                rescored_score, rescored_extracted = scorer.calculate_score(
                    item["solution"], str(saved["prediction"]), problem=item["problem"]
                )
                reason, context = score_reason(
                    scorer, item["problem"], item["solution"], str(saved["prediction"]),
                    original_score, int(rescored_score),
                )
            detail = {
                "problem_id": problem_id, "run": repeat,
                "source_checkpoint": str(source), "source_sha256": before[str(source.relative_to(root))],
                "original_score": original_score, "rescored_score": int(rescored_score),
                "original_extracted": saved.get("extracted", ""),
                "rescored_extracted": rescored_extracted, "error": error,
                "change_reason": reason, **context,
            }
            details.append(detail)
            observations.append({**saved, "score": int(rescored_score),
                                 "problem_id": problem_id, "run": repeat})

    rescored_screen = select_mechanism(rows, observations, config)
    change_counts = Counter(row["change_reason"] for row in details
                            if row["original_score"] != row["rescored_score"])
    remaining_stable_ids = sorted({problem_id for group in rescored_screen["groups"]
                                   for problem_id in group["stable_problem_ids"]})
    source_hashes = {
        "manifest.json": before["manifest.json"],
        "summary.json": before["summary.json"],
        "inputs/discovery.json": before["inputs/discovery.json"],
        "checkpoints/screen.json": before["checkpoints/screen.json"],
        "all_run_files_digest": mapping_digest(before),
        "all_run_file_count": len(before),
    }
    report: dict[str, Any] = {
        "schema_version": 1, "report_type": "offline_discovery_rescore",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "offline_only": True, "model_calls_made": 0,
        "source_run": str(root), "source_hashes_before": source_hashes,
        "original_status": original_summary.get("status"),
        "original_scorer_version": manifest.get("runtime_environment", {}).get("scorer_version", "unknown"),
        "scorer_version": scorer.SCORER_VERSION,
        "scorer_source_sha256": file_hash(Path(__file__).resolve().parents[1] / "benchmarks/math.py"),
        "rescore_script_sha256": file_hash(Path(__file__).resolve()),
        "counts": {
            "observations": len(details),
            "original_correct": sum(row["original_score"] == 1 and not row["error"] for row in details),
            "original_wrong": sum(row["original_score"] == 0 and not row["error"] for row in details),
            "rescored_correct": sum(row["rescored_score"] == 1 and not row["error"] for row in details),
            "rescored_wrong": sum(row["rescored_score"] == 0 and not row["error"] for row in details),
            "execution_errors": sum(bool(row["error"]) for row in details),
            "score_changes": sum(row["original_score"] != row["rescored_score"] for row in details),
            "changes_by_reason": dict(change_counts),
        },
        "original_screen": original_screen, "rescored_screen": rescored_screen,
        "remaining_stable_problem_ids": remaining_stable_ids,
        "decision": (
            "new_run_may_be_justified" if rescored_screen["selected"]
            else "do_not_lower_threshold; audit remaining errors before deciding on another run"
        ),
        "limitations": [
            "saved answers only; no new model calls",
            "remaining repeatable errors are mechanism hypotheses, not causal proof",
            "the archived source run remains final and cannot be resumed under the new scorer",
        ],
    }

    if tree_hashes(root) != before:
        raise RuntimeError("Source run changed during read-only rescore")
    requests_after = len(list((root / "requests").glob("*.json"))) if (root / "requests").is_dir() else 0
    if requests_after != request_files_before:
        raise RuntimeError("API request ledger changed during offline rescore")
    report["source_tree_unchanged"] = True
    report["request_files_before_after"] = [request_files_before, requests_after]
    output.mkdir(parents=True, exist_ok=False)
    _write_jsonl(output / "details.jsonl", details)
    atomic_json(output / "summary.json", report)
    (output / "human_audit.md").write_text(
        _human_audit(report, details, problem_map), encoding="utf-8"
    )
    return output / "summary.json", report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    path, report = rescore_discovery(args.run_dir, args.output_dir)
    print(json.dumps({"summary": str(path), "counts": report["counts"],
                      "rescored_selected": report["rescored_screen"]["selected"],
                      "model_calls_made": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
