# -*- coding: utf-8 -*-
# @Date    : 8/12/2024 22:00 PM
# @Author  : issac
# @Desc    : optimizer for graph (updated with AsyncLLM integration)

import asyncio
import glob
import json
import os
import re
import sys
import time
from typing import List, Literal, Dict, Optional

from pydantic import BaseModel, Field

from scripts.evaluator import DatasetType
from scripts.optimizer_utils.convergence_utils import ConvergenceUtils
from scripts.optimizer_utils.data_utils import DataUtils
from scripts.optimizer_utils.evaluation_utils import EvaluationUtils
from scripts.optimizer_utils.experience_utils import ExperienceUtils
from scripts.optimizer_utils.graph_utils import GraphUtils
from scripts.optimizer_utils.validation_utils import CodeValidator, BottleneckDetector, SmokeTestValidator
from scripts.optimizer_utils.operator_evolution import OperatorEvolver
from scripts.async_llm import create_llm_instance
from scripts.formatter import XmlFormatter, FormatError
from scripts.logs import logger

QuestionType = Literal["math", "code", "qa"]
OptimizerType = Literal["Graph", "Test"]


class GraphOptimize(BaseModel):
    modification: str = Field(default="", description="modification")
    graph: str = Field(default="", description="graph")
    prompt: str = Field(default="", description="prompt")


