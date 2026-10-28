# 自动化离线闭环：使用说明

入口：`python -m scripts.offline_operator_loop`。请在 AFlow 仓库根目录、项目虚拟环境中运行。

2026-10-08：需要从已提出的机制假设出发做独立补证、语义审核后生成候选时，使用[独立补证入口](independent-mechanism-evidence.md)。它复用基线与生成编译器，保持候选状态；本页主闭环仍使用原题面特征分组，不会自动把补证候选晋级。

## 现在能自动完成什么

训练来源数据划分 → 基线重复答题 → 重复失败筛选及特征分组 → 模型诊断与候选规格生成 → 固定骨架编译 → 开发集锦标赛 → 冻结一个候选 → 独立隐藏确认和非专项回归 → 独立算子库晋级 → 检查真实路由器可达性。

每个运行最多处理一个机制假设、晋级一个候选。没有稳定失败、没有合格候选或隐藏验证不通过，都会正常归档；不会为了凑算子数量降低门槛。

**当前是已有受约束模板生成器的自动编排，不是任意新算法程序的通用合成。** 支持 single_step、two_stage、verify_fix；本版不开放自由 Python 生成执行。算子元数据沿用现有特征词表，因此也不是开放域机制发现。

## 先做免费自检

```bash
cd /Users/majiatong/lab/AFlow
source .venv/bin/activate
python -m scripts.offline_operator_loop --help
python -m unittest discover -s tests -p 'test_offline_operator_loop.py' -v
```

这些测试不调用真实 API。集成测试通过模拟模型传输，实际经过规格解析、骨架编译、MATH 评分器、安装以及路由器；测试结果不是研究准确率，也不是新能力发现的证据。

2026-09-26 数据准备后的完整回归共 145 项通过。已补装 `antlr4-python3-runtime==4.11.1`，修复 LaTeX 不完整表达式被部分解析、三角函数中的 pi 未绑定为常量两处问题；评分器版本为 `math_equivalence_v8`，28 项人工核对的正反例全部通过。旧实验文件未重算；旧评分版本和新版本结果不能直接混合。有限探针仍不是完整数学等价性的证明。

2026-09-29 的真实试跑旁路审计又发现选择题字母和普通文本大小写两类表示误判。当前评分器升级为 `math_equivalence_v9`：只有题面含完整明确的 `(A)…` 选项块时才映射单个大写字母；只有至少两个字母的纯文本标签忽略大小写，单字母数学变量仍区分大小写。完整回归为 150 项；冻结试跑的复评报告单独保存，原运行结果未回写。

可直接启动的本次配置、数据来源与审计记录见 [2026-09-26 试跑说明](math-train-pilot-20260926.md)。

本入口复用旧数学评分器，未为符号解析增加操作系统级沙箱。180 秒约束的是求解及回退链路，不是符号评分的硬 CPU 上限。应在可信训练数据和受控研究环境中使用；不应把该原型作为面向恶意输入的公共服务。

## 准备独立训练数据

已准备 `data/datasets/math_train_independent.jsonl`（7,487 题），来自固定版本的原始 train 划分，保留原题和参考解答。下载、来源核验与已知重叠排除由 `python -m scripts.prepare_independent_math_train` 完成；本入口只读取结果。**不能把 test/validate 改名当训练集，也不能把 MATH-500 用于发现。** 默认数据文件已存在，不要重复运行下载脚本覆盖它。

提供 JSONL，每行至少包含两个非空字符串：

```json
{"problem":"题目文本","solution":"参考解答，包含明确的最终答案","split":"train","type":"Algebra","level":"Level 3"}
```

- `split` 如提供，必须是 train/training；训练来源还需通过 `--source-name` 声明。这是用户提供的来源声明，不是系统核验了原始下载出处。
- `type`/`subject`、`level` 可选。先按固定种子抽样，再近似平衡地分配所抽到的学科/难度组；不是严格分层随机抽样协议。
- 同一规范化题面去重，冲突参考解答拒绝导入。同一个 `source_id` 的不同题面变体会被拒绝，需先提供按原题分组隔离的数据。
- 自动排除本地 `math_validate.jsonl`、`math_test.jsonl`；自动排除同级运行目录及标准 `workspace/MATH/offline_runs` 下此前本入口已预留的数据。
- 其他历史使用过的数据应通过重复的 `--exclude-data 路径` 指定。程序没有完成整个旧工作区的所有格式/人工使用历史审计。
- 换数字、改写题面、训练模型是否预见过公开题，不能靠文本哈希证明独立。

