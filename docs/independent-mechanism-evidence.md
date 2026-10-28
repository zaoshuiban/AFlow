# 独立补证、语义审核与候选生成

入口：`.venv/bin/python -m scripts.independent_mechanism_evidence`。

本入口实现：事前冻结 → 完整基线采集 → 编号诊断 → 证据绑定的语义审核 → 原有固定骨架候选生成。真实调用需要每个运行命令显式传入 `--allow-api`。开发测试使用模拟传输，没有产生真实补证结论或付费请求。

2026-10-09 增加结构化选题与独立边界对照，见文末。旧字面协议仍可用于新准备；旧运行因源码变化不能直接续跑，原始文件和 frozen_code 副本保留，不能改写旧 manifest 来绕过版本检查。

生成结果保持 `candidate` 状态，位于独立目录；不会修改原有算子库或晋级。之后可通过 `scripts.reviewed_operator_continuation prepare/run` 另行冻结并衔接开发比较、单候选确认和私有发布，见 [审核候选交接](reviewed-operator-continuation.md)。生成成功仍不等于闭环成功晋级。

## 1. 准备两份配置

机制协议 JSON 有且仅有 `scope`、`hypothesis`、`program_verifier` 三项。以下只是格式示例，不是已证实机制，也不是为现有四题事后挑出的成功协议。正式运行前根据独立题量和研究目标确定范围，不能看过新回答再修改。

```json
{
  "scope": {
    "mechanism_id": "unjustified_exact_value_replacement",
    "required_terms": ["volume", "ratio"],
    "forbidden_terms": ["approximately", "nearest"]
  },
  "hypothesis": {
    "claim": "题设精确数值被无依据地替换为近似值",
    "violated_constraint": "未授权的近似不能替代题设精确量",
    "expected_intervention": "保留精确量并逐步检查数值替换依据",
    "applicable_when": "题设量为精确值且要求精确体积比",
    "not_applicable_when": "题设要求近似、取整或给出测量误差",
    "success_criteria": "最终答案与题设精确计算一致且不引入额外近似假设"
  },
  "program_verifier": "none"
}
```

`scope` 使用大小写敏感的字面匹配：所有 required_terms 均出现，且没有 forbidden_terms。它只负责可执行的候选范围，不自动理解“精确”或“测量值”；这些语义边界必须在整体审核中核对。不能把参考答案、基线输出或诊断结果放进选题条件。

模型与运行配置 JSON 使用 `LoopConfig` 字段；未填写字段使用原默认值。示例中的模型名应替换为本地实际名称，勿写密钥：

```json
{
  "opt_model": "YOUR_OPT_MODEL",
  "exec_model": "YOUR_EXEC_MODEL",
  "seed": 170101,
  "discovery_runs": 3,
  "candidate_count": 2,
  "min_stable_problems": 3,
  "min_wrong_repeats": 2,
  "min_error_rate": 0.2,
  "max_requests": 1000,
  "max_output_tokens": 8192,
  "timeout": 180,
  "thinking_mode": "provider-default"
}
```

重复数、阈值、模型参数、思考模式与预算全部在 prepare 固定。请求上限不是金额上限；Token 和未知用量保存到账本，实际费用看供应商账单。temperature=0 下重复回答只表示冻结设置下的重复性，不代表多种独立配置。

## 2. 免费冻结

将上述两个文件保存为自己的协议和配置文件后运行，例如：

```bash
.venv/bin/python -m scripts.independent_mechanism_evidence prepare \
  --output-dir workspace/MATH/evidence_runs/mechanism_001 \
  --data data/datasets/math_train_independent.jsonl \
  --source-name '独立 MATH 原始 train；填写本轮实际来源与排除记录' \
  --protocol /absolute/path/protocol.json \
  --config /absolute/path/config.json \
  --target-count 24 --control-count 12
```

24/12 是说明用规模，不代表统计充分性。若范围内独立题不足，准备直接失败，不补入已见题或降低阈值。程序只按冻结题面范围和种子抽取样本；对照是范围外随机题，并不自动保证都是邻近反例，需检查代表性。

自动排除本地 test/validate、标准 `offline_runs`/`evidence_runs` 目录及同级运行目录的已预留样本。额外历史数据通过多次 `--exclude-data /path/history.jsonl` 指定。题面及已提供 source_id/family_id 排除用于隔离；同一来源/题族的不同剩余变体会拒绝导入。没有题族元数据时不能声称完成语义去重。放在其他自定义目录的历史实验要显式提供排除数据。

