# -*- coding: utf-8 -*-
"""
MATH v6 续跑脚本: 从 R10 继续 (initial_round=9, 这样第一轮产生 round_10)
"""

import json, os, shutil, time, multiprocessing

WORKFLOWS_DIR = "workspace/MATH/workflows"
RESULTS_BASE = "workspace/MATH/experiment_ablation"

# Load existing results to pre-populate bottleneck history
results_path = f"{WORKFLOWS_DIR}/results.json"
with open(results_path) as f:
    existing_results = json.load(f)

# Sort by round
existing_results.sort(key=lambda r: r["round"])
scores = [(r["round"], r["score"]) for r in existing_results if r.get("score", 0) > 0]
print(f"Loaded {len(scores)} existing results:")
for r, s in scores:
    print(f"  R{r}: {s:.4f}")

# Load existing operators
with open(f"{WORKFLOWS_DIR}/template/operator.json") as f:
    ops = json.load(f)
current_operators = list(ops.keys())
print(f"Operators ({len(current_operators)}): {current_operators}")

# Calculate how many rounds remain (last scored round was R9)
last_scored = max(r for r, s in scores)
remaining_rounds = 15 - last_scored
print(f"Last scored round: {last_scored}, remaining: {remaining_rounds}")

MODEL = "deepseek-chat"
SAMPLE = 4

print(f"\nResuming from round {last_scored+1} ({remaining_rounds} rounds remaining)...")
print(f"Model: {MODEL}\n")

multiprocessing.set_start_method("fork", force=True)
from scripts.optimizer import Optimizer
from scripts.async_llm import LLMsConfig

mc = LLMsConfig.default()
cfg = mc.get(MODEL)

optimizer = Optimizer(
    dataset="MATH",
    question_type="math",
    opt_llm_config=cfg,
    exec_llm_config=cfg,
    check_convergence=False,
    operators=current_operators,
    optimized_path="workspace",
    sample=SAMPLE,
    initial_round=last_scored,   # start counting from last scored round
    max_rounds=remaining_rounds,
    validation_rounds=1,
    enable_code_validation=True,
    enable_bottleneck_detection=True,
    enable_smoke_test=True,
    max_validation_retries=3,
    smoke_test_timeout=30.0,
    enable_operator_evolution=True,
    enable_prompt_guidance=True,
    bottleneck_window=2,
    bottleneck_threshold=0.005,
)

# Pre-populate bottleneck detector with existing scores
for r, s in scores:
    optimizer.bottleneck_detector.update(r, s)

# Pre-populate evolution state
optimizer._evolution_triggered_count = 2  # already had 2 evolutions
optimizer._evolution_cooldown = 0  # reset cooldown to allow new evolutions
optimizer._last_evolved_operator = "GeometricDecomposer"

start_time = time.time()
try:
    optimizer.optimize("Graph")
    success = True
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    success = False

elapsed = time.time() - start_time
print(f"\nExperiment time: {elapsed:.1f}s")

# Merge results
with open(results_path) as f:
    all_results = json.load(f)

valid = [r for r in all_results if r.get("score", 0) > 0]
best = max(valid, key=lambda r: r["score"]) if valid else None
print(f"\nBest: {best['score']:.4f} (R{best['round']})" if best else "No valid results")
print(f"Valid rounds: {len(valid)}")
print(f"Zero-score: {sum(1 for r in all_results if r.get('score', 0) == 0)}")

# Save analysis
analysis = {
    "total_rounds": len(all_results),
    "valid_rounds": len(valid),
    "best_score": best["score"] if best else 0,
    "best_round": best["round"] if best else 0,
    "all_scores": [(r["round"], r["score"]) for r in all_results],
    "time_seconds": elapsed,
    "success": success,
    "evolution_triggered": optimizer._evolution_triggered_count,
    "smoke_rejections": optimizer._smoke_test_rejections,
    "ast_rejections": optimizer._total_validation_rejections,
    "final_operators": optimizer.operators,
}
if optimizer.operator_evolver:
    analysis["evolution_history"] = optimizer.operator_evolver.evolution_history

os.makedirs(f"{RESULTS_BASE}/expB_evolution_v6_resume", exist_ok=True)
with open(f"{RESULTS_BASE}/expB_evolution_v6_resume/analysis.json", "w") as f:
    json.dump(analysis, f, indent=2, ensure_ascii=False)
print(f"\nSaved to {RESULTS_BASE}/expB_evolution_v6_resume/")