## 小规模试跑：先 prepare，再明确启动

下面用 160 条独立训练题测试实际流程。它不是足以支撑论文结论的正式样本量；某一题型在隐藏池中不足时，会归档而不是自动补题或挑题。

先把 `YOUR_OPT_MODEL`、`YOUR_EXEC_MODEL` 替换为你配置中实际可用的模型名称。可以只查看名称，不输出 API 密钥：

```bash
python -c 'from scripts.async_llm import LLMsConfig; print(LLMsConfig.default().get_all_names())'
```

确认独立训练文件已经准备好，再执行：

```bash
python -m scripts.offline_operator_loop prepare \
  --output-dir workspace/MATH/offline_runs/pilot_001 \
  --data data/datasets/math_train_independent.jsonl \
  --source-name "EleutherAI/hendrycks_math@21a5633873b6a120296cce3e2df9d5550074f4a3; original train; local overlap audit 2026-09-26" \
  --source-split train \
  --discovery-count 40 \
  --design-count 40 \
  --gate-count 40 \
  --regression-count 40 \
  --candidate-count 2 \
  --discovery-runs 3 \
  --evaluation-runs 3 \
  --opt-model YOUR_OPT_MODEL \
  --exec-model YOUR_EXEC_MODEL \
  --timeout 180 \
  --max-requests 3000 \
  --max-output-tokens 4096
```

`prepare` 不调用模型、不收费。它固定输入、参数、执行代码、评分/API 依赖版本、基线模板和现有可路由算子副本；已有目录禁止覆盖。

对于官方 DeepSeek 接口，可增加 `--thinking-mode disabled`，让基线、生成器和候选都固定为非思考模式。本次准备好的试跑已经使用该设置；默认 `provider-default` 则不传该选项。模式属于冻结协议，不能在半途修改。输出截断或空响应会作为执行异常排除，不用它们制造稳定数学失败。

它会先检查模型名称是否存在于本地配置，并输出“全部阶段都执行时的保守请求数上界”。实际通常因拒绝、提前停止及范围筛选而更少；如果你设定的预算低于上界，程序会提醒可能中途达到预算。这个上界不是价格估计。

检查协议后，下面这条才会调用真实模型、产生费用：

```bash
python -m scripts.offline_operator_loop run \
  --output-dir workspace/MATH/offline_runs/pilot_001 \
  --allow-api
```

只需一次 `run`，各阶段自动衔接。它不会改变原有 `workspace/MATH/workflows/template`、原算子库或五随机种子的结果。

## 中断、恢复与预算

Ctrl+C 后用**完全相同的 run 命令**恢复。逐题执行结果、生成请求和各阶段结果有检查点，不会从头重新计费。

- 如果某个请求已经发送，但中断时未保存响应，其是否计费无法确定。恢复时按未知用量的失败处理，不自动重发该请求。后续已规定的其他请求仍可继续。
- SDK 自动重试关闭；格式修复是单独的模型请求，会记账。
- `--max-requests` 限制真实请求的预留次数，`--max-output-tokens` 限制每个请求的输出。它们**不是人民币费用上限**；输入长度和价格仍会影响账单。
- 达到请求预算后状态为 `budget_exhausted`。预算属于冻结协议，当前版本不允许在原运行中临时加预算；因此正式运行前应估算完整流程的请求量。
- 程序保存已报告的输入/输出 Token、未知用量请求数和供应商返回的模型标识。金额显示为未知，不把未配置价格的模型显示成“免费”。以供应商账单为准。
- 运行文件有单写锁，不能同时启动两个 run。可以在另一个终端用 status 读取原子状态快照。
- 数据、模型参数、运行代码、评分/API 依赖版本或冻结候选改变，会拒绝续跑。模型别名背后服务端变更不能仅靠本地哈希阻止，论文仍需记录真实运行时间和供应商模型版本。

```bash
python -m scripts.offline_operator_loop status \
  --output-dir workspace/MATH/offline_runs/pilot_001
```

## 默认判定规则和含义

发现阶段：所有发现题均运行 3 次；单题至少 2 次**无执行异常的错误**才算重复失败；同一非空特征组至少 3 道不同题重复失败，且该组有效执行的观察错误率至少为 0.20，才能触发生成。该比率不是经过人工评分审计的“审计错误率”，也不是能力缺口的因果证明。超时/接口异常另行记录，不冒充机制性错误。