冻结文件包括样本列表、题目与参考、机制范围和假设、判断规则、模型设置（无密钥）、运行源码副本、源码哈希、评分及 API 依赖版本、基线模板和准备事件。已有目录禁止覆盖。

## 3. 采集完整基线

```bash
.venv/bin/python -m scripts.independent_mechanism_evidence run \
  --output-dir workspace/MATH/evidence_runs/mechanism_001 --allow-api
```

使用原两阶段基线。每题所有重复都记录，包括正确回答、错误回答和执行异常；请求原始提示、原始回答、报告模型版本、Token 与错误原因保存在 requests。观察按题号、重复次数和 manifest 哈希绑定。

每个阶段完成时冻结相关请求文件的哈希，后续审核、生成或恢复会核对原始请求证据，不能仅修改原始响应而继续沿用旧审核。

中断后使用原命令恢复，已完成观察复用；已发出但结果未知的请求保留为失败，不重发。预算耗尽时不开放诊断和生成，不能在原协议中加预算。代码、依赖或冻结输入改变则拒绝继续。现有公开题的预训练可见性不由此证明。

```bash
.venv/bin/python -m scripts.independent_mechanism_evidence status \
  --output-dir workspace/MATH/evidence_runs/mechanism_001
```

## 4. 诊断与审核

完整采集后才能诊断：

```bash
.venv/bin/python -m scripts.independent_mechanism_evidence diagnose \
  --output-dir workspace/MATH/evidence_runs/mechanism_001 --allow-api
```

使用原编号归纳协议，只对稳定失败做诊断，但统计分母来自完整基线。一次诊断请求，不自动重试；截断/格式无效时保留请求与原响应。也可以导入诊断 JSON，导入会明确标记来源为用户提供，不能冒充未经修改的自动结果：

```bash
.venv/bin/python -m scripts.independent_mechanism_evidence import-diagnosis \
  --output-dir workspace/MATH/evidence_runs/mechanism_001 \
  --response /absolute/path/diagnosis.json
```

一旦诊断成功冻结就禁止覆盖。需要修正机制或诊断协议时建立新实验，不把新的解释覆盖旧原文。

生成待填写的审核文件：

```bash
.venv/bin/python -m scripts.independent_mechanism_evidence review-template \
  --output-dir workspace/MATH/evidence_runs/mechanism_001 \
  --file /absolute/path/review.json
```

每条审核含 `decision`（pending/confirm/reject）、`reviewer`、带时区的 `reviewed_at`、`rationale`、`reference_check`、`boundary_check`。整体 `scope_review` 还需确认假设、干预目标和题面适用边界。字段应由实际审阅者据实填写，不使用虚构身份或自动填“已确认”。

审核文件绑定 manifest、完整观察、原诊断、证据包及逐条分析哈希。更换任何证据会使旧审核失效。人工确认是记录在案的声明，程序没有认证身份或自动证明数学正确。

先预览，尚不冻结决定：

```bash
.venv/bin/python -m scripts.independent_mechanism_evidence review \
  --output-dir workspace/MATH/evidence_runs/mechanism_001 \
  --file /absolute/path/review.json --check-only
```

去掉 `--check-only` 才保存本轮最终审核，**包括不足或反对结论；已保存审核禁止覆盖**。因此 pending 文件应先用预览检查。

|状态|含义|是否能支持生成|
|---|---|---|
|reference_valid|原文编号有效，尚无语义确认|否|
|contradicted|约束检查或独立计算发现反证|阻止当前机制生成，人工确认不能覆盖|
|human_confirmed|具名人工审核确认该条诊断|计入人工支持，保留声明性质|
|program_verified|白名单验证器重新计算并保存了见证|仅支持其明确验证的窄命题|
|human_rejected|审阅者反对|阻止当前机制生成|

`semantic_verified=true` 等导入标记没有授权作用。默认检查器的 `not_covered` 和 `limited_checks_consistent` 也不会自动升级为通过。

第一版程序验证器 `explicit_exact_arithmetic_v1` 只支持完整题面 `Compute exactly: 有理算术表达式.`（`+ - * /`、括号、整数和小数），独立计算 Fraction，核对一个明确 boxed 有理参考和一个 boxed 预测。它验证的机制 ID 必须是 `exact_arithmetic_mismatch`，claim 必须为：

> The final rational answer differs from the exact value of the explicitly given arithmetic expression.

它不证明“擅自近似”、中间推理或几何机制。不匹配的题面/答案保持未覆盖；错误参考或正确预测反驳该数值错误诊断。程序通过时生成器只收到计算见证，不将诊断自由文本当作已验证解释。即使所有局部见证通过，整体机制范围和干预设计仍需人工审核。

