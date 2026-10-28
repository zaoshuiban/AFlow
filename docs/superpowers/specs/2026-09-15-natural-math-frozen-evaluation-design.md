# 冻结条件下的自然 MATH 双算子评测设计

## 1. 目标与研究问题

本实验在不修改现有两个算子、算子库、路由规则和评分器的条件下，评估双算子系统在自然 MATH 分布上的总体覆盖率、净收益、安全性和成本。实验不再构造专项正例，而是回答以下问题：

1. 括号化去重算子和受限数字素数算子在自然题中实际覆盖多少题；
2. 完整方法相对原始 AFlow 是否获得正的总体净收益；
3. 算子路由是否对非适用题造成误触发、跨算子冲突或可归因回归；
4. 准确率变化是否伴随超时、Token 和模型调用成本的改善；
5. 严格未使用本地自然集和公开标准 MATH-500 上的结果是否一致。

实验采用 C3 方案：本地严格未使用自然集重复 3 次，公开 MATH-500 完整运行 1 次。

## 2. 数据集与隔离策略

### 2.1 A 组：本地严格未使用自然集

数据源为 `data/datasets/math_test.jsonl`。当前文件包含 486 道唯一题目。准备阶段扫描 `workspace/MATH` 下历史实验 JSONL 中保存的 `problem_id`；若记录没有 `problem_id`，则用现有 `stable_problem_id(problem)` 计算稳定标识。与历史记录重叠的题目全部排除。

按 2026 年 9 月 15 日的审计结果，486 道题中有 189 道已经出现在历史实验材料中，剩余 297 道作为严格未使用自然集。准备脚本必须重新计算该数量，不把 297 写成不可验证的常量；如果实际数量变化，准备阶段应停止并报告差异，禁止静默改变样本集。

A 组使用全部 297 道题，不再抽样，每题运行 3 次。论文中称为“MATH 自然分布严格未使用测试集（297 题，3 次重复）”，不得称为 MATH-500。

### 2.2 B 组：公开标准 MATH-500

标准数据源为 `HuggingFaceH4/MATH-500` 的 `test.jsonl`，它是 OpenAI 在 *Let's Verify Step by Step* 工作中整理的 MATH 500 题子集。下载必须独立于正式执行脚本完成，并固定：

- Hugging Face 仓库及提交版本；
- 原始下载地址；
- 下载时间；
- 原始文件 SHA-256；
- 题目数量和字段检查结果。

B 组保留完整 500 题，不因历史重叠而删除题目。准备阶段为每题增加 `overlap_with_history` 和 `overlap_with_local_strict_set` 标签，并在结果中同时报告完整 500 题、历史不重叠子集和历史重叠子集。这里 `overlap_with_history` 使用准备 A 组之前的历史实验快照，明确排除本次 `natural_frozen_eval` 目录；`overlap_with_local_strict_set` 则单独与 A 组冻结 ID 清单比较，避免把本次 A 组误算成既往开发历史。B 组每题运行 1 次，定位为外部可比实验，不宣称为基础模型预训练阶段未见数据。

### 2.3 测试数据使用约束

准备阶段不输出题目正文，只输出数量、重叠统计和哈希。A、B 两组一旦生成 `manifest.json`，不得根据题目内容、路由分布或中间结果修改算子、路由器、阈值、评分器或提示词。所有结果均保留，包括失败种子、误触发和负收益。

## 3. 软件结构

新增单一入口模块：

```text
scripts/natural_math_frozen_evaluation.py
```

它提供四个子命令：

```text
prepare-local
prepare-math500
run
summarize
```

复用现有组件：

- `scripts.joint_two_operator_confirmation._verify_installed_records`：核对两个已安装算子；
- `scripts.operator_library.stable_problem_id`：生成题目稳定标识；
- `scripts.router_end_to_end_experiment.EndToEndRunner` 的单题执行逻辑：运行 Baseline 与生产路由系统；
- `benchmarks.math.MATHBenchmark`：答案提取和数学等价评分；
- 现有 JSON/JSONL 读写和 SHA-256 工具。

不得调用算子生成、候选锦标赛、晋级、降级或淘汰逻辑。

## 4. Prepare 阶段

### 4.1 输出目录

A 组：

```text
workspace/MATH/natural_frozen_eval/local_unused_297_v1/
```

B 组：

```text
workspace/MATH/natural_frozen_eval/math500_v1/
```

每个目录包含：

```text
dataset.jsonl
manifest.json
preparation_protocol.json
```

### 4.2 冻结清单

