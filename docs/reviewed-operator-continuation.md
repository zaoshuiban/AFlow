# 审核候选到离线评测的连接

入口：`python -m scripts.reviewed_operator_continuation`。当前实现已将独立补证入口产出的审核后候选连接到开发比较、单候选冻结、确认评测、私有实验库发布。此入口不重新诊断、不重新生成，也不把 source run 的候选替换为已有专项算子。

## 工作流

1. 使用 `scripts.independent_mechanism_evidence prepare/run/diagnose/review/generate` 产生受审核授权绑定的候选。审核及统计门槛不足不能交接。
2. 为后半程提供 design、gate、regression 三个 JSONL，并 prepare。design 包含范围内目标和范围外回归；gate 至少两道范围内题；regression 至少三道范围外题。默认还要求实际至少两道不同题获胜。
3. prepare 核对审核、完整基线、生成请求和候选版本，快照全部源证据。冻结评测题池、范围、配置、候选及规则。源题与三个池及池间不得题面或已提供 source_id/family_id 重叠。
4. run 按冻结审核范围比较候选，冻结唯一胜者，再做一次确认；失败归档，不用同一确认集换第二名。通过后只发布到此运行的 release 目录，并检查真实路由器可达性。

## 命令

以下是接口示例，路径应指向实际已生成候选的运行：

```sh
.venv/bin/python -m scripts.reviewed_operator_continuation prepare \
  --output-dir workspace/MATH/offline_runs/reviewed_confirmation_001 \
  --evidence-run /absolute/path/evidence_run \
  --design-data /absolute/path/design.jsonl \
  --gate-data /absolute/path/gate.jsonl \
  --regression-data /absolute/path/regression.jsonl \
  --evaluation-runs 3 --max-requests 1000

.venv/bin/python -m scripts.reviewed_operator_continuation run \
  --output-dir workspace/MATH/offline_runs/reviewed_confirmation_001 --allow-api

.venv/bin/python -m scripts.reviewed_operator_continuation status \
  --output-dir workspace/MATH/offline_runs/reviewed_confirmation_001
```

prepare 和 status 不创建模型客户端。真实 run 需要 `--allow-api`。请求上限不是美元预算；model_settings 继承源运行并校验。评测次数和该阶段请求预算在交接 prepare 冻结。新源码会使旧冻结运行拒绝恢复，应使用原冻结版本或另开运行，不改写旧 manifest。

## 两个专项的历史回放

新增 `scripts.prepare_specialist_replay` 导出已知历史构造题并可用 `--freeze` 冻结新基线。每个专项独立运行：3 道目标 + 2 道边界，重复 3 次；另外预选 3+3 道开发题、3 道确认题、3 道回归题。选择按题面稳定 ID 排序，未按历史完成错答挑题。

- `--evidence-mode historical_replay` 明确允许历史题来源，不声称独立自然训练证据。
- 后续必须使用 `--evaluation-mode historical_replay`；历史审核来源不能升级为 independent_natural。
- 回放题是已知构造专项题。所谓 gate 只是本轮生成器不可见的确认输入，不恢复为研究人员未看过的新隐藏题。
- 不导入旧预测为新响应；旧记录只供免费失败类型审计。新基线仍使用 generic_two_stage。
- 默认模板的两个已安装专项类和描述被移除，初始算子库为空；旧算法实现不会进入生成请求。这里是模型输入和调用边界，不是从仓库物理删除文件或操作系统沙箱。
- 当前生成后端仍为 prompt-only 固定骨架。禁止照搬旧 DP/素数工具；不承诺生成相同算法能力。
- 超时继续计为执行异常，不改写为数学错答。当前审核门槛不会因回放而降低，人工确认也不能绕过独立题数和完整基线要求。

## 已完成验证

本次 122 项测试通过（86 项链路/补证/范围/诊断，36 项算子库/晋级/路由/生成元数据）。使用模拟模型响应验证完整的审核→生成→交接→比较→确认→私有发布；同时检查候选/源证据/原始请求篡改、重叠题池、确认失败归档、恢复不重复调用以及回放标签。模拟测试不是研究测量，也不是一个真实成功闭环案例。
