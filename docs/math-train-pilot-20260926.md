# 独立 MATH 训练数据与离线闭环小规模试跑

准备批次：2026-09-26。目录保留该日期，不因跨日继续操作而更名。

数据、依赖核验和 `prepare` 已完成。尚未调用真实模型，模型请求数为 0；下面的 `run --allow-api` 由你执行，会产生费用。没有检查远端鉴权、余额和实时服务可用性。

## 一、现在直接运行什么

在终端完整执行：

```bash
cd /Users/majiatong/lab/AFlow
source .venv/bin/activate

python -u -m scripts.offline_operator_loop run \
  --output-dir workspace/MATH/offline_runs/pilot_20260926_001 \
  --allow-api
```

不用重新下载数据，也不用再次 `prepare`。请保持电脑联网、不要休眠。同一目录只启动一个运行进程。

在另一个终端查看状态：

```bash
cd /Users/majiatong/lab/AFlow
source .venv/bin/activate

python -m scripts.offline_operator_loop status \
  --output-dir workspace/MATH/offline_runs/pilot_20260926_001
```

Ctrl+C 中断后，用原来的 `run` 命令续跑。已完成并保存的请求不会重新发送；已发送但响应未保存的请求会按未知用量失败记录，不自动重发。运行中不要修改代码、输入、参数或依赖，否则冻结校验会拒绝续跑。

## 二、这次试跑的规模与目的

| 设置 | 已冻结的值 |
|---|---|
| 发现池 | 40 道训练题，每题重复 3 次 |
| 开发池 | 40 道训练题 |
| 隐藏确认池 | 40 道训练题 |
| 非专项回归池 | 40 道训练题 |
| 独立题目总数 | 160，四池题面无已知精确或排版规范化重叠 |
| 抽样种子 | 170101，仅控制本地抽样/顺序，不是服务端采样种子 |
| 候选数 | 2 |
| 开发/确认重复次数 | 每题 3 次；实际执行题目由冻结适用范围筛选 |
| 优化模型、执行模型 | 均使用现有配置名 `deepseek-v4-flash` |
| 思考模式 | 显式 `disabled`，基线和候选一致 |
| 求解及回退链路超时 | 180 秒；不是数学符号评分的硬 CPU 上限 |
| 单次请求输出上限 | 4,096 Token |
| 请求预算上限 | 3,000 次 |
| 全阶段保守请求数上界 | 2,646 次；实际可能因范围筛选或正常归档少很多 |

160 道题不等于 160 次调用：重复答题、多阶段求解、候选对比都会增加调用。请求预算与 Token 上限都不是人民币费用上限，以供应商账单为准。程序记录已报告 Token 和未知用量，不会把未知价格写成“免费”。

这次目的是检查真实 API 下的离线流程能否贯通：发现重复失败 → 诊断与规格 → 两个候选 → 开发筛选 → 冻结一个候选 → 隐藏确认和回归 → 独立库晋级与路由可达性检查。

**不是正式自然 MATH 测试集评测，也不保证一定生成合格的新算子。** 每次运行最多处理一个机制假设、晋级一个候选；当前生成器仍是固定骨架下的提示词型算子，不是任意新算法代码合成器。

## 三、训练数据从哪里来

