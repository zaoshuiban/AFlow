#!/usr/bin/env python3
"""Build a compact, auditable dataset for the AFlow visualization.

The script intentionally reads only artifacts already produced by the project:
MATH metadata, experiment summaries, per-round CSV files, and workflow source.
It does not call an LLM or rerun any experiment.
"""

from __future__ import annotations

import ast
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[2]
OUT_DIR = HERE.parents[1] / "data"
EXPERIMENT_ROOT = PROJECT_ROOT / "workspace" / "MATH" / "experiment_ablation"
QUESTION_FILE = PROJECT_ROOT / "data" / "datasets" / "math_validate.jsonl"


EXPERIMENTS = [
    {
        "id": "expA_original",
        "short": "A / 固定3算子",
        "label": "固定算子基线",
        "stage": "基线",
        "color": "#5B8FF9",
        "description": "Custom、ScEnsemble、Programmer；无验证机制。",
        "features": ["3个固定算子"],
    },
    {
        "id": "expB_extended",
        "short": "B1 / 扩展无验证",
        "label": "扩展算子，无安全网",
        "stage": "扩展",
        "color": "#F6BD16",
        "description": "扩展到6个算子，但没有AST或冒烟验证。",
        "features": ["6个固定算子", "无验证"],
    },
    {
        "id": "expB_extended_v2",
        "short": "B2 / AST",
        "label": "扩展算子 + AST验证",
        "stage": "验证",
        "color": "#61DDAA",
        "description": "加入AST语法检查与瓶颈检测。",
        "features": ["6个固定算子", "AST", "瓶颈检测"],
    },
    {
        "id": "expB_extended_v3",
        "short": "B3 / AST+冒烟",
        "label": "最优固定算子安全网",
        "stage": "验证",
        "color": "#22A06B",
        "description": "AST、冒烟测试和瓶颈检测，不使用退火保留。",
        "features": ["6个固定算子", "AST", "冒烟", "瓶颈检测"],
    },
    {
        "id": "expB_evolution_v4",
        "short": "v4 / 首次进化",
        "label": "首次闭环自动进化",
        "stage": "进化",
        "color": "#9661BC",
        "description": "瓶颈触发新算子生成，但倾向生成重复的验证型算子。",
        "features": ["自动进化", "无反模式引导"],
    },
    {
        "id": "expB_evolution_v5",
        "short": "v5 / 引导进化",
        "label": "反模式引导策略型进化",
        "stage": "进化",
        "color": "#F6903D",
        "description": "通过Prompt禁止重复验证型算子，鼓励策略型算子。",
        "features": ["自动进化", "反模式引导", "策略型算子"],
    },
    {
        "id": "expB_evolution_v6",
        "short": "v6 / 错误反馈",
        "label": "错误反馈与重试",
        "stage": "进化",
        "color": "#E86452",
        "description": "将冒烟失败反馈给LLM并重试；可视化使用有原始CSV的第一阶段。",
        "features": ["自动进化", "错误反馈", "重试"],
    },
]


