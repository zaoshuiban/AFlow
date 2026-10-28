# -*- coding: utf-8 -*-
"""
实验B：扩展算子 [+ AnswerExtractor, ReviewRevise, MultiPathReasoner, CodeVerifyRescue]
→ N轮优化 → score_E
"""
import json
import os
import shutil
import glob
import time
import multiprocessing
from datetime import datetime

MODEL_NAME = "deepseek-chat"
MAX_ROUNDS = 10
VALIDATION_ROUNDS = 1
SAMPLE = 4

WORKFLOWS_DIR = "workspace/GSM8K/workflows"
TEMPLATE_DIR = f"{WORKFLOWS_DIR}/template"
RESULTS_BASE = "workspace/GSM8K/experiment_ablation"

EXTENDED_OPERATORS = ["Custom", "ScEnsemble", "Programmer",
                      "AnswerExtractor", "ReviewRevise",
                      "MultiPathReasoner", "CodeVerifyRescue"]

SEED_GRAPH_PY = '''from typing import Literal
import workspace.GSM8K.workflows.template.operator as operator
import workspace.GSM8K.workflows.round_1.prompt as prompt_custom
from scripts.async_llm import create_llm_instance

from scripts.evaluator import DatasetType

class Workflow:
    def __init__(
        self,
        name: str,
        llm_config,
        dataset: DatasetType,
    ) -> None:
        self.name = name
        self.dataset = dataset
        self.llm = create_llm_instance(llm_config)
        self.custom = operator.Custom(self.llm)

    async def __call__(self, problem: str):
        """
        Implementation of the workflow
        """
        solution = await self.custom(input=problem, instruction="")
        return solution['response'], self.llm.get_usage_summary()["total_cost"]
'''

SEED_PROMPT_PY = '''# XXX_PROMPT = """
#
# Solve it.
#
# """
'''

SEED_INIT_PY = ''


