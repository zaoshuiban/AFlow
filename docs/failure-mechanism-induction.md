# 失败回答参与机制归纳

本模块是独立发现诊断入口，使用已保存的discovery回答，输出可追溯机制假设。它可以跨题面特征归组，尚不取代主闭环的生成触发条件，也不改变后续路由。当前版本通过人工语义审核后才能设计下一轮独立协议。

实现范围：错误回答、参考解答、违反的约束、证据片段编号、适用输入、反例边界参与归纳；执行异常与非稳定失败排除。模型提出机制，程序逐观察核验片段编号归属并回填原文，再计算不同题目支持数。相同题的多次重复不当作多个题。unknown与scoring_ambiguity不支持机制分组。下方旧目录命令属于原句引用协议；当前使用文末v3编号协议。

这是真实的模型诊断接口，但仅原句存在性可以程序验证；跨题机制语义、首次偏离是否正确以及因果解释仍需审查。失败样本无法提供总体错误率，不将其错误率报告成100%。当前结果全部eligible_for_generation=false，以免把失败后归组直接作为生成准入。要自动触发新算子，需要先冻结可从题面执行的机制适用条件，并在完整发现池上重新计算范围与支持证据；这一环节属于后续协议扩展。

## 免费准备

```bash
python -m scripts.failure_mechanism_induction prepare \
  --source-run workspace/MATH/offline_runs/discovery400_20260929_001 \
  --rescore-dir results/math_scorer_audit_20260930/discovery400_rescore_v10 \
  --output-dir results/mechanism_induction_20261008
```

仅读取发现题与其保存回答，校验原检查点和复评来源；不读取设计、隐藏门控或回归题。保存packet.json、prompt.txt、manifest.json，冻结全部原运行文件哈希。当前400题的v10旁路复评对应4道题、10条有效失败回答。

## 模型归纳：由用户启动

```bash
python -m scripts.failure_mechanism_induction run \
  --output-dir results/mechanism_induction_20261008 \
  --allow-api
```

复用原实验opt_model、模型参数、thinking_mode、4096输出上限、180秒请求上限，最多预留1个模型请求，不自动重试。此命令会产生模型费用。请求记录、用量、原始回答独立保存在新目录。重启复用保存请求；已成功导出的结果禁止覆盖。

若响应不是合法JSON或证据格式无效，保持原始响应和请求记录，不自动付费修复。可以离线检查响应后导入新的JSON文件，但导入来源会被标注为用户提供，不能称为未经修改的自动结果：

```bash
python -m scripts.failure_mechanism_induction import \
  --output-dir results/mechanism_induction_20261008 \
  --response /absolute/path/reviewed_response.json
```

最终产物：response.json、result.json、report.md。准备完成只表示接口和证据就绪，不表示真实模型已完成归纳；本次实现没有进行真实API调用。

## 下一轮检查点

先审查是否能把圆锥精确量替换，与其他三题的目标／位置错误区分开；再审查后三题是否确实存在同一可操作约束，允许分成不同机制或unknown。已有4题已用于开发，不能作为未见最终测试。主闭环当前仍使用固定特征分组；本模块完成的范围是失败语义假设提出与证据核验。

## 2026-10-08 输出截断修复

首次真实请求达到4096输出Token上限，finish_reason=length；输入14123、输出4096，总计18219 Token。JSON在groups列表中间截断，不作为完整归纳结果。该请求已发送并报告用量，以供应商账单为准。

新准备目录为results/mechanism_induction_20261008_v2，输出上限8192；提示词要求各项说明简短。预算改变只作用于机制归纳，原发现实验仍使用其冻结预算。修复后的失败请求会输出failure_summary.json及保存的raw_response.txt，不会自动修补截断JSON或重新发请求。

新目录已免费准备，可以直接执行：

```bash
cd /Users/majiatong/lab/AFlow
.venv/bin/python -m scripts.failure_mechanism_induction run \
  --output-dir results/mechanism_induction_20261008_v2 \
  --allow-api
```

