# -*- coding: utf-8 -*-
"""
代码语法验证、冒烟测试与瓶颈检测工具

解决的核心问题：
1. LLM 生成的 graph.py/prompt.py 可能包含语法错误，直接运行导致 0 分浪费轮次
2. 即便语法正确，运行时错误（import 缺失、属性错误等）仍会导致 0 分
3. 优化器可能退化（新工作流不如历史最优），浪费轮次
4. 优化器可能陷入停滞，需要检测瓶颈以触发算子进化
"""

import ast
import asyncio
import importlib
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from scripts.logs import logger


class CodeValidator:
    """验证 LLM 生成的 graph.py 和 prompt.py 代码"""

    @staticmethod
    def validate_graph_and_prompt(
        graph_content: str,
        prompt_content: str,
        round_number: int,
        dataset: str,
        library_path: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        验证生成的 graph.py 和 prompt.py 是否可以通过语法检查

        Args:
            graph_content: LLM 生成的 graph 部分（不含模板头）
            prompt_content: LLM 生成的 prompt.py 全部内容
            round_number: 当前轮次
            dataset: 数据集名称

        Returns:
            (is_valid, error_message)
        """
        # 构造完整的 graph.py（与 WORKFLOW_TEMPLATE 一致）
        full_graph = f"""from typing import Literal
import workspace.{dataset}.workflows.template.operator as operator
import workspace.{dataset}.workflows.round_{round_number}.prompt as prompt_custom
from scripts.async_llm import create_llm_instance


from scripts.evaluator import DatasetType

{graph_content}
"""
        errors = []

        # 1. graph.py 语法检查
        graph_valid, graph_err = CodeValidator._check_syntax(full_graph, "graph.py")
        if not graph_valid:
            errors.append(f"graph.py 语法错误: {graph_err}")

        # 2. prompt.py 语法检查
        prompt_valid, prompt_err = CodeValidator._check_syntax(prompt_content, "prompt.py")
        if not prompt_valid:
            errors.append(f"prompt.py 语法错误: {prompt_err}")

        # 3. graph.py 结构检查（即使语法通过，也需检查关键元素）
        if graph_valid:
            struct_valid, struct_err = CodeValidator._check_graph_structure(full_graph)
            if not struct_valid:
                errors.append(f"graph.py 结构问题: {struct_err}")
            bypass_valid, bypass_err = CodeValidator._check_managed_operator_bypass(
                full_graph, dataset, library_path=library_path
            )
            if not bypass_valid:
                errors.append(f"graph.py 算子路由问题: {bypass_err}")

        # 4. prompt.py 常见模式检查
        if prompt_valid:
            pattern_valid, pattern_err = CodeValidator._check_prompt_patterns(prompt_content)
            if not pattern_valid:
                errors.append(f"prompt.py 模式问题: {pattern_err}")

        if errors:
            return False, "; ".join(errors)
        return True, ""

    @staticmethod
    def _check_syntax(code: str, filename: str) -> Tuple[bool, str]:
        """AST 语法检查"""
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            line_info = f" (line {e.lineno})" if e.lineno else ""
            return False, f"{e.msg}{line_info}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _check_graph_structure(code: str) -> Tuple[bool, str]:
        """检查 graph.py 是否包含必要的 Workflow 类和 __call__ 方法"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False, "无法解析"

        has_workflow = False
        has_call = False

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Workflow":
                has_workflow = True
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if item.name == "__call__":
                            has_call = True

        if not has_workflow:
            return False, "缺少 Workflow 类"
        if not has_call:
            return False, "Workflow 类缺少 __call__ 方法"

        return True, ""

    @staticmethod
    def _check_managed_operator_bypass(
        code: str,
        dataset: str,
        library_path: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Reject direct use of library-managed evolved specialists."""
        resolved_library_path = (
            Path(library_path)
            if library_path
            else Path("workspace") / dataset / "operator_library" / "operators.json"
        )
        if not resolved_library_path.exists():
            return True, ""
        try:
            data = json.loads(resolved_library_path.read_text(encoding="utf-8"))
            managed_names = set(data.get("operators", {}))
            tree = ast.parse(code)
        except Exception as exc:
            return False, f"无法验证持久算子库: {exc}"
        bypassed = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "operator"
                and node.attr in managed_names
            ):
                bypassed.add(node.attr)
        if bypassed:
            return False, (
                "持久库算子只能通过 ContextAwareOperatorRouter 调用，禁止直调: "
                + ", ".join(sorted(bypassed))
            )
        return True, ""

    @staticmethod
    def _check_prompt_patterns(code: str) -> Tuple[bool, str]:
        """检查 prompt.py 中的常见 LLM 生成错误模式"""
        lines = code.split('\n')
        for i in range(len(lines) - 1):
            line = lines[i].strip()
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""

            if re.match(r'^#\s*\w+_PROMPT\s*=', line) and next_line.startswith('"""'):
                return False, f"第{i+1}行: 注释掉的 PROMPT 变量名后跟裸字符串"

            if re.match(r'^#\s*\w+_PROMPT\s*$', line) and next_line.startswith('"""'):
                return False, f"第{i+1}行: 注释掉的 PROMPT 名后跟裸字符串"

        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    val = str(node.value.value)
                    if len(val) > 50:
                        return False, f"第{node.lineno}行: 裸字符串常量（未赋值给任何变量）"
        except SyntaxError:
            pass

        return True, ""


class SmokeTestValidator:
    """冒烟测试：写入文件后实际 import + 实例化 + 运行 1 个样例"""

    # 每个数据集的冒烟测试样例（硬编码，避免数据读取）
    SMOKE_SAMPLES = {
        "MATH": {
            "problem": "What is the value of $2+2$?",
        },
        "GSM8K": {
            "problem": "Janet's ducks lay 16 eggs per day. She eats three for breakfast every morning and bakes muffins for her friends every day with four. She sells the remainder at the farmers' market for $2 per fresh duck egg. How much does she make every day at the farmers' market?",
        },
    }

    @staticmethod
    async def smoke_test(
        round_directory: str,
        dataset: str,
        exec_llm_config,
        timeout: float = 30.0,
    ) -> Tuple[bool, str]:
        """
        对已写入磁盘的 graph.py/prompt.py 执行冒烟测试

        步骤:
        1. 清除 __pycache__ 缓存
        2. import graph 模块
        3. 实例化 Workflow
        4. 用 1 个样例调用 __call__
        5. 验证返回格式正确 (response, cost)

        Args:
            round_directory: round_N 目录的路径
            dataset: 数据集名 (MATH / GSM8K)
            exec_llm_config: 执行用 LLM 配置
            timeout: 单次调用超时（秒）

        Returns:
            (passed, error_message)
        """
        round_directory = os.path.abspath(round_directory)

        # 1. 清除 __pycache__
        _clear_pycache(round_directory)

        # 2. 计算 import 路径
        #    round_directory 形如 .../workspace/MATH/workflows/round_6
        #    import 路径应为 workspace.MATH.workflows.round_6.graph
        import_path = _directory_to_import_path(round_directory, "graph")
        if import_path is None:
            return False, f"无法从路径 {round_directory} 推导 import 路径"

        # 3. import 并获取 Workflow 类
        try:
            # 清除可能缓存的旧模块
            modules_to_remove = [k for k in sys.modules if k.startswith(import_path.rsplit(".", 1)[0])]
            for m in modules_to_remove:
                del sys.modules[m]

            graph_module = importlib.import_module(import_path)
            WorkflowClass = getattr(graph_module, "Workflow", None)
            if WorkflowClass is None:
                return False, "模块中未找到 Workflow 类"
        except Exception as e:
            tb = traceback.format_exc()
            return False, f"import 失败: {e}\n{tb}"

        # 4. 实例化
        try:
            workflow_instance = WorkflowClass(
                name=dataset,
                llm_config=exec_llm_config,
                dataset=dataset,
            )
        except Exception as e:
            tb = traceback.format_exc()
            return False, f"实例化 Workflow 失败: {e}\n{tb}"

        # 5. 运行一个样例
        sample = SmokeTestValidator.SMOKE_SAMPLES.get(dataset)
        if sample is None:
            # 如果没有硬编码样例，只验证 import + 实例化
            logger.info(f"数据集 {dataset} 无冒烟样例，仅验证 import + 实例化")
            return True, ""

        try:
            result = await asyncio.wait_for(
                workflow_instance(sample["problem"]),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return False, f"冒烟测试超时 ({timeout}s)"
        except Exception as e:
            tb = traceback.format_exc()
            return False, f"冒烟测试运行失败: {e}\n{tb}"

        # 6. 验证返回格式
        if not isinstance(result, tuple) or len(result) != 2:
            return False, f"返回格式错误: 期望 (response, cost)，实际 {type(result)}"

        response, cost = result
        if not isinstance(response, str):
            return False, f"返回 response 不是 str: {type(response)}"
        if not isinstance(cost, (int, float)):
            return False, f"返回 cost 不是数值: {type(cost)}"

        # 如果 response 为空，说明某个算子返回了 status=error 但 graph 仍能运行
        # 这不算致命错误，允许继续（评估时该题会自动得 0 分）
        if not response.strip():
            logger.warning(f"⚠️ 冒烟测试返回空 response，graph 可能有算子出错但未崩溃，允许通过")
        
        logger.info(f"✅ 冒烟测试通过: response='{response[:80]}...' cost={cost}")
        return True, ""


class BestSoFarTracker:
    """
    最优保留机制：防止优化器退化

    策略：
    - 记录历史最优工作流（分数 + 文件路径）
    - 只接受 ≥ 最优分数的新工作流
    - 不接受时，恢复历史最优工作流到下一个 round 目录
    - 退化时按指数退火概率接受：P = exp(-|Δscore| / temperature)
      - 轻微退化高概率接受（保留探索）
      - 严重退化低概率接受（防止崩溃）
      - temperature 随轮次线性衰减（前期探索，后期收敛）
    """

    def __init__(
        self,
        root_path: str,
        initial_best_score: float = 0.0,
        initial_best_round: int = 1,
        accept_worse_prob: float = 0.0,
        initial_temperature: float = 0.05,
        max_rounds: int = 20,
    ):
        """
        Args:
            root_path: workspace/{dataset} 路径
            initial_best_score: 初始最优分数
            initial_best_round: 初始最优轮次
            accept_worse_prob: （已弃用，保留向后兼容）接受更差工作流的固定概率
            initial_temperature: 退火初始温度 T₀（控制接受退化的宽容度）
            max_rounds: 总优化轮次（用于计算温度衰减）
        """
        self.root_path = root_path
        self.best_score = initial_best_score
        self.best_round = initial_best_round
        self.accept_worse_prob = accept_worse_prob  # 弃用，保留兼容
        self.initial_temperature = initial_temperature
        self.max_rounds = max_rounds
        self.current_round = initial_best_round
        self.reject_count = 0

    def _get_temperature(self) -> float:
        """线性退火：T = T₀ × (1 - progress), progress ∈ [0, 1]"""
        progress = min((self.current_round - 1) / max(self.max_rounds - 1, 1), 1.0)
        return self.initial_temperature * (1.0 - progress)

    def _acceptance_probability(self, delta: float, temperature: float) -> float:
        """P = exp(-|Δ| / T)，delta < 0 表示退化"""
        if temperature <= 0:
            return 0.0
        return float(min(1.0, __import__("math").exp(-abs(delta) / temperature)))

    def should_accept(self, new_score: float) -> Tuple[bool, str]:
        """
        判断是否接受新工作流

        Returns:
            (should_accept, reason)
        """
        if new_score >= self.best_score:
            reason = f"新分数 {new_score:.4f} ≥ 最优 {self.best_score:.4f}，接受"
            return True, reason

        # 分数更低 → 计算退化幅度
        delta = new_score - self.best_score  # < 0
        temperature = self._get_temperature()
        prob = self._acceptance_probability(delta, temperature)

        import random
        if prob > 0 and random.random() < prob:
            reason = (
                f"新分数 {new_score:.4f} < 最优 {self.best_score:.4f} "
                f"(Δ={delta:+.4f}, T={temperature:.4f}, P={prob:.3f})，"
                f"以概率 {prob:.3f} 接受（探索）"
            )
            return True, reason

        reason = (
            f"新分数 {new_score:.4f} < 最优 {self.best_score:.4f} "
            f"(Δ={delta:+.4f}, T={temperature:.4f}, P={prob:.3f})，拒绝以保留最优"
        )
        return False, reason

    def update_best(self, score: float, round_number: int, directory: str):
        """更新最优记录并保存文件备份"""
        self.current_round = max(self.current_round, round_number)
        if score > self.best_score:
            self.best_score = score
            self.best_round = round_number
            self._save_best_files(directory)
            logger.info(f"🏆 新最优: {score:.4f} (R{round_number})，已备份")

    def restore_best_to(self, target_round: int):
        """
        将历史最优工作流恢复到指定 round 目录

        当新工作流被拒绝时调用，确保下一轮基于最优版本继续优化
        """
        src_dir = os.path.join(self.root_path, "workflows", f"round_{self.best_round}")
        dst_dir = os.path.join(self.root_path, "workflows", f"round_{target_round}")

        if not os.path.exists(src_dir):
            logger.warning(f"最优轮次目录不存在: {src_dir}")
            return

        os.makedirs(dst_dir, exist_ok=True)

        for fname in ["graph.py", "prompt.py", "__init__.py"]:
            src_file = os.path.join(src_dir, fname)
            dst_file = os.path.join(dst_dir, fname)
            if os.path.exists(src_file):
                # 对 graph.py 需要替换 import 路径中的 round 编号
                with open(src_file, "r", encoding="utf-8") as f:
                    content = f.read()
                if fname == "graph.py":
                    content = re.sub(
                        r"import workspace\.(.+)\.workflows\.round_\d+\.prompt as prompt_custom",
                        rf"import workspace.\1.workflows.round_{target_round}.prompt as prompt_custom",
                        content,
                    )
                with open(dst_file, "w", encoding="utf-8") as f:
                    f.write(content)

        _clear_pycache(dst_dir)
        self.reject_count += 1
        self.current_round = max(self.current_round, target_round)
        logger.info(
            f"🔄 已恢复最优工作流 R{self.best_round} → R{target_round} "
            f"(累计拒绝 {self.reject_count} 次)"
        )

    def _save_best_files(self, directory: str):
        """备份最优工作流到 _best_snapshot 目录"""
        snapshot_dir = os.path.join(self.root_path, "workflows", "_best_snapshot")
        os.makedirs(snapshot_dir, exist_ok=True)
        for fname in ["graph.py", "prompt.py", "__init__.py"]:
            src = os.path.join(directory, fname)
            dst = os.path.join(snapshot_dir, fname)
            if os.path.exists(src):
                with open(src, "r", encoding="utf-8") as f:
                    content = f.read()
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(content)

    def get_status(self) -> str:
        t = self._get_temperature()
        return (
            f"最优: {self.best_score:.4f} (R{self.best_round}), "
            f"拒绝次数: {self.reject_count}, "
            f"温度: {t:.4f} (R{self.current_round}/{self.max_rounds})"
        )


class BottleneckDetector:
    """检测优化停滞（瓶颈）— 双条件触发：无提升 + 连续退化"""

    def __init__(self, window: int = 3, threshold: float = 0.005,
                 no_gain_window: int = 2, decline_window: int = 3):
        self.window = window
        self.threshold = threshold
        self.no_gain_window = no_gain_window      # 条件 1：连续 N 轮未超越历史最优
        self.decline_window = decline_window       # 条件 2：连续 N 轮分数单调下降
        self.history: List[Dict] = []

    def update(self, round_number: int, score: float):
        self.history.append({"round": round_number, "score": score})

    def is_stagnant(self) -> Tuple[bool, Optional[str]]:
        if len(self.history) < 2:
            return False, None

        # 全局历史最优（排除零分）
        valid_history = [r["score"] for r in self.history if r["score"] > 0]
        if not valid_history:
            return False, None
        best_all = max(valid_history)

        # 条件 1：连续 no_gain_window 轮未超越历史最优
        if len(self.history) >= self.no_gain_window:
            recent = self.history[-self.no_gain_window:]
            best_recent = max(r["score"] for r in recent if r["score"] > 0) if any(r["score"] > 0 for r in recent) else 0
            if best_recent <= best_all:
                rounds_str = ", ".join(f"R{r['round']}={r['score']:.4f}" for r in recent)
                return True, (
                    f"连续{self.no_gain_window}轮未超越历史最优{best_all:.4f} "
                    f"(近期最高={best_recent:.4f}). "
                    f"轮次: [{rounds_str}]"
                )

        # 条件 2：连续 decline_window 轮分数单调下降（紧急触发）
        if len(self.history) >= self.decline_window:
            recent = self.history[-self.decline_window:]
            scores = [r["score"] for r in recent]
            if all(s > 0 for s in scores) and all(scores[i] > scores[i+1] for i in range(len(scores)-1)):
                rounds_str = ", ".join(f"R{r['round']}={r['score']:.4f}" for r in recent)
                return True, (
                    f"连续{self.decline_window}轮退化 "
                    f"({scores[0]:.4f}→{scores[-1]:.4f}). "
                    f"轮次: [{rounds_str}]"
                )

        # 保留原始逻辑作为兜底（window=3, threshold）
        if len(self.history) >= self.window:
            recent = self.history[-self.window:]
            scores = [r["score"] for r in recent]
            if all(s == 0.0 for s in scores):
                return False, None
            valid_scores = [s for s in scores if s > 0]
            if not valid_scores:
                return False, None
            best_recent = max(valid_scores)
            improvement = best_recent - best_all
            if improvement < self.threshold:
                rounds_str = ", ".join(f"R{r['round']}={r['score']:.4f}" for r in recent)
                return True, (
                    f"最近{self.window}轮无显著提升 "
                    f"(提升={improvement:+.4f}, 阈值={self.threshold}). "
                    f"轮次: [{rounds_str}]"
                )

        return False, None

    def get_status(self) -> str:
        if not self.history:
            return "无数据"

        best = max(self.history, key=lambda x: x["score"])
        latest = self.history[-1]

        is_stag, detail = self.is_stagnant()
        stag_str = f" ⚠ 瓶颈! {detail}" if is_stag else ""

        return (
            f"Round {latest['round']}: score={latest['score']:.4f}, "
            f"best={best['score']:.4f} (R{best['round']})"
            f"{stag_str}"
        )


# ========== 辅助函数 ==========

def _clear_pycache(directory: str):
    """清除目录下的 __pycache__"""
    pycache = os.path.join(directory, "__pycache__")
    if os.path.exists(pycache):
        import shutil
        shutil.rmtree(pycache, ignore_errors=True)


def _directory_to_import_path(directory: str, module_name: str) -> Optional[str]:
    """
    将绝对路径目录转换为 Python import 路径

    例: /Users/.../AFlow/workspace/MATH/workflows/round_6 → workspace.MATH.workflows.round_6.graph
    """
    # 找到包含 'workspace' 的路径部分
    parts = Path(directory).parts
    try:
        ws_idx = list(parts).index("workspace")
    except ValueError:
        return None

    relative_parts = parts[ws_idx:]
    return ".".join(relative_parts) + f".{module_name}"