# Event round numbers are curated from the experiment report and cross-checked
# against missing per-round CSVs. They are narrative annotations, not scores.
CURATED_EVENTS = {
    "expB_extended": [
        (3, "rejection", "生成失败"), (4, "rejection", "生成失败"),
        (5, "rejection", "生成失败"), (6, "rejection", "生成失败"),
    ],
    "expB_extended_v2": [(4, "rejection", "唯一零分轮")],
    "expB_evolution_v4": [
        (9, "rejection", "冒烟拒绝"), (12, "rejection", "冒烟拒绝"),
        (13, "evolution", "生成 AlgebraicSanityCheck"),
        (14, "evolution", "尝试几何约束算子"),
    ],
    "expB_evolution_v5": [
        (2, "rejection", "冒烟拒绝"), (4, "rejection", "冒烟拒绝"),
        (6, "rejection", "冒烟拒绝"), (7, "evolution", "三角策略算子"),
        (9, "rejection", "冒烟拒绝"), (10, "rejection", "冒烟拒绝"),
        (11, "rejection", "冒烟拒绝"), (12, "evolution", "三角变换算子"),
        (13, "evolution", "代数变换算子"), (15, "evolution", "GeometricDecomposer"),
    ],
    "expB_evolution_v6": [
        (2, "rejection", "冒烟拒绝"),
        (9, "evolution", "TrigonometricIdentitySolver"),
    ],
}


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def load_questions():
    questions = []
    lookup = {}
    with QUESTION_FILE.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            item = json.loads(line)
            qid = f"q{index + 1:03d}"
            level_match = re.search(r"(\d+)", item.get("level", ""))
            record = {
                "id": qid,
                "index": index + 1,
                "type": item.get("type", "Unknown"),
                "level": int(level_match.group(1)) if level_match else None,
                "problem": item.get("problem", ""),
                "solution": item.get("solution", ""),
            }
            questions.append(record)
            lookup[normalize_question(record["problem"])] = qid
    return questions, lookup


def operator_trace(graph_path: Path):
    if not graph_path.exists():
        return {"operators": [], "sequence": [], "source": None}
    source = graph_path.read_text(encoding="utf-8")
    attr_to_class = {}
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            call = node.value
            if not (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "operator"
            ):
                continue
            attr_to_class[target.attr] = call.func.attr
    except SyntaxError:
        pass

    sequence = []
    for match in re.finditer(r"await\s+self\.([A-Za-z_]\w*)\s*\(", source):
        attr = match.group(1)
        if attr in attr_to_class:
            sequence.append(attr_to_class[attr])
    operators = list(dict.fromkeys(attr_to_class.values()))
    return {
        "operators": operators,
        "sequence": sequence or operators,
        "counts": dict(Counter(sequence or operators)),
        "source": str(graph_path.relative_to(PROJECT_ROOT)),
    }


def select_round_csv(round_dir: Path, expected_score: float | None):
    candidates = list(round_dir.glob("*.csv"))
    if not candidates:
        return None
    if expected_score is None:
        return sorted(candidates)[-1]

    def distance(path: Path):
        match = re.match(r"([0-9.]+)_", path.name)
        value = float(match.group(1)) if match else -1
        return (abs(value - expected_score), -path.stat().st_mtime)

    return min(candidates, key=distance)


def load_outcomes(csv_path: Path, question_lookup: dict[str, str]):
    outcomes = []
    unmatched = []
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            question = row.get("question", "")
            qid = question_lookup.get(normalize_question(question))
            if not qid:
                unmatched.append(index + 1)
                qid = f"unmatched-{index + 1}"
            try:
                score = float(row.get("score", 0) or 0)
            except ValueError:
                score = 0.0
            outcomes.append({
                "q": qid,
                "score": score,
                "prediction": row.get("prediction", ""),
            })
    return outcomes, unmatched


