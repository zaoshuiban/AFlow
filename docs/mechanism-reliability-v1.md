# 自动可靠性检查第一版

入口：`scripts/mechanism_reliability.py`。只读取已保存数据，不调用模型，不晋级或安装算子。

本版实现引用核验、有限诊断一致性检查、结构化题面筛选、独立证据去重及错误率统计、三态判定。未实现分批运行编排和自动补证选题，因此不是完整的数据收集闭环。

2026-10-08 新增独立入口 `scripts.independent_mechanism_evidence`，实现事前冻结、完整补证采集、具名语义审核和候选生成接口，见[使用说明](independent-mechanism-evidence.md)。本文件描述的旧检查器保持原行为；不会把旧 `semantic_verified` 字段直接改为通过。

## 输入协议

JSON 包含 `scope`、`frozen_scope_sha256`、`discovery_ids`、`rows`、`observations`、`packet`、`response`。

- scope：mechanism_id、required_terms、forbidden_terms；大小写敏感的字面筛选。它只是候选范围，不等于已经证明正确的数学契约。
- frozen_scope_sha256：使用 offline_loop_protocol.digest(scope)，应在补证选题前保存。事后计算同一个哈希不能证明事前冻结；当前检查器不能单独验证时间先后。
- discovery_ids：历史发现题目编号；补证不得重用。
- rows：包含 problem_id、problem、solution 的独立补证题，包含范围内题与邻近对照题。
- observations：包含 problem_id、run、prediction、score、error 的完整运行记录。执行异常不进入准确率分母；成功回答必须保留。
- packet、response：编号引用归纳模块的原始输入与模型响应。诊断题面、参考解答和失败原文必须与补证记录完全一致。

执行：

```bash
.venv/bin/python -m scripts.mechanism_reliability \
  --input results/independent_evidence.json \
  --output-dir results/reliability_new_run
```

输出目录必须是新目录。输入哈希存入 report.json。

## 决策边界

出现目标机制的明确约束矛盾：refuted（该候选诊断存在反证，不代表整个能力方向被否定）。

统计满足至少3道稳定失败、每题至少2次有效错误、有效执行错误率至少0.20，还需至少3道独立语义确认，才能 independently_supported。

其他情况：insufficient_evidence。

现有诊断规则始终 semantic_verified=false，因此本版不会仅凭“未发现矛盾”输出独立支持。需要后续增加可独立核验特定机制的检查器。对照题目前统计数量、保留诊断，不自动判断所有对照误触发。阈值是沿用的探索标准，不构成显著性检验或因果证明。
