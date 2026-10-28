# -*- coding: utf-8 -*-
"""Controlled MATH ablation for the net contribution of evolved operators.

The historical AFlow best graph in this workspace is 3x Custom -> ScEnsemble.
This experiment keeps the graph fixed and compares it with equal-budget and
two-stage controls before attributing any gain to an evolved operator.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any

import pandas as pd

from benchmarks.math import MATHBenchmark
from scripts.async_llm import LLMsConfig, create_llm_instance


SOLVE_PROMPT = (
    "Solve the given math problem step by step. Provide a clear, logical "
    "solution with the final answer in \\boxed{} format. Ensure the answer "
    "is accurate and complete. Double-check your reasoning against the "
    "problem conditions before finalizing.\n"
)
GENERIC_ANALYSIS_PROMPT = (
    "Analyze the mathematical problem. Identify its type, constraints, useful "
    "theorems, and a reliable solution plan. Do not guess the final answer.\n"
)
GENERIC_SOLVE_PROMPT = (
    "Use the supplied analysis to solve the problem carefully. Verify the "
    "reasoning and put the final answer in \\boxed{}.\n"
)


CONFIGS = {
    "aflow_3custom": {
        "label": "Original AFlow best graph: 3xCustom -> ScEnsemble",
        "kind": "custom",
        "custom_count": 3,
    },
    "equal_budget_4custom": {
        "label": "Equal solver-call budget: 4xCustom -> ScEnsemble",
        "kind": "custom",
        "custom_count": 4,
    },
    "generic_two_stage": {
        "label": "2xCustom + generic two-stage -> ScEnsemble",
        "kind": "generic_two_stage",
        "custom_count": 2,
    },
    "evolved_geo": {
        "label": "2xCustom + GeometricCombinatorialDecomposer -> ScEnsemble",
        "kind": "evolved_geo",
        "custom_count": 2,
    },
    "evolved_combo": {
        "label": "2xCustom + CombinatorialSymmetryDecomposer -> ScEnsemble",
        "kind": "evolved_combo",
        "custom_count": 2,
    },
}


class FixedWorkflow:
    def __init__(self, config_name: str, llm_config: Any):
        from workspace.MATH.workflows.template.operator import (
            CombinatorialSymmetryDecomposer,
            Custom,
            GeometricCombinatorialDecomposer,
            ScEnsemble,
        )

        self.spec = CONFIGS[config_name]
        self.llm = create_llm_instance(llm_config)
        self.custom = Custom(self.llm)
        self.ensemble = ScEnsemble(self.llm)
        self.geo = GeometricCombinatorialDecomposer(self.llm)
        self.combo = CombinatorialSymmetryDecomposer(self.llm)

    async def __call__(self, problem: str) -> tuple[str, float]:
        solutions = []
        for _ in range(self.spec["custom_count"]):
            result = await self.custom(input=problem, instruction=SOLVE_PROMPT)
            solutions.append(result.get("response", ""))

        kind = self.spec["kind"]
        if kind == "generic_two_stage":
            analysis = await self.custom(
                input=problem, instruction=GENERIC_ANALYSIS_PROMPT
            )
            second_input = (
                f"Problem:\n{problem}\n\nAnalysis:\n"
                f"{analysis.get('response', '')}"
            )
            result = await self.custom(
                input=second_input, instruction=GENERIC_SOLVE_PROMPT
            )
            solutions.append(result.get("response", ""))
        elif kind == "evolved_geo":
            result = await self.geo(problem=problem)
            solutions.append(result.get("response", ""))
        elif kind == "evolved_combo":
            result = await self.combo(problem=problem)
            solutions.append(result.get("response", ""))

        selected = await self.ensemble(solutions=solutions, problem=problem)
        return (
            selected.get("response", ""),
            self.llm.get_usage_summary().get("total_cost", 0.0),
        )


def exact_mcnemar_p(wins: int, losses: int) -> float:
    """Two-sided exact McNemar/binomial p-value."""
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, k) for k in range(min(wins, losses) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


async def evaluate_config(
    config_name: str,
    run_index: int,
    data: list[dict[str, Any]],
    benchmark: MATHBenchmark,
    llm_config: Any,
    concurrency: int,
    timeout: float,
    output_root: str,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_one(index: int, item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            workflow = FixedWorkflow(config_name, llm_config)
            try:
                prediction, cost = await asyncio.wait_for(
                    workflow(item["problem"]), timeout=timeout
                )
                score, extracted = benchmark.calculate_score(
                    item["solution"], prediction
                )
                error = ""
            except Exception as exc:
                prediction, cost, score, extracted = "", 0.0, 0, ""
                error = f"{type(exc).__name__}: {exc}"
            return {
                "index": index,
                "type": item.get("type", "unknown"),
                "level": item.get("level", "unknown"),
                "score": int(score),
                "prediction": prediction,
                "extracted": extracted,
                "cost": float(cost),
                "error": error,
            }

    rows = await asyncio.gather(
        *(evaluate_one(i, item) for i, item in enumerate(data))
    )
    score = statistics.mean(row["score"] for row in rows)
    total_cost = sum(row["cost"] for row in rows)
    errors = sum(bool(row["error"]) for row in rows)

    type_scores: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        type_scores[row["type"]].append(row["score"])
    by_type = {
        key: {
            "accuracy": statistics.mean(values),
            "correct": sum(values),
            "total": len(values),
        }
        for key, values in sorted(type_scores.items())
    }

    run_dir = os.path.join(output_root, config_name)
    os.makedirs(run_dir, exist_ok=True)
    csv_path = os.path.join(run_dir, f"run_{run_index + 1}.csv")
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    print(
        f"{config_name} run={run_index + 1}: "
        f"accuracy={score:.5f}, cost={total_cost:.4f}, errors={errors}"
    )
    return {
        "config": config_name,
        "run": run_index + 1,
        "score": score,
        "cost": total_cost,
        "errors": errors,
        "by_type": by_type,
        "rows": rows,
        "csv": csv_path,
    }


def paired_comparison(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    base_rows = {row["index"]: row for row in baseline["rows"]}
    cand_rows = {row["index"]: row for row in candidate["rows"]}
    common = sorted(set(base_rows) & set(cand_rows))
    wins = sum(
        base_rows[i]["score"] == 0 and cand_rows[i]["score"] == 1
        for i in common
    )
    losses = sum(
        base_rows[i]["score"] == 1 and cand_rows[i]["score"] == 0
        for i in common
    )
    ties = len(common) - wins - losses
    return {
        "baseline": baseline["config"],
        "candidate": candidate["config"],
        "delta": candidate["score"] - baseline["score"],
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "mcnemar_exact_p": exact_mcnemar_p(wins, losses),
    }


async def main(args: argparse.Namespace) -> None:
    requested = args.configs or list(CONFIGS)
    invalid = [name for name in requested if name not in CONFIGS]
    if invalid:
        raise SystemExit(f"Unknown configs: {', '.join(invalid)}")

    model_config = LLMsConfig.default().get(args.model)
    if model_config is None:
        raise SystemExit(f"Model not found in config/config2.yaml: {args.model}")

    output_root = args.output_dir
    os.makedirs(output_root, exist_ok=True)
    benchmark = MATHBenchmark(
        name="MATH",
        file_path=args.data,
        log_path=os.path.join(output_root, "mismatches"),
    )
    data = await benchmark.load_data()
    if args.sample_size:
        data = data[: args.sample_size]

    print(f"Dataset={args.data}, samples={len(data)}, model={args.model}")
    print("Primary fair comparison: evolved_* vs generic_two_stage")
    print("End-to-end reference comparison: evolved_* vs aflow_3custom")

    results = []
    for run_index in range(args.num_runs):
        for config_name in requested:
            results.append(
                await evaluate_config(
                    config_name,
                    run_index,
                    data,
                    benchmark,
                    model_config,
                    args.concurrency,
                    args.timeout,
                    output_root,
                )
            )

    aggregate = {}
    for config_name in requested:
        selected = [r for r in results if r["config"] == config_name]
        scores = [r["score"] for r in selected]
        aggregate[config_name] = {
            "label": CONFIGS[config_name]["label"],
            "mean": statistics.mean(scores),
            "std": statistics.stdev(scores) if len(scores) > 1 else 0.0,
            "scores": scores,
            "mean_cost": statistics.mean(r["cost"] for r in selected),
        }

    comparisons = []
    for run_index in range(1, args.num_runs + 1):
        by_name = {
            r["config"]: r for r in results if r["run"] == run_index
        }
        for candidate_name in ("evolved_geo", "evolved_combo"):
            if candidate_name not in by_name:
                continue
            for baseline_name in ("generic_two_stage", "aflow_3custom"):
                if baseline_name in by_name:
                    comparisons.append(
                        paired_comparison(
                            by_name[baseline_name], by_name[candidate_name]
                        )
                    )

    report_path = os.path.join(output_root, "summary.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "arguments": vars(args),
                "aggregate": aggregate,
                "paired_comparisons": comparisons,
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )

    print("\nSummary")
    for name, item in aggregate.items():
        print(
            f"{name}: {item['mean']:.5f} ± {item['std']:.5f}; "
            f"mean_cost={item['mean_cost']:.4f}"
        )
    for item in comparisons:
        print(
            f"{item['candidate']} vs {item['baseline']}: "
            f"delta={item['delta']:+.5f}, "
            f"W/T/L={item['wins']}/{item['ties']}/{item['losses']}, "
            f"p={item['mcnemar_exact_p']:.4f}"
        )
    print(f"Saved: {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-runs", type=int, default=1)
    parser.add_argument("--sample-size", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--data", default="data/datasets/math_validate.jsonl")
    parser.add_argument(
        "--output-dir", default="workspace/MATH/operator_ablation_v2"
    )
    parser.add_argument("--configs", nargs="+", choices=sorted(CONFIGS))
    asyncio.run(main(parser.parse_args()))