def build_experiment(meta, question_lookup):
    exp_dir = EXPERIMENT_ROOT / meta["id"]
    results = read_json(exp_dir / "results.json", [])
    analysis = read_json(exp_dir / "analysis.json", {})
    result_by_round = {int(item["round"]): item for item in results if "round" in item}

    # Preserve explicit rejected rounds recorded in bottleneck history.
    timeline = dict(result_by_round)
    for item in analysis.get("bottleneck_history", []) or []:
        round_no = int(item["round"])
        timeline.setdefault(round_no, {"round": round_no, "score": item.get("score", 0.0)})

    events = [
        {"round": r, "kind": kind, "label": label}
        for r, kind, label in CURATED_EVENTS.get(meta["id"], [])
    ]
    events_by_round = {}
    for event in events:
        events_by_round.setdefault(event["round"], []).append(event)

    rounds = []
    unmatched_total = []
    score_mismatches = []
    for round_no, item in sorted(timeline.items()):
        expected_score = float(item.get("score", 0) or 0)
        round_dir = exp_dir / "workflows" / f"round_{round_no}"
        csv_path = select_round_csv(round_dir, expected_score)
        outcomes = []
        unmatched = []
        csv_score = None
        if csv_path:
            outcomes, unmatched = load_outcomes(csv_path, question_lookup)
            csv_score = sum(row["score"] for row in outcomes) / len(outcomes) if outcomes else None
            if csv_score is not None and abs(csv_score - expected_score) > 1e-6:
                score_mismatches.append({
                    "round": round_no,
                    "result_score": expected_score,
                    "csv_score": csv_score,
                    "file": str(csv_path.relative_to(PROJECT_ROOT)),
                })
        unmatched_total.extend({"round": round_no, "row": row} for row in unmatched)
        trace = operator_trace(round_dir / "graph.py")
        status = "evaluated" if csv_path else ("rejected" if expected_score == 0 else "summary-only")
        rounds.append({
            "round": round_no,
            "score": round(expected_score, 6),
            "status": status,
            "time": item.get("time"),
            "csv": str(csv_path.relative_to(PROJECT_ROOT)) if csv_path else None,
            "csv_score": round(csv_score, 6) if csv_score is not None else None,
            "operators": trace["operators"],
            "sequence": trace["sequence"],
            "operator_counts": trace.get("counts", {}),
            "graph_source": trace["source"],
            "events": events_by_round.get(round_no, []),
            "outcomes": outcomes,
        })

    valid = [row for row in rounds if row["status"] == "evaluated"]
    best = max(valid, key=lambda row: row["score"], default=None)
    evolution = analysis.get("evolution_history", []) or []
    validation_stats = analysis.get("validation_stats", {}) or {}
    return {
        **meta,
        "rounds": rounds,
        "events": events,
        "evolution": evolution,
        "final_operators": analysis.get("final_operators", []) or [],
        "validation_stats": validation_stats,
        "summary": {
            "timeline_rounds": len(rounds),
            "evaluated_rounds": len(valid),
            "rejected_rounds": sum(1 for row in rounds if row["status"] == "rejected"),
            "best_score": round(best["score"], 6) if best else None,
            "best_round": best["round"] if best else None,
            "evolved_operators": len(evolution),
        },
        "audit": {
            "unmatched_rows": unmatched_total,
            "score_mismatches": score_mismatches,
        },
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    questions, lookup = load_questions()
    experiments = [build_experiment(meta, lookup) for meta in EXPERIMENTS]

    types = sorted({item["type"] for item in questions})
    levels = sorted({item["level"] for item in questions if item["level"] is not None})
    data = {
        "meta": {
            "title": "更多算子不一定更聪明",
            "subtitle": "AFlow 工作流自动进化的收益、失败与瓶颈",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": "MATH validation",
            "question_count": len(questions),
            "types": types,
            "levels": levels,
            "score_unit": "accuracy",
            "source_policy": "results.json 定轮次与总分；CSV 定逐题结果；graph.py 定算子；analysis.json 定进化摘要。",
        },
        "questions": questions,
        "experiments": experiments,
    }
    audit = {
        "question_count": len(questions),
        "experiment_count": len(experiments),
        "round_count": sum(len(exp["rounds"]) for exp in experiments),
        "evaluated_round_count": sum(exp["summary"]["evaluated_rounds"] for exp in experiments),
        "unmatched_outcome_rows": sum(len(exp["audit"]["unmatched_rows"]) for exp in experiments),
        "score_mismatches": [
            {"experiment": exp["id"], **item}
            for exp in experiments for item in exp["audit"]["score_mismatches"]
        ],
        "notes": [
            "v5/v6 的有效轮数组不连续，统一保留原始 round 字段。",
            "没有逐题CSV的零分轮标记为 rejected，不参与题型/难度统计。",
            "v6 续跑只有汇总分析、没有对应逐题CSV，因此交互主图使用第一阶段原始数据。",
        ],
    }
    (OUT_DIR / "dashboard.json").write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (OUT_DIR / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
