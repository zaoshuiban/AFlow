# -*- coding: utf-8 -*-
"""Re-score historical MATH mismatch logs with the current scorer."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.math import MATHBenchmark
from scripts.operator_library import stable_problem_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def correction_reason(
    benchmark: MATHBenchmark,
    *,
    expected: str,
    prediction: str,
    old_extracted: str,
    new_extracted: str,
) -> str:
    expected_answer = benchmark.extract_model_answer(expected)
    expected_boxes = benchmark.extract_boxed_answers(expected)
    prediction_boxes = benchmark.extract_boxed_answers(prediction)
    combined = f"{expected_answer}\n{new_extracted}"
    if old_extracted.strip() != new_extracted.strip():
        return "nested_box_extraction"
    if "\\begin{" in combined and "matrix}" in combined:
        return "matrix_equivalence"
    if len(expected_boxes) > 1 or (
        len(prediction_boxes) == 1
        and len(benchmark._split_top_level_answers(new_extracted)) > 1
    ):
        return "unordered_multi_answer"
    if re.search(
        r"\\(?:text|mathrm)\{[^{}]*(?:units?|meters?|feet|inches?|"
        r"cents?|dollars?|square|cubic)[^{}]*\}|\^?\{?\\circ\}?",
        combined,
        re.I,
    ):
        return "terminal_unit_normalization"
    if re.search(r"\\(?:text|mathrm|operatorname)\{", combined):
        return "text_wrapper_normalization"
    if "\\frac" in combined or "\\dfrac" in combined or re.search(
        r"\d+\s*/\s*\d+", combined
    ):
        return "fraction_numeric_equivalence"
    if re.search(r"\\(?:left|right|quad|qquad|[,;!])", combined):
        return "latex_spacing_normalization"
    return "other_semantic_equivalence"


def _round_number(path: Path) -> int:
    match = re.search(r"round_(\d+)", str(path))
    return int(match.group(1)) if match else -1


def _preview(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value).split()).replace("|", r"\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _aggregate_events(
    events: Iterable[Dict[str, Any]], data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[int(event["index"])].append(event)
    output = []
    for index, items in grouped.items():
        item = data[index]
        latest = max(items, key=lambda row: (row["round"], row["log_path"]))
        output.append(
            {
                "index": index,
                "problem_id": stable_problem_id(item["problem"]),
                "type": item.get("type", "unknown"),
                "level": item.get("level", "unknown"),
                "problem": item["problem"],
                "occurrences": len(items),
                "rounds": sorted({row["round"] for row in items}),
                "latest_prediction": latest["prediction"],
                "latest_extracted": latest["new_extracted"],
                "reference_extracted": latest["reference_extracted"],
                "reasons": dict(Counter(row["reason"] for row in items)),
            }
        )
    return sorted(output, key=lambda row: (-row["occurrences"], row["index"]))


def build_report(
    *,
    dataset_path: Path,
    workflows_dir: Path,
) -> Dict[str, Any]:
    data = load_jsonl(dataset_path)
    problem_to_index = {item["problem"]: index for index, item in enumerate(data)}
    if len(problem_to_index) != len(data):
        raise ValueError("dataset contains duplicate problem text")
    benchmark = MATHBenchmark("MATH", str(dataset_path), str(workflows_dir))
    log_paths = sorted(
        workflows_dir.glob("round_*/log.json"), key=_round_number
    )
    if not log_paths:
        raise FileNotFoundError(f"no round logs under {workflows_dir}")

    corrected = []
    remaining = []
    skipped = []
    for log_path in log_paths:
        with log_path.open(encoding="utf-8") as handle:
            entries = json.load(handle)
        if isinstance(entries, dict):
            entries = [entries]
        for entry_number, entry in enumerate(entries):
            question = str(entry.get("question", ""))
            index = problem_to_index.get(question)
            if index is None:
                skipped.append(
                    {
                        "log_path": str(log_path),
                        "entry_number": entry_number,
                        "reason": "question_not_found_in_dataset",
                    }
                )
                continue
            expected = str(data[index]["solution"])
            prediction = str(entry.get("model_output", ""))
            score, new_extracted = benchmark.calculate_score(expected, prediction)
            event = {
                "index": index,
                "problem_id": stable_problem_id(question),
                "round": _round_number(log_path),
                "log_path": str(log_path.relative_to(REPO_ROOT)),
                "entry_number": entry_number,
                "old_extracted": str(entry.get("extracted_output", "")),
                "new_extracted": new_extracted,
                "reference_extracted": benchmark.extract_model_answer(expected),
                "prediction": prediction,
                "new_score": int(score),
            }
            if score:
                event["reason"] = correction_reason(
                    benchmark,
                    expected=expected,
                    prediction=prediction,
                    old_extracted=event["old_extracted"],
                    new_extracted=new_extracted,
                )
                corrected.append(event)
            else:
                event["reason"] = "remaining_semantic_mismatch"
                remaining.append(event)

    corrected_problems = _aggregate_events(corrected, data)
    remaining_problems = _aggregate_events(remaining, data)
    persistent = [row for row in remaining_problems if row["occurrences"] >= 2]
    correction_counts = Counter(row["reason"] for row in corrected)
    remaining_type_counts = Counter(row["type"] for row in remaining_problems)
    scorer_path = REPO_ROOT / "benchmarks" / "math.py"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scorer": {
            "version": benchmark.SCORER_VERSION,
            "path": str(scorer_path.relative_to(REPO_ROOT)),
            "sha256": sha256_file(scorer_path),
        },
        "dataset": {
            "path": str(dataset_path.relative_to(REPO_ROOT)),
            "sha256": sha256_file(dataset_path),
            "rows": len(data),
        },
        "logs": [
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "round": _round_number(path),
                "sha256": sha256_file(path),
            }
            for path in log_paths
        ],
        "summary": {
            "original_logged_mismatches": len(corrected) + len(remaining),
            "corrected_false_negative_events": len(corrected),
            "remaining_failure_events": len(remaining),
            "corrected_unique_problems": len(corrected_problems),
            "remaining_unique_problems": len(remaining_problems),
            "persistent_unique_problems_min_2": len(persistent),
            "correction_reason_counts": dict(sorted(correction_counts.items())),
            "remaining_problem_type_counts": dict(
                sorted(remaining_type_counts.items())
            ),
            "skipped_log_entries": len(skipped),
        },
        "corrected_false_negatives": corrected_problems,
        "remaining_failures": remaining_problems,
        "persistent_failures": persistent,
        "events": {
            "corrected": corrected,
            "remaining": remaining,
            "skipped": skipped,
        },
    }


def render_markdown(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# MATH历史失败日志清洗报告",
        "",
        f"- 评分器版本：`{report['scorer']['version']}`",
        f"- 数据集：`{report['dataset']['path']}`（{report['dataset']['rows']}题）",
        f"- 日志轮次：{len(report['logs'])}",
        f"- 原始错误事件：{summary['original_logged_mismatches']}",
        f"- 修正为假阴性：{summary['corrected_false_negative_events']}",
        f"- 仍为错误：{summary['remaining_failure_events']}",
        f"- 仍失败的不同题目：{summary['remaining_unique_problems']}",
        f"- 持续失败题（至少2次）：{summary['persistent_unique_problems_min_2']}",
        "",
        "## 假阴性修正原因",
        "",
        "| 原因 | 事件数 |",
        "|---|---:|",
    ]
    for reason, count in summary["correction_reason_counts"].items():
        lines.append(f"| `{reason}` | {count} |")

    lines.extend(
        [
            "",
            "## 清洗后仍然失败的题目",
            "",
            "下表按现有8轮日志中的失败次数排序。它们是后续失败聚类的候选，"
            "但在进入算子生成前仍应人工检查根因。",
            "",
            "| 索引 | 类型 | 难度 | 次数 | 轮次 | 最新预测 | 参考答案 | 题目 |",
            "|---:|---|---|---:|---|---|---|---|",
        ]
    )
    for row in report["remaining_failures"]:
        rounds = ",".join(str(value) for value in row["rounds"])
        lines.append(
            f"| {row['index']} | {row['type']} | {row['level']} | "
            f"{row['occurrences']} | {rounds} | "
            f"`{_preview(row['latest_extracted'])}` | "
            f"`{_preview(row['reference_extracted'])}` | "
            f"{_preview(row['problem'], 100)} |"
        )

    lines.extend(
        [
            "",
            "## 持续失败题型分布",
            "",
            "| 类型 | 不同题目数 |",
            "|---|---:|",
        ]
    )
    for kind, count in summary["remaining_problem_type_counts"].items():
        lines.append(f"| {kind} | {count} |")
    lines.extend(
        [
            "",
            "## 使用约束",
            "",
            "- 本报告只重算优化集日志，不涉及486题最终测试集。",
            "- `remaining_failures`表示当前评分器确认不等价，不自动等同于同一种能力缺口。",
            "- 算子生成应使用同一机制簇，并将设计题与锦标赛题分开。",
            "- 完整逐事件审计信息见同名JSON文件。",
            "",
        ]
    )
    return "\n".join(lines)


def resolve(path: str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else REPO_ROOT / value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/datasets/math_validate.jsonl")
    parser.add_argument("--workflows", default="workspace/MATH/workflows")
    parser.add_argument(
        "--output-json", default="results/20260809_MATH_true_failures.json"
    )
    parser.add_argument(
        "--output-md", default="results/20260809_MATH_true_failures.md"
    )
    args = parser.parse_args()
    report = build_report(
        dataset_path=resolve(args.dataset),
        workflows_dir=resolve(args.workflows),
    )
    json_path = resolve(args.output_json)
    markdown_path = resolve(args.output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")


if __name__ == "__main__":
    main()