def main():
    multiprocessing.set_start_method("fork", force=True)

    print("=" * 60)
    print("  实验B: 扩展算子 [Custom, ScEnsemble, Programmer,")
    print("           AnswerExtractor, ReviewRevise, MultiPathReasoner, CodeVerifyRescue]")
    print("=" * 60)
    print(f"时间: {datetime.now().isoformat()}")
    print(f"配置: model={MODEL_NAME}, max_rounds={MAX_ROUNDS}, sample={SAMPLE}\n")

    # 验证模板
    with open(f"{TEMPLATE_DIR}/operator.json", 'r') as f:
        ops = json.load(f)
    print(f"模板算子: {list(ops.keys())}")

    if len(ops) != 7:
        print("ERROR: 模板不是扩展版！请先设置扩展算子模板。")
        return

    # 清理工作区
    print("\n清理工作区...")
    for d in glob.glob(f"{WORKFLOWS_DIR}/round_*"):
        rn = os.path.basename(d).split('_')[1]
        if rn == '1':
            # 重建 round_1
            shutil.rmtree(d)
        else:
            shutil.rmtree(d)

    for f in ['results.json', 'processed_experience.json']:
        p = f"{WORKFLOWS_DIR}/{f}"
        if os.path.exists(p):
            os.remove(p)

    for d in glob.glob(f"{WORKFLOWS_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)

    # 重建 round_1 种子
    round_dir = f"{WORKFLOWS_DIR}/round_1"
    os.makedirs(round_dir, exist_ok=True)
    with open(f"{round_dir}/graph.py", 'w', encoding='utf-8') as f:
        f.write(SEED_GRAPH_PY)
    with open(f"{round_dir}/prompt.py", 'w', encoding='utf-8') as f:
        f.write(SEED_PROMPT_PY)
    with open(f"{round_dir}/__init__.py", 'w', encoding='utf-8') as f:
        f.write(SEED_INIT_PY)

    print("工作区已清理，round_1 种子已重建。")

    # 运行实验
    from scripts.optimizer import Optimizer
    from scripts.async_llm import LLMsConfig

    mc = LLMsConfig.default()
    cfg = mc.get(MODEL_NAME)
    if cfg is None:
        print(f"ERROR: 模型 '{MODEL_NAME}' 未找到！")
        return

    optimizer = Optimizer(
        dataset="GSM8K",
        question_type="math",
        opt_llm_config=cfg,
        exec_llm_config=cfg,
        check_convergence=False,
        operators=EXTENDED_OPERATORS,
        optimized_path="workspace",
        sample=SAMPLE,
        initial_round=1,
        max_rounds=MAX_ROUNDS,
        validation_rounds=VALIDATION_ROUNDS,
    )

    print(f"\n开始运行实验B ({MAX_ROUNDS}轮优化)...")
    start_time = time.time()

    try:
        optimizer.optimize("Graph")
        success = True
    except Exception as e:
        print(f"实验出错: {e}")
        import traceback
        traceback.print_exc()
        success = False

    elapsed = time.time() - start_time
    print(f"\n实验耗时: {elapsed:.1f}s")

    # 读取结果
    results_path = f"{WORKFLOWS_DIR}/results.json"
    if os.path.exists(results_path):
        with open(results_path, 'r') as f:
            results = json.load(f)
    else:
        results = []

    # 分析结果
    scores = [r["score"] for r in results if r.get("score") is not None and r["score"] > 0]
    valid_rounds = [r for r in results if r.get("score") is not None and r["score"] > 0]

    if scores:
        best_score = max(scores)
        best_round_idx = scores.index(best_score)
        best_round = valid_rounds[best_round_idx]["round"]
    else:
        best_score = 0
        best_round = "N/A"

    print(f"\n{'='*60}")
    print(f"  实验B 结果")
    print(f"{'='*60}")
    print(f"总轮次: {len(results)}")
    print(f"有效轮次: {len(valid_rounds)}")
    print(f"最佳分数: {best_score:.4f} (Round {best_round})")
    print(f"各轮分数: {[r.get('score', 0) for r in results]}")

    # 保存到实验目录
    exp_dir = f"{RESULTS_BASE}/expB_extended"
    if os.path.exists(exp_dir):
        shutil.rmtree(exp_dir)
    os.makedirs(exp_dir, exist_ok=True)

    # 复制结果
    if os.path.exists(results_path):
        shutil.copy2(results_path, f"{exp_dir}/results.json")
    if os.path.exists(f"{WORKFLOWS_DIR}/processed_experience.json"):
        shutil.copy2(f"{WORKFLOWS_DIR}/processed_experience.json", f"{exp_dir}/processed_experience.json")

    # 保存工作流快照
    workflows_snapshot = f"{exp_dir}/workflows"
    if os.path.exists(workflows_snapshot):
        shutil.rmtree(workflows_snapshot)
    shutil.copytree(WORKFLOWS_DIR, workflows_snapshot)

    # 模板快照
    template_snapshot = f"{exp_dir}/template_snapshot"
    if os.path.exists(template_snapshot):
        shutil.rmtree(template_snapshot)
    shutil.copytree(TEMPLATE_DIR, template_snapshot)

    # 保存分析
    analysis = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "model": MODEL_NAME,
            "max_rounds": MAX_ROUNDS,
            "sample": SAMPLE,
            "operators": EXTENDED_OPERATORS,
        },
        "total_rounds": len(results),
        "valid_rounds": len(valid_rounds),
        "best_score": best_score,
        "best_round": best_round,
        "all_scores": [r.get("score", 0) for r in results],
        "time_seconds": elapsed,
        "success": success,
    }

    with open(f"{exp_dir}/analysis.json", 'w', encoding='utf-8') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    # 生成对比
    expA_results_path = f"{RESULTS_BASE}/expA_original/results.json"
    if os.path.exists(expA_results_path):
        with open(expA_results_path, 'r') as f:
            expA_results = json.load(f)
        expA_scores = [r["score"] for r in expA_results if r.get("score") and r["score"] > 0]
        expA_best = max(expA_scores) if expA_scores else 0

        delta = best_score - expA_best
        print(f"\n{'='*60}")
        print(f"  对比: 实验A vs 实验B")
        print(f"{'='*60}")
        print(f"实验A (原始3算子) 最佳: {expA_best:.4f}")
        print(f"实验B (扩展7算子) 最佳: {best_score:.4f}")
        print(f"Δ = {delta:+.4f}")

        if delta > 0.01:
            conclusion = f"扩展算子库显著提升了搜索空间上限 (Δ={delta:+.4f})"
        elif delta > 0:
            conclusion = f"扩展算子库轻微提升了搜索空间上限 (Δ={delta:+.4f})"
        elif delta < -0.01:
            conclusion = f"扩展算子库反而降低了优化效果 (Δ={delta:+.4f})，更大搜索空间增加了优化难度"
        else:
            conclusion = f"扩展算子库对搜索空间上限无显著影响 (Δ={delta:+.4f})"
        print(f"\n结论: {conclusion}")

        # 保存完整对比报告
        report = {
            "timestamp": datetime.now().isoformat(),
            "experiment_a": {
                "operators": ["Custom", "ScEnsemble", "Programmer"],
                "best_score": expA_best,
                "all_scores": [r.get("score", 0) for r in expA_results],
            },
            "experiment_b": {
                "operators": EXTENDED_OPERATORS,
                "best_score": best_score,
                "all_scores": [r.get("score", 0) for r in results],
            },
            "comparison": {
                "best_score_delta": delta,
                "conclusion": conclusion,
            }
        }
        with open(f"{RESULTS_BASE}/comparison_report.json", 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n完整报告: {RESULTS_BASE}/comparison_report.json")

    print(f"\n实验B数据保存到: {exp_dir}")


if __name__ == "__main__":
    main()