该命令会发送新的模型请求，可能产生新费用。旧目录保留为失败证据；修改后的脚本哈希不同，不能用它直接续跑旧目录。8192是新的输出上限，不保证模型一定在该范围内完成。

## v3：原文片段编号协议（当前）

packet schema_version=2。每次失败回答按原文行及空行分段，单段不超过800字符；所有段按顺序拼接可逐字恢复回答。每个编号包含题目ID、运行次数、片段序号，例如`0cf4595f03201e31/r1/f0007`。保存start/end字符位置，区间为左闭右开。编号仅在冻结的packet内稳定。

模型每条分析返回`evidence_ids`列表（1至3个编号），不返回error_quote。程序检查这些编号确实属于所声明的题目和运行，拒绝未知、重复、跨题、跨运行或空白片段。通过后由程序取回原文，保存source_fragments和error_quote，并将semantic_review_status标为pending。模型即使同时返回自写error_quote，也不会用于证据，程序以原文覆盖。

input packet保存完整回答与片段以便核验；发送模型的prompt只包含片段，避免重复发送整段回答。旧schema=1仍可离线按严格原句模式校验，但旧目录不能在实现哈希改变后续发模型请求。

当前已免费准备4题、10条失败回答。启动命令：

```bash
cd /Users/majiatong/lab/AFlow
.venv/bin/python -m scripts.failure_mechanism_induction run \
  --output-dir results/mechanism_induction_20261008_v3 \
  --allow-api
```

输出预算8192，最多1次模型请求。需要重新准备时使用prepare、原source-run和rescore-dir，并传入`--max-output-tokens 8192`与一个未使用的新目录。

编号校验不自动证明模型选的是正确的错误位置，不自动验证机制解释或反例。report.md列出原文、编号、偏移及待审状态，并列出未通过校验的分析。旧v2真实结果保留，本次没有将其改写为v3自动结果，也没有新增付费请求。

## 诊断约束一致性检查

当前增加diagnostic_constraints_v1，接在引用核验之后。新归纳结果为每条分析保存constraint_review和semantic_review_status；分组额外报告noncontradictory_problem_count、constraint_contradiction_count。原distinct_problem_count仍表示引用支持的不同题目数，不等同于可信机制支持数。

支持的检查形式：

- 题面显式给出数值平方根半径，参考解答有一个数值距离平方时，比较距离平方与半径平方，检查诊断关于圆内／圆外的肯定断言及相关反例。
- 参考解答显式声明某三字母角为平角，而诊断把x绑定到其两条相反射线时，标记冲突。
- 参考中含相关相似三角形关系、目标线段明确时，检查把倒数和的倒数误称调和平均的问题，并提醒适用范围缺少端点或错误归因于交点高度。

这些规则不按题目ID分支，也不执行Asymptote源码；依赖可识别的有限表达形式和参考解答。not_covered表示未覆盖，needs_review表示需要审查，limited_checks_consistent只表示已覆盖检查未发现矛盾。上述任何状态都不能证明完整诊断正确。contradiction单独显示，不能成为自动晋级证据；当前所有分组仍不自动触发生成。

旧v3已用保存的响应离线复查，报告在results/diagnostic_constraints_20261008_v1/report.md：10条引用仍定位有效，8条有可识别矛盾，2条未覆盖。没有真实API调用，旧v3目录全部文件内容保持不变。

复查其他已完成目录：

```bash
.venv/bin/python -m scripts.diagnostic_constraint_checks \
  --input-dir results/mechanism_induction_20261008_v3 \
  --output-dir results/diagnostic_constraints_NEW_NAME
```

输出目录必须未使用且位于源目录之外。程序保存audit.json及report.md。旧归纳目录在实现哈希变更后不续发模型请求；后续真实归纳需新prepare，manifest会同时冻结归纳代码与检查器哈希。本阶段不需要重新调用模型。
