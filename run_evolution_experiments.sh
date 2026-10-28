#!/bin/bash
# ============================================================
# AFlow 算子进化机制 — 三阶段实验自动运行脚本
# 使用方法: bash run_evolution_experiments.sh [phase]
#   phase: all | phase1 | phase2 | phase3 | exp1 | exp2 | exp3
#   默认: all（依次执行全部）
# ============================================================

set -euo pipefail

# ---- 公共配置 ----
MODEL_OPT="deepseek-chat"
MODEL_EXEC="deepseek-chat"
SAMPLE=4
DATASET_DEFAULT="GSM8K"

# ---- 日志目录 ----
LOG_DIR="experiment_logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ============================================================
# 阶段一：GSM8K 快速验证
# ============================================================

run_exp1_baseline() {
    log "========== Exp1: GSM8K Baseline（不开进化）=========="
    python run.py \
        --dataset GSM8K \
        --sample $SAMPLE \
        --max_rounds 15 \
        --opt_model_name "$MODEL_OPT" \
        --exec_model_name "$MODEL_EXEC" \
        --enable_code_validation true \
        --enable_bottleneck_detection true \
        --enable_smoke_test true \
        --enable_operator_evolution false \
        2>&1 | tee "$LOG_DIR/exp1_baseline_${TIMESTAMP}.log"
    log "Exp1 完成"
}

run_exp2_evolution_full() {
    log "========== Exp2: GSM8K 进化全开（4项改进）=========="
    python run.py \
        --dataset GSM8K \
        --sample $SAMPLE \
        --max_rounds 15 \
        --opt_model_name "$MODEL_OPT" \
        --exec_model_name "$MODEL_EXEC" \
        --enable_code_validation true \
        --enable_bottleneck_detection true \
        --enable_smoke_test true \
        --enable_operator_evolution true \
        --enable_prompt_guidance true \
        2>&1 | tee "$LOG_DIR/exp2_evolution_full_${TIMESTAMP}.log"
    log "Exp2 完成"
}

run_exp3_evolution_ablation() {
    log "========== Exp3: GSM8K 消融（开进化但不加 prompt 引导）=========="
    python run.py \
        --dataset GSM8K \
        --sample $SAMPLE \
        --max_rounds 15 \
        --opt_model_name "$MODEL_OPT" \
        --exec_model_name "$MODEL_EXEC" \
        --enable_code_validation true \
        --enable_bottleneck_detection true \
        --enable_smoke_test true \
        --enable_operator_evolution true \
        --enable_prompt_guidance false \
        2>&1 | tee "$LOG_DIR/exp3_evolution_ablation_${TIMESTAMP}.log"
    log "Exp3 完成"
}

run_phase1() {
    log "====== 阶段一：GSM8K 快速验证 ======"
    run_exp1_baseline
    run_exp2_evolution_full
    run_exp3_evolution_ablation
    log "====== 阶段一完成 ======"
    log ""
    log "📊 结果汇总："
    log "  Exp1 Baseline:  查看 workspace/GSM8K/workflows/results.json"
    log "  Exp2 全开:      查看 workspace/GSM8K/workflows/results.json"
    log "  Exp3 消融:      查看 workspace/GSM8K/workflows/results.json"
    log ""
    log "🔑 重点对比 Exp2 vs Exp3：如果 Exp2 的算子不是 verify/check 类而 Exp3 是 → prompt 引导有效"
}

# ============================================================
# 阶段二：MATH 压力测试
# ============================================================

run_phase2() {
    log "====== 阶段二：MATH 压力测试 ======"

    # 2a: MATH Baseline
    log "--- MATH Baseline ---"
    python run.py \
        --dataset MATH \
        --sample $SAMPLE \
        --max_rounds 20 \
        --opt_model_name "$MODEL_OPT" \
        --exec_model_name "$MODEL_EXEC" \
        --enable_code_validation true \
        --enable_bottleneck_detection true \
        --enable_smoke_test true \
        --enable_operator_evolution false \
        2>&1 | tee "$LOG_DIR/math_baseline_${TIMESTAMP}.log"

    # 2b: MATH 进化全开
    log "--- MATH 进化全开 ---"
    python run.py \
        --dataset MATH \
        --sample $SAMPLE \
        --max_rounds 20 \
        --opt_model_name "$MODEL_OPT" \
        --exec_model_name "$MODEL_EXEC" \
        --enable_code_validation true \
        --enable_bottleneck_detection true \
        --enable_smoke_test true \
        --enable_operator_evolution true \
        --enable_prompt_guidance true \
        2>&1 | tee "$LOG_DIR/math_evolution_full_${TIMESTAMP}.log"

    log "====== 阶段二完成 ======"
    log "📊 MATH 结果：查看 workspace/MATH/workflows/results.json"
    log "🔑 关注：进化触发次数、错误类型分布多样性、算子创新方向"
}