## 5. 审核后生成候选

生成准入要求同时满足：

- 范围内至少 `min_stable_problems` 道独立稳定失败；错误率以所有有效目标观察为分母，执行异常排除。
- 至少同样多的不同题目，每题有 `min_wrong_repeats` 次属于**同一机制**且经人工或程序确认的错误。
- 整体机制与作用域人工确认，无当前机制的明确反证或人工反对。

```bash
.venv/bin/python -m scripts.independent_mechanism_evidence generate \
  --output-dir workspace/MATH/evidence_runs/mechanism_001 --allow-api
```

生成器直接消费审核后的假设、范围和支持证据，不重新诊断后替换结论。对照原题、未审核解释、开发/隐藏题不进入候选上下文。仍只生成 single_step/two_stage/verify_fix 提示策略，编译器给候选添加冻结字面范围的调用前拒绝条件；原有特征规则可能进一步缩小范围。候选保留审核、manifest、scope 的来源哈希，仅进入独立工作库。

## 主要产物

|文件|用途|
|---|---|
|manifest.json、inputs/、model_settings.json|事前冻结的协议、样本、模型和来源|
|frozen_code/、base_template/|本轮运行源码与基线副本|
|events/|准备、采集开始、采集完成、审核与生成的顺序记录|
|requests/|真实请求原文、响应、状态与用量；无 API 密钥|
|checkpoints/evidence/baseline/|逐题逐次基线和输入/输出哈希|
|observations.json、baseline_summary.json|完整观察导出与汇总，消费时与检查点核对|
|packet.json、checkpoints/diagnostic.json|编号原文和冻结诊断/来源|
|checkpoints/semantic_review.json、review_report.json|审核声明、四类证据状态及准入结果|
|checkpoints/reviewed_generation.json、work/operator_library/|生成规格、候选代码和来源|

事件时间来自本机时钟；本地哈希链不等于外部公证。测试只验证工作流和拒绝边界，不是准确率实验。

免费验证：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_independent_mechanism_evidence.py' -v
```

## 结构化选题与边界对照（2026-10-09）

结构化协议将 scope 改为以下形式；hypothesis/program_verifier 保持原定义，并新增 control_scope 和 sampling_policy：

```json
{
  "scope": {
    "mechanism_id": "unjustified_exact_value_replacement",
    "selector": {
      "kind": "decimal_reasoning_v1",
      "min_distinct_decimals": 2,
      "families": ["gas_proportion", "solution_mixture", "pole_similarity", "ladder_geometry", "equal_perimeters", "projectile_model"]
    }
  },
  "control_scope": {
    "mechanism_id": "unjustified_exact_value_replacement",
    "selector": {
      "kind": "decimal_boundary_v1",
      "min_distinct_decimals": 2,
      "boundary_types": ["given_approximation", "final_rounding"]
    }
  },
  "sampling_policy": {
    "one_per_template_family": true,
    "control_counts": {"given_approximation": 1, "final_rounding": 1}
  }
}
```

以上是需要合入完整协议的字段示例，不能单独替代包含 hypothesis/program_verifier 的完整文件。

- 题设小数只取正文，使用有理数归一化计算不同值，`2.28` 和 `2.280` 算一个值。记录每个数字的原题字符位置。
- Asymptote全部忽略，包括静态标签；LaTeX间距、答案选项、For example/Note/You should enter 等后续说明不作为数值来源。未支持的形式不纳入，避免用布局小数凑数。
- 目标题必须匹配列出的有限模板，并且正文没有近似、最近值或取整指令。这是输入识别规则，不是机制语义证明。
- 对照分别匹配“给定近似量的单位换算”和“多步量值/费用类题要求最终取整”；前者优先归类，两类各取1道。目标题与对照不能重叠，数量不足拒绝准备。
- 固定种子打乱后每个目标模板最多选一道，例如两道抛体方程只保留一道。模板同类限额不能替代人工题族核验。
- `inputs/selection.json` 保存数量和逐题理由并参与冻结；候选运行前调用同一个 `mechanism_scope.matches` 检查器。不能通过单独修改生成提示词扩大适用范围。

结构化选择器测试：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_mechanism_scope.py' -v
```

## 2026-10-09：历史回放

prepare 新增 `--evidence-mode historical_replay`，允许已知历史题，明确标记非独立自然证据；不导入旧回答，不降低语义审核门槛，超时不算数学错答。新准备的模板移除两项已安装专项的定义和描述，初始库为空。详见 [审核候选交接](reviewed-operator-continuation.md)。