开发和隐藏确认复用 `PromotionPolicy` 与 `QualityWeights`。默认硬门槛包括至少 6 个专项观察、2 道不同专项题、2 道不同获胜题、净胜至少 2 次、回归损失比例不超过 0.10、专项拒绝比例不超过 0.25、候选错误比例不超过 0.10。此外，本入口要求至少 3 道不同非专项回归题，以及至少 2 道无基线执行异常、实际使用候选而获胜的专项题。

这些是**本入口的原型默认协议**，不是已经论证最优的阈值。0.10 的回归阈值沿用旧锦标赛，并不等于自然分布实验中另行约定的 0.05；不能混用实验结论。需要改变正式阈值时，应先扩展并冻结相应协议，再开始新运行，不能看过隐藏结果再改门槛。

开发集先选出一个候选。隐藏专项题由发现阶段已冻结的特征组确定；非专项回归题从独立回归池选取该组之外的题，只按题面特征，不看模型结果。候选、题号和阈值写入 `confirmation_freeze` 后才开始隐藏确认。

专项验证直接检查候选能力；非专项验证真实执行候选/拒绝/基础工作流回退。**回退也重新运行并记录成本，不把基线答对直接当作回退答对。** 配对题目的执行顺序由种子固定。开发集多个候选共享已保存的基线输出。

隐藏失败只归档，不能再用同一隐藏集挑第二名。通过者获得与版本、哈希和作用域绑定的隐藏证据，然后由真实路由器检查是否能够选择并解析该算子。如果与旧算子冲突、证据不够或路由分数不足，不会把它报告为成功晋级。

注意：最后的路由检查验证了可达性及安装完整性，**不是扩大算子库后的完整端到端自然分布性能评测**。两类实验仍需分开。

## 产物与状态

运行目录中的主要文件：

| 路径 | 用途 |
|---|---|
| `manifest.json` | 数据角色、来源声明、参数、代码和输入哈希 |
| `inputs/` | 四份冻结数据及原有可路由算子快照 |
| `base_template/` | 只读基线模板副本 |
| `requests/` | 请求状态、输出、Token、未知用量；不保存密钥 |
| `checkpoints/screen.json` | 重复失败统计、特征分组及选择理由 |
| `checkpoints/generation.json` | 诊断和结构化候选规格 |
| `work/operator_library/` | 未发布的原始候选，保留 candidate 状态作为审计材料 |
| `checkpoints/design_ranking.json` | 开发集候选排名及指标 |
| `checkpoints/confirmation_freeze.json` | 独立确认前的候选及样本冻结记录 |
| `checkpoints/gate_metrics/` | 隐藏专项和回归的配对记录 |
| `release/` | 晋级尝试的独立模板、算子库和哈希；是否可用以 summary 为准 |
| `summary.json`、`state.json` | 最终结论与当前状态 |

`promoted` 才表示闭环成功晋级。`archived_no_stable_mechanism`、`archived_generation_failed`、`archived_design_failed`、`archived_hidden_failed`、`archived_route_not_selectable` 等都表示正常拒绝结果，不是必须修成通过的程序错误。独立样本数量不足会以 `archived_insufficient_*_data` 结束。

## 如何使用晋级后的独立副本

不要把 release 文件直接复制覆盖原有双算子系统。用提供的加载接口校验冻结哈希并建立路由器：

```python
from scripts.async_llm import LLMsConfig, create_llm_instance
from scripts.offline_operator_loop import load_released_router

llm = create_llm_instance(LLMsConfig.default().get("YOUR_EXEC_MODEL"))
router = load_released_router("workspace/MATH/offline_runs/pilot_001", llm)
# response = await router("一道新的题目")  # 这一行会产生实际模型调用
```

加载本身不会调用 API。只有已完成且 summary 标明 promoted 的发布副本可加载。后续扩大库的联合评估和正式自然 MATH 测试，应另建冻结协议；这里发现/选择/晋级阶段的分数不能作为最后的泛化成绩。

## 研究表述边界

可以说：“实现了训练数据驱动的离线候选生成、独立确认和证据入库的自动编排。”

目前不能据此说：“已实现任意能力的自主算法合成”“自动证明了稳定失败的因果机制”“完全消除了数据泄漏”“已证明自然 MATH 分布净收益”。这些仍需要后续算法扩展、评分审计和真实独立实验。