class Optimizer:
    def __init__(
        self,
        dataset: DatasetType,
        question_type: QuestionType,
        opt_llm_config,
        exec_llm_config,
        operators: List,
        sample: int,
        check_convergence: bool = False,
        optimized_path: str = None,
        initial_round: int = 1,
        max_rounds: int = 20,
        validation_rounds: int = 5,
        enable_code_validation: bool = False,
        enable_bottleneck_detection: bool = False,
        enable_smoke_test: bool = False,

        enable_operator_evolution: bool = False,
        max_validation_retries: int = 3,
        bottleneck_window: int = 3,
        bottleneck_threshold: float = 0.005,
        smoke_test_timeout: float = 30.0,
        enable_prompt_guidance: bool = True,

    ) -> None:
        self.optimize_llm_config = opt_llm_config
        self.optimize_llm = create_llm_instance(self.optimize_llm_config)
        self.execute_llm_config = exec_llm_config

        self.dataset = dataset
        self.type = question_type
        self.check_convergence = check_convergence

        self.graph = None
        self.operators = operators

        self.root_path = f"{optimized_path}/{self.dataset}"
        self.sample = sample
        self.top_scores = []
        self.round = initial_round
        self.max_rounds = max_rounds
        self.validation_rounds = validation_rounds

        # 代码验证与瓶颈检测
        self.enable_code_validation = enable_code_validation
        self.enable_bottleneck_detection = enable_bottleneck_detection
        self.enable_smoke_test = enable_smoke_test

        self.enable_operator_evolution = enable_operator_evolution
        self.enable_prompt_guidance = enable_prompt_guidance
        self.max_validation_retries = max_validation_retries
        self.smoke_test_timeout = smoke_test_timeout
        self.code_validator = CodeValidator()
        self.bottleneck_detector = BottleneckDetector(
            window=bottleneck_window, threshold=bottleneck_threshold
        )

        self._validation_retry_count = 0  # 本轮验证重试计数
        self._total_validation_rejections = 0  # 累计 AST 被拒绝次数
        self._smoke_test_rejections = 0  # 累计冒烟测试被拒绝次数


        self.graph_utils = GraphUtils(self.root_path)
        self.data_utils = DataUtils(self.root_path)
        self.experience_utils = ExperienceUtils(self.root_path)
        self.evaluation_utils = EvaluationUtils(self.root_path)
        self.convergence_utils = ConvergenceUtils(self.root_path)

        # 算子进化器（延迟初始化，需要 optimize_llm 就绪）
        self.operator_evolver: Optional[OperatorEvolver] = None
        self._evolution_cooldown = 0  # 冷却计数器，防止连续触发
        self._evolution_triggered_count = 0  # 已触发次数
        self._last_evolved_operator: Optional[str] = None  # 最近一次进化的算子名
        self._rounds_since_evolution = 0  # 进化后经过的轮次
        self._evolution_unused_rounds = 0  # 新算子未被使用的轮次计数

        # 零分快速恢复：连续零分时回退 template 快照
        self._consecutive_zero_count = 0  # 连续零分计数
        self._template_snapshot_dir = None  # 上一次进化前的 template 快照路径

    def optimize(self, mode: OptimizerType = "Graph"):
        if mode == "Test":
            test_n = 1  # validation datasets's execution number
            for i in range(test_n):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                score = loop.run_until_complete(self.test())
            return None

        for opt_round in range(self.max_rounds):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            retry_count = 0
            max_retries = 1

            while retry_count < max_retries:
                try:
                    score = loop.run_until_complete(self._optimize_graph())
                    break
                except Exception as e:
                    retry_count += 1
                    logger.info(f"Error occurred: {e}. Retrying... (Attempt {retry_count}/{max_retries})")
                    if retry_count == max_retries:
                        logger.info("Max retries reached. Moving to next round.")
                        score = 0.0
                        # 异常轮次也写入 results.json，让瓶颈检测能感知
                        graph_path = f"{self.root_path}/workflows"
                        data = self.data_utils.load_results(graph_path)
                        new_data = self.data_utils.create_result_data(self.round + 1, 0.0, 0.0, 0.0)
                        data.append(new_data)
                        result_path = self.data_utils.get_results_file_path(graph_path)
                        self.data_utils.save_results(result_path, data)

                    wait_time = 5 * retry_count
                    time.sleep(wait_time)

                if retry_count < max_retries:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

            self.round += 1
            logger.info(f"Score for round {self.round}: {score}")

            # 零分快速恢复：连续 2 轮零分时回退 template 快照
            if self.enable_operator_evolution:
                if score is not None and score == 0.0:
                    self._consecutive_zero_count += 1
                    if self._consecutive_zero_count >= 2:
                        logger.warning(
                            f"⚠️ 连续 {self._consecutive_zero_count} 轮零分，"
                            f"回退 template 到进化前快照"
                        )
                        self._rollback_template_snapshot()
                        self._consecutive_zero_count = 0
                        self._evolution_cooldown = 0  # 重置冷却，允许重新进化
                else:
                    # 非零分轮次，重置计数
                    self._consecutive_zero_count = 0



            # 瓶颈检测
            if self.enable_bottleneck_detection and score is not None:
                self.bottleneck_detector.update(self.round, score)
                is_stagnant, detail = self.bottleneck_detector.is_stagnant()

                # 改 4：进化后反馈回路 — 检查新算子使用情况
                if self.enable_operator_evolution and self._last_evolved_operator and self._rounds_since_evolution > 0:
                    self._rounds_since_evolution += 1
                    # 检查新算子是否出现在最新 round 的 graph.py 中
                    latest_round_dir = os.path.join(self.root_path, "workflows", f"round_{self.round}")
                    graph_file = os.path.join(latest_round_dir, "graph.py")
                    op_used_this_round = False
                    if os.path.exists(graph_file):
                        with open(graph_file, "r", encoding="utf-8") as f:
                            graph_code = f.read()
                        op_used_this_round = self._last_evolved_operator in graph_code

                    if op_used_this_round:
                        self._evolution_unused_rounds = 0
                    else:
                        self._evolution_unused_rounds += 1
                        logger.info(
                            f"🧬 新算子 {self._last_evolved_operator} "
                            f"第 {self._evolution_unused_rounds} 轮未被 graph 使用"
                        )
                        if self._evolution_unused_rounds >= 3:
                            logger.warning(
                                f"🧬 新算子 {self._last_evolved_operator} "
                                f"连续 {self._evolution_unused_rounds} 轮未被使用，自动回退"
                            )
                            self._rollback_template_snapshot()
                            if self._last_evolved_operator in self.operators:
                                self.operators.remove(self._last_evolved_operator)
                                logger.info(f"🧬 已从算子库移除 {self._last_evolved_operator}")
                            self._last_evolved_operator = None
                            self._evolution_unused_rounds = 0
                            self._rounds_since_evolution = 0
                            self._evolution_cooldown = 0  # 重置冷却，允许重新进化

                if is_stagnant:
                    logger.warning(f"🔍 瓶颈检测: {detail}")

                    # 瓶颈触发算子进化
                    if self.enable_operator_evolution and self._evolution_cooldown <= 0:
                        logger.info(f"🧬 瓶颈触发算子进化...")
                        
                        # 进化前保存 template 快照（用于零分回退 / 未使用回退）
                        self._save_template_snapshot()
                        
                        evolved = loop.run_until_complete(self._trigger_operator_evolution(detail))
                        if evolved and evolved.get("candidate_names"):
                            # Staged MATH evolution never exposes an untested
                            # specialist to graph search. Candidates remain
                            # archived until the direct tournament promotes one.
                            self._rollback_template_snapshot()
                            self._evolution_cooldown = 3
                            self._last_evolved_operator = None
                            self._rounds_since_evolution = 0
                            self._evolution_unused_rounds = 0
                            logger.info(
                                "🧬 已生成候选批次 %s: %s。"
                                "尚未安装；请先运行直接能力锦标赛。",
                                evolved.get("batch_id", "unknown"),
                                ", ".join(evolved["candidate_names"]),
                            )
                        elif evolved:
                            # ====== 准入门槛：小规模评测过滤低质量算子 ======
                            gate_passed, gate_details = loop.run_until_complete(
                                self.operator_evolver.quality_gate(
                                    operator_name=evolved["name"],
                                    exec_llm_config=self.execute_llm_config,
                                    dataset=self.dataset,
                                    sample_size=25,
                                    baseline_threshold=0.0,
                                )
                            )

                            if gate_passed:
                                self._evolution_cooldown = 3
                                self._evolution_triggered_count += 1
                                if self.dataset == "MATH":
                                    # A legacy gate only rejects clearly weak
                                    # artifacts. It never authorizes direct graph
                                    # execution. The revision remains archived as
                                    # a candidate until paired contextual evidence
                                    # promotes it for the evidence router.
                                    self._rollback_template_snapshot()
                                    self._last_evolved_operator = None
                                    self._rounds_since_evolution = 0
                                    self._evolution_unused_rounds = 0
                                    if "ContextAwareOperatorRouter" not in self.operators:
                                        self.operators.append("ContextAwareOperatorRouter")
                                    logger.info(
                                        f"🧬 新算子 {evolved['name']} 已作为 candidate 归档；"
                                        "未加入 graph 直调列表，等待成对上下文证据"
                                    )
                                else:
                                    self._last_evolved_operator = evolved["name"]
                                    self._rounds_since_evolution = 1
                                    self._evolution_unused_rounds = 0
                                    if evolved["name"] not in self.operators:
                                        self.operators.append(evolved["name"])
                            else:
                                logger.warning(
                                    f"🧬 进化算子 {evolved['name']} 未通过准入门槛 "
                                    f"(Δ={gate_details.get('diff', 'N/A'):+.4f})，丢弃并回退"
                                )
                                self._rollback_template_snapshot()
                                self._evolution_cooldown = 0  # 允许立刻重新进化
                    elif self.enable_operator_evolution and self._evolution_cooldown > 0:
                        self._evolution_cooldown -= 1
                        logger.info(f"🧬 算子进化冷却中 (剩余 {self._evolution_cooldown} 轮)")

                logger.info(f"📊 瓶颈状态: {self.bottleneck_detector.get_status()}")

            converged, convergence_round, final_round = self.convergence_utils.check_convergence(top_k=3)

            if converged and self.check_convergence:
                logger.info(
                    f"Convergence detected, occurred in round {convergence_round}, final round is {final_round}"
                )
                # Print average scores and standard deviations for each round
                self.convergence_utils.print_results()
                break

            time.sleep(5)

    async def _optimize_graph(self):
        validation_n = self.validation_rounds  # validation datasets's execution number
        graph_path = f"{self.root_path}/workflows"
        data = self.data_utils.load_results(graph_path)

        if self.round == 1:
            # 新实验开始时清空历史 results，避免不同次实验的分数互相污染导致收敛误判
            results_file = os.path.join(graph_path, "results.json")
            if os.path.exists(results_file):
                with open(results_file, "w") as f:
                    json.dump([], f)
                logger.info(f"🔄 新实验开始，已清空历史 results.json")

            directory = self.graph_utils.create_round_directory(graph_path, self.round)
            # Load graph using graph_utils
            self.graph = self.graph_utils.load_graph(self.round, graph_path)
            avg_score = await self.evaluation_utils.evaluate_graph(self, directory, validation_n, data, initial=True)



            # 初始化算子进化器（R1 评估后）
            if self.enable_operator_evolution and self.operator_evolver is None:
                self.operator_evolver = OperatorEvolver(
                    self.root_path, self.optimize_llm, self.dataset,
                    enable_prompt_guidance=self.enable_prompt_guidance,
                )
                logger.info(f"🧬 算子进化器初始化完成")

        # Create a loop until the generated graph meets the check conditions
        while True:
            directory = self.graph_utils.create_round_directory(graph_path, self.round + 1)

            top_rounds = self.data_utils.get_top_rounds(self.sample)
            sample = self.data_utils.select_round(top_rounds)

            prompt, graph_load = self.graph_utils.read_graph_files(sample["round"], graph_path)
            graph = self.graph_utils.extract_solve_graph(graph_load)

            processed_experience = self.experience_utils.load_experience()
            experience = self.experience_utils.format_experience(processed_experience, sample["round"])

            operator_description = self.graph_utils.load_operators_description(self.operators)
            log_data = self.data_utils.load_log(sample["round"])

            graph_optimize_prompt = self.graph_utils.create_graph_optimize_prompt(
                experience, sample["score"], graph[0], prompt, operator_description, self.type, log_data
            )

            # Replace ActionNode with AsyncLLM and XmlFormatter
            try:
                # Create XmlFormatter based on GraphOptimize model
                graph_formatter = XmlFormatter.from_model(GraphOptimize)
                
                # Call the LLM with formatter
                response = await self.optimize_llm.call_with_format(
                    graph_optimize_prompt, 
                    graph_formatter
                )
                
                # If we reach here, response is properly formatted and validated
                logger.info(f"Graph optimization response received successfully")
            except FormatError as e:
                # Handle format validation errors
                logger.error(f"Format error in graph optimization: {str(e)}")
                # Try again with a fallback approach - direct call with post-processing
                raw_response = await self.optimize_llm(graph_optimize_prompt)
                
                # Try to extract fields using basic parsing
                response = self._extract_fields_from_response(raw_response)
                if not response:
                    logger.error("Failed to extract fields from raw response, retrying...")
                    continue

            response["prompt"] = self._sanitize_prompt_content(response.get("prompt", ""))

            # Check if the modification meets the conditions
            check = self.experience_utils.check_modification(
                processed_experience, response["modification"], sample["round"]
            )

            # Code validation: 语法检查通过后才写入文件
            if check and self.enable_code_validation:
                is_valid, error_msg = CodeValidator.validate_graph_and_prompt(
                    response["graph"],
                    response["prompt"],
                    self.round + 1,
                    self.dataset,
                    library_path=os.path.join(
                        self.root_path,
                        "operator_library",
                        "operators.json",
                    ),
                )
                if not is_valid:
                    self._validation_retry_count += 1
                    self._total_validation_rejections += 1
                    logger.warning(
                        f"❌ 代码验证失败 (重试 {self._validation_retry_count}/{self.max_validation_retries}): "
                        f"{error_msg}"
                    )
                    if self._validation_retry_count >= self.max_validation_retries:
                        raise RuntimeError(
                            f"已达最大验证重试次数 ({self.max_validation_retries})，"
                            "拒绝写入未通过安全校验的工作流: "
                            f"{error_msg}"
                        )
                    # 验证失败，重新生成
                    check = False

            # If `check` is True, break the loop; otherwise, regenerate the graph
            if check:
                self._validation_retry_count = 0  # 重置计数
                break

        # Save the graph and evaluate
        self.graph_utils.write_graph_files(directory, response, self.round + 1, self.dataset)

        # 冒烟测试：import + 实例化 + 1 样例运行
        if self.enable_smoke_test:
            smoke_passed, smoke_err = await SmokeTestValidator.smoke_test(
                round_directory=directory,
                dataset=self.dataset,
                exec_llm_config=self.execute_llm_config,
                timeout=self.smoke_test_timeout,
            )
            if not smoke_passed:
                self._smoke_test_rejections += 1
                logger.warning(
                    f"🔥 冒烟测试失败: {smoke_err[:200]}"
                )
                # 冒烟测试失败 → 本轮直接给 0 分，不进入完整评估
                # 0 分会被最优保留机制拒绝并恢复最优工作流
                logger.info(f"🔥 冒烟测试失败，跳过完整评估（直接 0 分）")
                avg_score = 0.0
                experience = self.experience_utils.create_experience_data(sample, response["modification"])
                self.experience_utils.update_experience(directory, experience, avg_score)
                # 将 0 分也写入 results.json，让瓶颈检测能感知到
                cur_round = self.round + 1
                new_data = self.data_utils.create_result_data(cur_round, 0.0, 0.0, 0.0)
                data.append(new_data)
                result_path = self.data_utils.get_results_file_path(graph_path)
                self.data_utils.save_results(result_path, data)
                return avg_score
            else:
                logger.info(f"🔥 冒烟测试通过")

        experience = self.experience_utils.create_experience_data(sample, response["modification"])

        self.graph = self.graph_utils.load_graph(self.round + 1, graph_path)

        logger.info(directory)

        avg_score = await self.evaluation_utils.evaluate_graph(self, directory, validation_n, data, initial=False)

        self.experience_utils.update_experience(directory, experience, avg_score)

        return avg_score

    @staticmethod
    def _sanitize_prompt_content(prompt_content: str) -> str:
        """Convert common LLM prompt-label output into valid prompt.py assignments."""
        code = (prompt_content or "").strip()
        code = re.sub(r"^```(?:python)?\s*", "", code)
        code = re.sub(r"\s*```$", "", code).strip()

        if re.search(r"^\s*[A-Z][A-Z0-9_]*_PROMPT\s*=", code, re.MULTILINE):
            return code

        lines = code.splitlines()
        assignments = []
        index = 0
        while index < len(lines):
            heading = re.match(r"^\s*#\s*([A-Z][A-Z0-9_]*_PROMPT)\s*$", lines[index])
            if not heading:
                index += 1
                continue

            name = heading.group(1)
            index += 1
            body = []
            while index < len(lines):
                if re.match(r"^\s*#\s*[A-Z][A-Z0-9_]*_PROMPT\s*$", lines[index]):
                    break
                body.append(lines[index])
                index += 1

            text = "\n".join(body).strip()
            if text:
                text = text.replace('"""', '\\"\\"\\"')
                assignments.append(f'{name} = """\n{text}\n"""')

        if assignments:
            logger.info("Sanitized prompt.py content from comment labels to Python assignments")
            return "\n\n".join(assignments) + "\n"

        return code

    def _save_template_snapshot(self):
        """
        在算子进化触发前保存 template 目录快照。
        如果进化后的算子导致连续零分，可以回退到此快照。
        """
        import shutil
        template_dir = os.path.join(self.root_path, "workflows", "template")
        snapshot_dir = os.path.join(self.root_path, "workflows", "_template_snapshot_before_evolution")

        if not os.path.exists(template_dir):
            return

        # 清理旧快照
        if os.path.exists(snapshot_dir):
            shutil.rmtree(snapshot_dir, ignore_errors=True)

        # 复制当前 template
        shutil.copytree(template_dir, snapshot_dir)
        self._template_snapshot_dir = snapshot_dir
        logger.info(f"📸 已保存 template 快照 → {snapshot_dir}")

    def _rollback_template_snapshot(self):
        """
        回退 template 目录到进化前的快照。
        当连续零分时调用，移除有问题的进化算子。
        """
        import shutil
        template_dir = os.path.join(self.root_path, "workflows", "template")
        
        if self._template_snapshot_dir and os.path.exists(self._template_snapshot_dir):
            # 清除当前 template
            shutil.rmtree(template_dir, ignore_errors=True)
            # 恢复快照
            shutil.copytree(self._template_snapshot_dir, template_dir)

            # 清除 __pycache__
            pycache_dirs = glob.glob(os.path.join(template_dir, "**/__pycache__"), recursive=True)
            for pc in pycache_dirs:
                shutil.rmtree(pc, ignore_errors=True)
            # 也清除 workflows 级别
            wf_pycache = os.path.join(self.root_path, "workflows", "__pycache__")
            if os.path.exists(wf_pycache):
                shutil.rmtree(wf_pycache, ignore_errors=True)

            # 清除 sys.modules 中模板相关缓存
            modules_to_remove = [
                k for k in sys.modules 
                if "workflows.template" in k
            ]
            for m in modules_to_remove:
                del sys.modules[m]

            # 将进化产生的算子从 operators 列表中移除（因为 template 已回退）
            json_file = os.path.join(template_dir, "operator.json")
            if os.path.exists(json_file):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # 只保留回退后仍存在于 operator.json 中的算子
                    self.operators = [op for op in self.operators if op in data]
                    # 移除 auto_evolved 算子（因为快照中没有它们）
                    removed = [op for op in self.operators if op not in data]
                    if removed:
                        logger.info(f"🔄 已从算子库移除回退的算子: {removed}")
                except Exception as e:
                    logger.warning(f"读取回退后的 operator.json 失败: {e}")

            logger.info(f"🔄 已回退 template → 进化前快照")
        else:
            logger.warning("⚠️ 无 template 快照可回退，跳过恢复")

    async def _trigger_operator_evolution(self, bottleneck_detail: str) -> Optional[Dict]:
        """
        瓶颈触发算子进化

        流程：
        1. 收集失败样例
        2. 获取当前 graph 和算子描述
        3. 调用 OperatorEvolver 生成新算子
        4. 成功则返回进化记录，失败返回 None
        """
        if self.operator_evolver is None:
            self.operator_evolver = OperatorEvolver(
                self.root_path, self.optimize_llm, self.dataset,
                enable_prompt_guidance=self.enable_prompt_guidance,
            )

        # 收集上下文
        failure_cases = self.operator_evolver.collect_failure_cases(max_cases=5)
        graph_code = self.operator_evolver.get_current_graph_code()
        operator_descriptions = self.operator_evolver.get_operator_descriptions(self.operators)

        logger.info(f"🧬 开始算子进化...")
        logger.info(f"🧬 瓶颈信息: {bottleneck_detail[:100]}")
        logger.info(f"🧬 失败样例数: {len(failure_cases)}")

        try:
            if self.dataset == "MATH":
                return await self.operator_evolver.evolve_candidates(
                    bottleneck_info=bottleneck_detail,
                    failure_cases=failure_cases,
                    graph_code=graph_code,
                    operator_descriptions=operator_descriptions,
                    candidate_count=4,
                )
            result = await self.operator_evolver.evolve(
                bottleneck_info=bottleneck_detail,
                failure_cases=failure_cases,
                graph_code=graph_code,
                operator_descriptions=operator_descriptions,
            )
            return result
        except Exception as e:
            logger.error(f"🧬 算子进化异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _extract_fields_from_response(self, response: str) -> Dict[str, str]:
        """
        Fallback method to extract fields from raw response text using basic parsing
        
        Args:
            response: Raw response text from LLM
            
        Returns:
            Dictionary with extracted fields or None if extraction fails
        """
        try:
            # Try to extract XML tags with regex
            import re
            
            # Initialize result dictionary with default values
            result = {
                "modification": "",
                "graph": "",
                "prompt": ""
            }
            
            # Extract each field with regex
            for field in result.keys():
                pattern = rf"<{field}>(.*?)</{field}>"
                match = re.search(pattern, response, re.DOTALL)
                if match:
                    result[field] = match.group(1).strip()
            
            # Verify we have at least some content
            if not any(result.values()):
                logger.error("No fields could be extracted from response")
                return None
            
            return result
        except Exception as e:
            logger.error(f"Error extracting fields from response: {str(e)}")
            return None

    async def test(self):
        rounds = [1]  # You can choose the rounds you want to test here.
        data = []

        graph_path = f"{self.root_path}/workflows_test"
        json_file_path = self.data_utils.get_results_file_path(graph_path)

        data = self.data_utils.load_results(graph_path)

        for round in rounds:
            directory = self.graph_utils.create_round_directory(graph_path, round)
            self.graph = self.graph_utils.load_graph(round, graph_path)

            score, avg_cost, total_cost = await self.evaluation_utils.evaluate_graph_test(self, directory, is_test=True)

            new_data = self.data_utils.create_result_data(round, score, avg_cost, total_cost)
            data.append(new_data)

            self.data_utils.save_results(json_file_path, data)
