# 已有资源受限失败的可执行算子闭环

本入口复用已有素数实验的数据与真实响应，不新建题库、不重新校准、不寻找数学错答。它与原语义审核入口并列；不会把超时/截断改写成数学错误，也不把旧算子重新命名为新生成结果。

## 路径

历史基线完整性检查 → 资源受限失败筛选 → 两次模型生成新的 solve(problem) 源码 → 冻结实现 → 开发比较 → 冻结一个胜者 → 确认和边界回归 → 私有实验库发布或归档。

- 发现阶段直接导入已运行的 restricted_prime/evidence_run。核对 manifest、冻结输入、原始请求和完整观察哈希；只读旧源码副本，不运行它，因此允许历史代码版本与当前运行器不同。没有重新采集发现基线。
- 至少 3 道目标题，每道至少 2 次完成错答、超时或输出截断，才允许生成。网络等基础设施故障不计支持。
- 生成器仅收到范围、发现题、参考结果及失败类型/回答片段。完整原始回答保留在快照。两个旧专项实现、开发/确认题及其答案均不进入生成请求。
- 模型分别生成两个候选，代码通过校验后注册为新的冻结版本。所有评测题执行同一份 solve(problem)，不逐题重新写程序，不恢复旧 DP/素数工具，不提供旧代码。
- 候选范围外真实回退基线。开发和确认都进行基线/候选配对。超时/截断改进可以计为胜利；网络故障从有效胜负中排除，存在无效配对时禁止晋级。
- 开发选定一个候选后才开始确认；确认失败只归档，不试第二名。发布只写此运行的 release，原生产库不变，并用真实路由器核验可达性。

## 执行边界

生成的算法允许有限整数计算、容器、循环、递归，以及 math/itertools/re/collections/functools/fractions 白名单。AST 拒绝文件、网络、进程、动态代码、反射及未允许的属性。每题在 `python -I` 的独立工作进程和临时目录执行，使用清理后的环境，6 秒墙钟上限、5 秒 CPU 上限；超时/取消会终止进程并等待回收。RSS 256 MiB 是采样监控阈值，尤其在 macOS 上不是硬地址空间上限。此机制是窄用途受限执行器，不是通用恶意 Python 的强安全沙箱。

每次执行记录源码、源码哈希、题面哈希、退出码、输出和错误；原始模型生成请求、解析失败、校验拒绝也归档。生成格式无效不会偷偷改用旧算子。执行失败不混记为网络故障。

## 本次准备

源发现记录：`results/specialist_replay_20261009_setup/restricted_prime/evidence_run`。

已有 3 道目标题各 3 次失败（其中一次完成错答，其余截断）；一条边界网络故障不计支持。复用此前已有 design.jsonl（3 目标+3 边界）、gate.jsonl（3 目标）、regression.jsonl（3 边界），不生成新题。

固定条件：执行和生成模型沿用源配置，输出上限 8192，thinking disabled，基线求解超时 180 秒，评测重复 3 次。后半程请求上限 160，保守计划最多 128 个新 API 请求，包括两次生成、开发/确认基线和边界回退；本地代码执行不调用模型。金额以供应商账单为准。可以因无效生成、开发失败或确认失败提前归档。

## 命令

准备（本次已完成，不需重复）：

```sh
.venv/bin/python -m scripts.operational_operator_replay prepare \
  --output-dir workspace/MATH/offline_runs/prime_operational_replay_20261009_001 \
  --evidence-run results/specialist_replay_20261009_setup/restricted_prime/evidence_run \
  --design-data results/specialist_replay_20261009_setup/restricted_prime/design.jsonl \
  --gate-data results/specialist_replay_20261009_setup/restricted_prime/gate.jsonl \
  --regression-data results/specialist_replay_20261009_setup/restricted_prime/regression.jsonl \
  --max-requests 160
```

真实执行（会产生模型调用费用）：

```sh
.venv/bin/python -m scripts.operational_operator_replay run \
  --output-dir workspace/MATH/offline_runs/prime_operational_replay_20261009_001 \
  --allow-api
```

免费检查状态：

```sh
.venv/bin/python -m scripts.operational_operator_replay status \
  --output-dir workspace/MATH/offline_runs/prime_operational_replay_20261009_001
```

中断后使用同一 run 命令恢复；已记录请求不重复发出，未知结果不自动重试。修改源码或冻结文件后禁止继续，不能改 manifest 绕过校验。

## 解释边界

这是已知构造题上的历史闭环重演；gate 对本轮生成器不可见，但不是研究人员未见过的新自然题。新算子具有代码执行能力，而基线为两阶段文本求解；结果不能单独证明专项算法优于“通用 Programmer”对照，也不能把工具权限增加的收益全归因于特定推理机制。

本次实现通过 109 项测试，包含资源失败→生成→确认→隔离发布的模拟链路，以及真实本地进程执行、超时回收、禁止访问、篡改拒绝、网络故障不晋级、隐藏失败不换候选。没有在实现阶段发起新的付费请求；真实运行是否晋级由上述命令的结果决定。