`manifest.json` 至少保存：

- 实验角色、数据集角色和状态；
- 数据源路径、源文件 SHA-256、冻结数据 SHA-256；
- 每道题的稳定 ID；
- 数据条数和重复次数；
- 两个算子的名称、版本、制品 SHA-256 和算子记录 SHA-256；
- 算子库文件 SHA-256；
- 路由器、算子运行时、评分器和实验脚本 SHA-256；
- 请求模型名、供应商、温度、超时和并发数；
- 预注册指标与分析分层；
- 禁止调参、禁止覆盖结果和中断恢复规则。

自然实验使用 DeepSeek 官方当前名称 `deepseek-flash`。配置中可保留旧名称用于复现实验，但新自然实验不得继续把 `deepseek-v4-flash` 写成实际模型版本。每条执行结果同时记录 API 返回的模型名称；若当前 LLM 封装无法取得该字段，则记录请求名称、API 基础地址和调用时间，并在清单中明确“供应商可能更新别名后端”的限制。

### 4.3 准备期校验

准备失败条件包括：

- 源数据为空、存在重复 ID 或必要字段缺失；
- A 组严格未使用题数不是 297；
- B 组题数不是 500；
- 两个算子不是可路由状态；
- 算子制品或依赖文件哈希不匹配；
- 输出目录已经存在；
- 模型配置不存在。

准备成功后状态为 `prepared_unopened`。

## 5. Run 阶段

### 5.1 对照条件

每个 `problem_id + run_index` 执行两个条件：

1. `generic_two_stage_aflow`：原始 AFlow Baseline；
2. `production_two_specialist_routed_aflow`：冻结路由器先选择算子，无安全匹配时回退原始 AFlow。

同一道题的两个条件构成一组配对观测。调度时尽量相邻执行，避免把全部 Baseline 和全部路由方法分成两个相距很久的批次。

### 5.2 单条结果字段

每条记录至少包含：

- 数据集、题目 ID、运行序号和数据分层标签；
- 原题与标准答案；
- Baseline 和完整方法的原始响应、提取答案和得分；
- 错误、超时、Token、模型调用次数和成本；
- 请求模型、实际返回模型和调用时间；
- 路由候选、最终选择、路由证据和被阻止候选；
- 是否回退及回退原因；
- 算子执行轨迹和状态。

### 5.3 增量写入与恢复

执行量较大，结果不得只在整批结束时写入。每完成一组成对比较，立即以 JSONL 追加并刷新到磁盘。唯一键为：

```text
dataset_role + problem_id + run_index
```

重新启动时校验 manifest、数据和代码哈希，读取已有唯一键，仅补齐缺失观测。已有答案、得分和 Token 不得覆盖。每次恢复写入审计事件，包括恢复时间、已有记录数、缺失记录数和启动参数。

如果代码或冻结哈希变化，恢复必须拒绝执行。API 超时和普通模型错误作为正式结果保留，不能自动重试成更好的答案；只有请求未发出、进程崩溃或无法判定是否形成有效响应的基础设施故障才允许补齐，并记录原因。

### 5.4 状态文件

运行目录新增：

```text
comparison_details.jsonl
execution_audit.jsonl
comparison_summary.json
```

状态依次为：

```text
prepared_unopened → running → completed_frozen
```

## 6. 指标与预注册判据

### 6.1 主指标

主指标为 `correct_within_timeout`。分别计算：

```text
delta_accuracy = routed_accuracy - baseline_accuracy
net_wins = wins - losses
```

其中 Win 表示 Baseline 错、完整方法对；Loss 表示 Baseline 对、完整方法错。

总体成功目标为：

- `net_wins > 0`；
- 完整方法总体准确率不低于 Baseline；
- 超时率不高于 Baseline；
- 路由归因回归目标为 0。

自然覆盖率不设最低通过阈值，低覆盖率本身是需要报告的研究发现。

### 6.2 覆盖与安全指标

必须报告：

- 任一算子覆盖率及两个算子的各自覆盖率；
- 回退率；
- 路由子集的 Baseline/完整方法准确率与净胜题数；
- 非路由子集的准确率差；
- 契约满足率和算子执行成功率；
- 误触发、跨算子冲突和路由归因回归；
- 全部观察回归、超时归因回归和非路由生成波动。

算子执行成功率目标为至少 95%，跨算子冲突目标为 0。目标未达到时仍生成完整结果，不允许事后改变阈值。

### 6.3 成本指标

必须报告：

