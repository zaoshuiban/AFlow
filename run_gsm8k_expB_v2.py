# -*- coding: utf-8 -*-
"""
GSM8K 实验 B v2: 扩展7算子 + 全套验证机制

对比 v1:
- v1: 无验证机制, ~60% 无效轮次
- v2: AST验证 + 冒烟测试 + 最优保留 + 瓶颈检测
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

# 使用 expB_extended 快照中的完整算子定义
SNAPSHOT_DIR = f"{RESULTS_BASE}/expB_extended/template_snapshot"


def set_extended_template():
    """从快照恢复扩展7算子模板"""
    if os.path.exists(SNAPSHOT_DIR):
        print("  从快照恢复扩展7算子模板...")
        for fname in ['operator.json', 'operator_an.py', 'op_prompt.py', 'operator.py']:
            src = os.path.join(SNAPSHOT_DIR, fname)
            dst = os.path.join(TEMPLATE_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, dst)
    else:
        print("  WARNING: 快照不存在，使用当前模板")

    for d in glob.glob(f"{TEMPLATE_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)
    for d in glob.glob(f"{WORKFLOWS_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)
    print("  已设置扩展7算子模板")


def clean_workspace():
    """清理工作区"""
    for d in glob.glob(f"{WORKFLOWS_DIR}/round_*"):
        shutil.rmtree(d)
    for f in ['results.json', 'processed_experience.json']:
        p = f"{WORKFLOWS_DIR}/{f}"
        if os.path.exists(p):
            os.remove(p)
    for d in glob.glob(f"{WORKFLOWS_DIR}/**/__pycache__", recursive=True):
        shutil.rmtree(d, ignore_errors=True)


def ensure_seed():
    """确保种子文件存在"""
    round_dir = f"{WORKFLOWS_DIR}/round_1"
    os.makedirs(round_dir, exist_ok=True)
    with open(f"{round_dir}/graph.py", 'w') as f:
        f.write(SEED_GRAPH_PY)
    with open(f"{round_dir}/prompt.py", 'w') as f:
        f.write(SEED_PROMPT_PY)
    with open(f"{round_dir}/__init__.py", 'w') as f:
        f.write(SEED_INIT_PY)


def analyze_results(results):
    if not results:
        return {"error": "No results"}
    scores = [r["score"] for r in results if r.get("score") is not None]
    valid_scores = [s for s in scores if s > 0]
    if not valid_scores:
        return {"error": "No valid scores", "all_results": results}
    return {
        "total_rounds": len(results),
        "valid_rounds": len(valid_scores),
        "zero_score_rounds": len(scores) - len(valid_scores),
        "best_score": max(scores),
        "best_round": scores.index(max(scores)) + 1,
        "all_scores": scores,
    }


def main():
    print("=" * 60)
    print("  GSM8K 实验 B v2: 扩展7算子 + 全套验证机制")
    print("  [Custom, ScEnsemble, Programmer, AnswerExtractor,")
    print("   ReviewRevise, MultiPathReasoner, CodeVerifyRescue]")
    print("  新增: AST验证 + 冒烟测试 + 最优保留 + 瓶颈检测")
    print("=" * 60)
    print(f"时间: {datetime.now().isoformat()}\n")

    # 设置扩展模板
    print("[1] 设置扩展7算子模板...")
    set_extended_template()

    # 清理工作区
    print("[2] 清理工作区...")
    clean_workspace()
    ensure_seed()

    # 验证模板
    with open(f"{TEMPLATE_DIR}/operator.json", 'r') as f:
        ops = json.load(f)
    print(f"[3] 模板算子: {list(ops.keys())}")

    # 运行实验
    print(f"\n[4] 开始10轮优化 (model={MODEL_NAME})...")
    print(f"    AST验证: ✅ 启用")
    print(f"    冒烟测试: ✅ 启用")
    print(f"    最优保留: ✅ 启用 (退火策略 T₀=0.05)")
    print(f"    瓶颈检测: ✅ 启用\n")

    multiprocessing.set_start_method("fork", force=True)
    from scripts.optimizer import Optimizer
    from scripts.async_llm import LLMsConfig

    mc = LLMsConfig.default()
    cfg = mc.get(MODEL_NAME)

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
        # ===== v2 新增 =====
        enable_code_validation=True,
        enable_bottleneck_detection=True,
        enable_smoke_test=True,
        smoke_test_timeout=30.0,
    )

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

    # 统计
    print(f"\n验证机制统计:")
    print(f"  AST 拒绝: {optimizer._total_validation_rejections}")
    print(f"  冒烟拒绝: {optimizer._smoke_test_rejections}")

    # 读取结果
    results_path = f"{WORKFLOWS_DIR}/results.json"
    if os.path.exists(results_path):
        with open(results_path, 'r') as f:
            results = json.load(f)
    else:
        results = []

    analysis = analyze_results(results)
    analysis["time_seconds"] = elapsed
    analysis["success"] = success
    analysis["validation_stats"] = {
        "ast_rejections": optimizer._total_validation_rejections,
        "smoke_rejections": optimizer._smoke_test_rejections,
    }

    # 保存
    target_dir = f"{RESULTS_BASE}/expB_extended_v2"
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    shutil.copytree(WORKFLOWS_DIR, f"{target_dir}/workflows")
    if os.path.exists(results_path):
        shutil.copy2(results_path, f"{target_dir}/results.json")
    template_snapshot = f"{target_dir}/template_snapshot"
    if os.path.exists(template_snapshot):
        shutil.rmtree(template_snapshot)
    shutil.copytree(TEMPLATE_DIR, template_snapshot)
    with open(f"{target_dir}/analysis.json", 'w', encoding='utf-8') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    print(f"\n实验B v2结果:")
    print(f"  有效轮次: {analysis.get('valid_rounds', 'N/A')}/{analysis.get('total_rounds', 'N/A')}")
    print(f"  零分轮次: {analysis.get('zero_score_rounds', 'N/A')}")
    print(f"  最佳分数: {analysis.get('best_score', 'N/A'):.4f} (Round {analysis.get('best_round', 'N/A')})")
    print(f"  各轮分数: {analysis.get('all_scores', [])}")

    # 对比 v1
    v1_path = f"{RESULTS_BASE}/expB_extended/analysis.json"
    if os.path.exists(v1_path):
        with open(v1_path, 'r') as f:
            analysis_v1 = json.load(f)
        score_v1 = analysis_v1.get("best_score", 0)
        score_v2 = analysis.get("best_score", 0)
        delta = score_v2 - score_v1
        print(f"\n对比 v1: {score_v1:.4f} → {score_v2:.4f} (Δ={delta:+.4f})")

    print(f"\n结果已保存到: {target_dir}")


if __name__ == "__main__":
    main()