使用保留原始划分的 [EleutherAI/hendrycks_math 固定版本](https://huggingface.co/datasets/EleutherAI/hendrycks_math/tree/21a5633873b6a120296cce3e2df9d5550074f4a3)。版本：`21a5633873b6a120296cce3e2df9d5550074f4a3`。

七学科原始 train 合计 7,500 题，test 合计 5,000 题。test 仅用于重复排除，没有进入发现/生成提示词。不能因为某个镜像将全部 12,500 题都标成 train，就将它们全部用于开发。

最终文件：`data/datasets/math_train_independent.jsonl`，保留 7,487 条。原题、解答、学科和难度不改写，补充 split、来源文件、来源行号、版本和题目 ID。

| 排除原因 | 条数 |
|---|---:|
| 与原始 test 精确题面重复 | 1 |
| 与原始 test 排版规范化后重复 | 2 |
| train 内部重复，保留其中一条 | 2 |
| 规范化题面相同但提取的最终答案冲突，保守隔离 | 6 |
| 提取的最终答案为空 | 2 |
| 合计 | 13 |

本次扫描本地 `workspace/MATH`、`results`、`data/datasets` 下 561 个 JSON/JSONL/CSV 文件，解析错误为 0；在所识别的任务字段与题目 ID 范围内，剩余数据未发现历史重叠。**不包含非结构化日志、文档、人工阅读史的穷尽审计，也不能证明公开题未被模型预训练见过。** 因此应表述为“原始训练划分、与已扫描历史及测试题无已知文本重叠”，不是“绝对全新未见”。

JSONL SHA-256：

```text
902a65bf8fae9dd0033735e5785568b42d389f324d0fb34eff824a9f1cfff4b7
```

审计文件：

- `results/math_train_readiness_20260926/data/manifest.json`：来源版本、分片哈希、数量、分布、输出哈希。
- `results/math_train_readiness_20260926/data/excluded_rows.jsonl`：逐条排除理由。
- `results/math_train_readiness_20260926/data/history_scan.json`：扫描范围、文件哈希和解析情况。
- `data/sources/hendrycks_math_21a5633873/`：原始下载缓存与下载校验记录。

后续若重新准备数据，历史实验已增加，排除结果可能变化；本次实验以已保存的文件、来源/排除记录和哈希为准，不覆盖重建。

## 四、评分器检查与修复

已安装与 SymPy 1.14 配套的 `antlr4-python3-runtime==4.11.1`，另安装 `pyarrow==25.0.1` 用于读取来源 Parquet。ANTLR 版本要求见 [SymPy 官方文档](https://docs.sympy.org/latest/modules/parsing.html#runtime-installation)。依赖已写入 `requirements.txt` 与数据准备专用的 `requirements-math-data.txt`。

评分探针结果：

| 阶段 | 通过情况 | 发现 |
|---|---:|---|
| 补依赖前，v7 | 23/28 | 部分根式、三角、对数、组合数表达式无法正确判等 |
| 仅补 ANTLR 后，v7 | 26/28 | 三角表达式中的 pi 仍被当作变量；`1+` 被部分解析为 `1` |
| 修复后，v8 | 28/28 | 显式绑定 LaTeX pi 常量；启用严格完整解析 |

修复后的版本是 `math_equivalence_v8`。同时增加不完整表达式、三角函数等回归用例，完整项目测试 145 项通过，`pip check` 无依赖冲突。有限测试不代表覆盖了所有数学表达式。

后续真实试跑发现 v8 仍会把选择题字母和普通文本大小写差异判错。2026-09-29 已升级为 `math_equivalence_v9`，并使用保存答案在独立目录完成旁路复评：120 次观察中修正 9 次格式误判，复评后为 109 次正确、3 次真实判错、8 次执行异常；只剩 1 道含 Asymptote 图形的题稳定失败，没有分组达到原生成门槛。原运行的 372 个文件哈希保持不变，未产生新模型调用。详见 `results/math_train_readiness_20260926/pilot_rescore_v9/`。

原始检查记录保存在 `results/math_train_readiness_20260926/scorer_before.json`、`scorer_after_dependencies.json`、`scorer_after_fix.json`。旧实验输出没有被重算或覆盖；不要把新评分器结果直接并入旧评分版本统计。

本次运行还冻结 Python、SymPy、ANTLR、mpmath、regex、OpenAI SDK、Pydantic 版本，避免“代码没变，但补装依赖后评分变了”。

## 五、模型版本与异常边界

按 [DeepSeek 官方更新说明](https://api-docs.deepseek.com/updates/)，`deepseek-v4-flash` 目前兼容路由至 V4.1 Flash，不再表示 8 月时的 V4 Flash。这里沿用现有本地配置名，没有修改账户密钥或旧实验配置；实际响应模型标识会写入请求记录。

官方现行接口默认开启思考模式，见 [思考模式说明](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)。本次为低成本流程试跑显式关闭，模式相同地应用于基线、生成器和候选；不能将这次结果与旧实验直接合并。

输出因 Token 上限截断、响应为空、超时或接口错误，都按执行异常记录；不能将它们当作稳定数学能力缺口。已经产生的用量仍保留，不因失败而假定不收费。真实 API 尚未由本次准备工作验证。

## 六、跑完后看什么

主要查看：

```text
workspace/MATH/offline_runs/pilot_20260926_001/summary.json
workspace/MATH/offline_runs/pilot_20260926_001/state.json
workspace/MATH/offline_runs/pilot_20260926_001/checkpoints/screen.json
```

后续阶段实际到达后，才会生成候选、排名、隐藏确认和发布产物。不要把某个文件不存在直接视为程序失败。

- `promoted`：有一个候选通过本次门槛，进入本次运行独立库，并通过路由可达性检查；不代表已经证明自然分布收益。
- `archived_no_stable_mechanism`：当前发现样本不足以触发生成，或有效重复失败不足。是正常终止，不为了产出新算子降低门槛。
- `archived_generation_failed` / `archived_design_failed`：候选生成或开发筛选未通过。
- `archived_insufficient_*_data`：冻结的独立池中符合题型要求的样本不足，不自动从隐藏结果中挑题补题。
- `archived_hidden_failed`：独立确认未通过，不用同一隐藏集继续选第二名。
- `archived_route_not_selectable`：候选无法被真实路由器可靠选中，不报告晋级成功。
- `budget_exhausted` / `blocked`：先检查预算或错误记录，不修改冻结协议强行继续。

可以把终端结尾与 `summary.json` 发来，下一步先核对接口异常、评分、机制样本数和正常归档原因，再决定是否扩大独立发现数据。不要只看“有没有晋级”。

本次预检记录：`results/math_train_readiness_20260926/pilot_preflight.json`。原双算子库、原工作流模板和已有正式实验结果均保留；试跑只使用独立目录中的副本。