- 总 Token 和平均每题 Token；
- 输入与输出 Token；若供应商封装无法分别提供，则明确记录限制；
- 模型调用次数；
- 总超时数和超时率；
- 每新增一道正确题的 Token；
- 相对 Baseline 的 Token 变化率；
- 实际成本或依据运行当日官方单价估算的成本。

本地代码返回的 `$0.000000` 不得解释为免费，只能解释为价格映射缺失。

## 7. 统计分析

### 7.1 A 组

A 组同时报告：

- 运行级描述统计：891 组成对观测；
- 题目级主统计：297 道题，每题依据 3 次运行形成多数正确状态；
- 以题目为重采样单位的配对 Bootstrap 95% 置信区间。

不得把同一道题的 3 次运行当成 3 道相互独立的题进行显著性推断。

### 7.2 B 组

B 组报告：

- 完整 500 题结果；
- 历史不重叠子集；
- 历史重叠子集；
- 与 A 组重叠子集；
- 以题目为单位的配对 Bootstrap 95% 置信区间。

论文主表使用完整 MATH-500 结果，分层结果用于说明数据重叠风险。

### 7.3 汇总产物

`summarize` 生成：

```text
comparison_summary.json
comparison_report.md
combined_natural_math_report.md
```

合并报告并排展示 A、B 两组的准确率、净胜题数、覆盖率、回归、超时、Token 和置信区间，不把两组题目直接合并为一个总体准确率。

## 8. 预期命令接口

```bash
python -m scripts.natural_math_frozen_evaluation prepare-local \
  --output-dir workspace/MATH/natural_frozen_eval/local_unused_297_v1 \
  --num-runs 3 \
  --model deepseek-flash \
  --timeout 180 \
  --concurrency 4
```

```bash
python -m scripts.natural_math_frozen_evaluation prepare-math500 \
  --source data/external/math500/test.jsonl \
  --output-dir workspace/MATH/natural_frozen_eval/math500_v1 \
  --num-runs 1 \
  --model deepseek-flash \
  --timeout 180 \
  --concurrency 4
```

```bash
python -m scripts.natural_math_frozen_evaluation run \
  --manifest workspace/MATH/natural_frozen_eval/local_unused_297_v1/manifest.json
```

```bash
python -m scripts.natural_math_frozen_evaluation run \
  --manifest workspace/MATH/natural_frozen_eval/math500_v1/manifest.json
```

```bash
python -m scripts.natural_math_frozen_evaluation summarize \
  --local-summary workspace/MATH/natural_frozen_eval/local_unused_297_v1/comparison_summary.json \
  --math500-summary workspace/MATH/natural_frozen_eval/math500_v1/comparison_summary.json \
  --output workspace/MATH/natural_frozen_eval/combined_natural_math_report.md
```

## 9. 测试要求

实现必须增加单元测试，覆盖：

- 历史题排除和 297 题数量守卫；
- MATH-500 题数、字段与重复 ID 校验；
- 历史重叠和 A 组重叠标签；
- manifest 哈希冻结和代码变化拒绝；
- 算子记录变化拒绝；
- 单条配对结果结构；
- 增量写入、重复键跳过和中断恢复；
- 超时保留而非自动洗掉；
- 覆盖率、Win/Tie/Loss、净收益和回归归因；
- A 组题目级多数统计；
- 题目级 Bootstrap 的固定随机种子复现；
- A、B 合并报告不错误合并分母。

测试全部使用假 LLM 和小型夹具，不产生 API 费用。

## 10. 验收标准

实现完成应满足：

1. `prepare-local` 在当前仓库严格得到 297 道题，否则拒绝冻结；
2. `prepare-math500` 只接受恰好 500 道唯一题；
3. manifest 能检测数据、算子、路由器和评分器变化；
4. 运行中断后只补齐缺失唯一键；
5. 正式运行不会生成、晋级或修改任何算子；
6. 汇总同时给出总体、路由子集、非路由子集和重叠分层结果；
7. 单元测试不调用真实模型且全部通过；
8. 未执行正式付费实验前可以完成全部本地验证。

## 11. 成本和运行安排

按现有五随机种子实验的平均 Token 外推，A、B 两组预计约 1364 万 Token。按 2026 年 9 月 15 日 DeepSeek Flash 价格，非高峰预计约 28～45 元，加入 20% 余量约 34～54 元；建议账户准备 70 元。正式执行安排在工作日北京时间 18:10 后或周末，并按 A 组、B 组顺序运行，避免多个实验目录并行造成限流和时间条件混杂。