# ============================================================
# 阶段三：HumanEval 适配验证
# ============================================================

run_phase3() {
    log "====== 阶段三：HumanEval 适配验证 ======"

    # 3a: HumanEval Baseline
    log "--- HumanEval Baseline ---"
    python run.py \
        --dataset HumanEval \
        --sample $SAMPLE \
        --max_rounds 15 \
        --opt_model_name "$MODEL_OPT" \
        --exec_model_name "$MODEL_EXEC" \
        --enable_code_validation true \
        --enable_bottleneck_detection true \
        --enable_smoke_test true \
        --enable_operator_evolution false \
        2>&1 | tee "$LOG_DIR/humaneval_baseline_${TIMESTAMP}.log"

    # 3b: HumanEval 进化全开
    log "--- HumanEval 进化全开 ---"
    python run.py \
        --dataset HumanEval \
        --sample $SAMPLE \
        --max_rounds 15 \
        --opt_model_name "$MODEL_OPT" \
        --exec_model_name "$MODEL_EXEC" \
        --enable_code_validation true \
        --enable_bottleneck_detection true \
        --enable_smoke_test true \
        --enable_operator_evolution true \
        --enable_prompt_guidance true \
        2>&1 | tee "$LOG_DIR/humaneval_evolution_full_${TIMESTAMP}.log"

    log "====== 阶段三完成 ======"
    log "📊 HumanEval 结果：查看 workspace/HumanEval/workflows/results.json"
    log "🔑 关注：代码场景下 anti-pattern 是否需要扩展（SyntaxFixer/ImportChecker 等）"
}

# ============================================================
# 结果快速分析
# ============================================================

analyze_results() {
    log "====== 结果快速分析 ======"

    for dataset in GSM8K MATH HumanEval; do
        results_file="workspace/${dataset}/workflows/results.json"
        if [ -f "$results_file" ]; then
            log ""
            log "📈 ${dataset} 结果："
            python3 -c "
import json
with open('${results_file}') as f:
    data = json.load(f)
if isinstance(data, list):
    best = max(data, key=lambda x: x.get('score', 0))
    print(f'  总轮次: {len(data)}')
    print(f'  最高分: {best[\"score\"]:.4f} (R{best[\"round\"]})')
    # 最近5轮趋势
    recent = data[-5:] if len(data) >= 5 else data
    scores = [r['score'] for r in recent]
    print(f'  近5轮: {\" → \".join(f\"{s:.4f}\" for s in scores)}')
    # 进化触发检查（通过算子库变化推断）
    ops_counts = set()
    for r in data:
        if 'operators_count' in r:
            ops_counts.add(r['operators_count'])
    if len(ops_counts) > 1:
        print(f'  ⚠ 算子库有变化（可能触发了进化）')
else:
    print('  格式不识别')
" 2>/dev/null || log "  解析失败"
        else
            log "📈 ${dataset}: 结果文件不存在（跳过）"
        fi
    done

    log ""
    log "🔍 检查进化日志关键词："
    for logf in "$LOG_DIR"/exp2_* "$LOG_DIR"/exp3_* "$LOG_DIR"/math_evolution_* "$LOG_DIR"/humaneval_evolution_*; do
        if [ -f "$logf" ]; then
            evolve_count=$(grep -c "🧬 算子进化成功" "$logf" 2>/dev/null || echo "0")
            unused_count=$(grep -c "连续.*轮未被使用" "$logf" 2>/dev/null || echo "0")
            verify_count=$(grep -c "verify\|check\|validate\|Sanity" "$logf" 2>/dev/null || echo "0")
            log "  $(basename "$logf"):"
            log "    进化成功: ${evolve_count}次 | 自动回退: ${unused_count}次 | verify类关键词: ${verify_count}次"
        fi
    done
}

# ============================================================
# 入口
# ============================================================

case "${1:-all}" in
    exp1)    run_exp1_baseline ;;
    exp2)    run_exp2_evolution_full ;;
    exp3)    run_exp3_evolution_ablation ;;
    phase1)  run_phase1 ;;
    phase2)  run_phase2 ;;
    phase3)  run_phase3 ;;
    all)
        run_phase1
        run_phase2
        run_phase3
        ;;
    analyze) analyze_results ;;
    *)
        echo "用法: bash run_evolution_experiments.sh [all|phase1|phase2|phase3|exp1|exp2|exp3|analyze]"
        echo ""
        echo "  all     — 依次执行阶段一~三"
        echo "  phase1  — GSM8K: Baseline + 进化全开 + 消融"
        echo "  phase2  — MATH: Baseline + 进化全开"
        echo "  phase3  — HumanEval: Baseline + 进化全开"
        echo "  exp1    — 仅跑 GSM8K Baseline"
        echo "  exp2    — 仅跑 GSM8K 进化全开"
        echo "  exp3    — 仅跑 GSM8K 消融"
        echo "  analyze — 分析已有结果"
        exit 1
        ;;
esac
