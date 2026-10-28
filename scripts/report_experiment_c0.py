"""C0 reporting; retains all scope errors and distinguishes source from root counts."""
import csv,json,statistics
from pathlib import Path
from collections import Counter
from scripts.experiment_c0 import ROOT,SEEDS,BUDGET,verify,history,features
from scripts.offline_loop_protocol import atomic_json,file_hash
verify()
rows=[json.loads(s) for s in (ROOT/'executions.jsonl').read_text().splitlines()]
assert len(rows)==2592
inventory=json.loads((ROOT/'candidate_inventory.json').read_text());eligible=[c for c in inventory if c['eligible'] and c['id'] not in ['c01','c02']];ids={c['id'] for c in eligible};lineages={c['id']:c['lineage'] for c in eligible};primary=[r for r in rows if r['candidate'] in ids]
assert len(primary)==2160
truth=json.loads((ROOT/'oracle_outputs.json').read_text());plans=json.loads((ROOT/'test_plans.json').read_text());diag=json.loads((ROOT/'diagnostic_outputs.json').read_text());protocol=json.loads((ROOT/'protocol.json').read_text())
methods=['R','G','F'];outcomes=[]
for c in eligible:
 for seed in SEEDS:
  for method in methods:
   rr=sorted([r for r in primary if r['candidate']==c['id'] and r['seed']==seed and r['method']==method],key=lambda r:r['index']);assert len(rr)==24
   first=next((r for r in rr if r['functional_failure']),None)
   outcomes.append(dict(candidate=c['id'],lineage=c['lineage'],seed=seed,method=method,first_counterexample_call=first['index'] if first else None,cumulative_execution_seconds_to_first=sum(x['wall_seconds'] for x in rr[:first['index']]) if first else None,first_problem_sha256=first['problem_sha256'] if first else None,right_censored_at=24 if not first else None,classification_counts=dict(Counter(r['classification'] or 'UNKNOWN' for r in rr)),total_execution_seconds=sum(x['wall_seconds'] for x in rr)))
atomic_json(ROOT/'paired_first_counterexamples.json',outcomes)
curves=[]
for b in [4,8,12,16,24]:
 for method in methods:
  rr=[r for r in primary if r['method']==method and r['index']<=b];bad=[r for r in rr if r['functional_failure']]
  curves.append(dict(budget=b,method=method,source_seed_detections=len({(r['candidate'],r['seed']) for r in bad}),source_seed_denominator=30,unique_source_versions=len({r['candidate'] for r in bad}),unique_lineages=len({lineages[r['candidate']] for r in bad}),mismatching_calls=len(bad),calls=len(rr)))
atomic_json(ROOT/'budget_curves.json',curves)
summaries=[]
for m in methods:
 rr=[r for r in primary if r['method']==m];bad=[r for r in rr if r['functional_failure']];po=[x for x in outcomes if x['method']==m]
 plan=[x for x in plans if x['method']==m];hs={x['problem_sha256'] for x in plan}
 summaries.append(dict(method=m,calls=len(rr),unique_source_versions=sorted({r['candidate'] for r in bad}),lineages=sorted({lineages[r['candidate']] for r in bad}),source_seed_detected=sum(x['first_counterexample_call'] is not None for x in po),source_seed_denominator=len(po),mismatching_calls=len(bad),execution_seconds=sum(r['wall_seconds'] for r in rr),classification_counts=dict(Counter(r['classification'] or 'UNKNOWN' for r in rr)),unique_reference_inputs=len(hs),isolated_reference_work_seconds=sum(truth[h]['dp_wall_seconds']+truth[h]['brute_wall_seconds'] for h in hs),reference_cost_note='Measured per-input reference cost sum if each strategy separately uses its unique set; actual shared precompute paid once, not this sum times operators.'))
atomic_json(ROOT/'strategy_summary.json',summaries)
sets={m:{r['candidate'] for r in primary if r['method']==m and r['functional_failure']} for m in methods}
paired=[]
for c in eligible:
 for seed in SEEDS:
  pp={m:next(x for x in outcomes if x['candidate']==c['id'] and x['seed']==seed and x['method']==m) for m in methods}
  paired.append(dict(candidate=c['id'],seed=seed,R=pp['R']['first_counterexample_call'],G=pp['G']['first_counterexample_call'],F=pp['F']['first_counterexample_call'],F_only=pp['F']['first_counterexample_call'] is not None and pp['G']['first_counterexample_call'] is None,G_only=pp['G']['first_counterexample_call'] is not None and pp['F']['first_counterexample_call'] is None))
atomic_json(ROOT/'paired_detection_results.json',paired)
atomic_json(ROOT/'detection_overlap.json',dict(source_sets={m:sorted(v) for m,v in sets.items()},F_only_vs_G=sorted(sets['F']-sets['G']),G_only_vs_F=sorted(sets['G']-sets['F']),G_F_overlap=sorted(sets['G']&sets['F']),G_only_source_seed=sum(x['G_only'] for x in paired),F_only_source_seed=sum(x['F_only'] for x in paired),unit='Source version and paired source-seed; not root-cause counts'))
# Record exact historical witness provenance, without reexecuting old failures.
tasks=json.loads(Path('results/experiment_b1_20261015_v1/inputs/natural_discovery.json').read_text())+json.loads(Path('results/experiment_b1_20261015_v1/inputs/diagnostics.json').read_text());byid={t['id']:t for t in tasks}
evidence=[]
for mid,tid,orig,fix,audit in [
 ('M1','ed6d58a5f459a1d86a68','results/experiment_b1_20261015_v1/causal_programs/M1_original.py','results/experiment_b1_20261015_v1/causal_programs/M1_repaired.py','results/experiment_b1_20261015_v1/counterfactual_diagnosis.json'),
 ('M3','e4d3bffc7350d308b0da','results/experiment_b1_20261015_v1/causal_programs/M3_original.py','results/experiment_b1_20261015_v1/causal_programs/M3_repaired.py','results/experiment_b1_20261015_v1/counterfactual_diagnosis.json'),
 ('M2','29fd9652186ed13194b9','results/experiment_b1_20261015_v1/posthoc_causal_audit/29fd9652186ed13194b9_baseline_1/original.py','results/experiment_b1_20261015_v1/posthoc_causal_audit/29fd9652186ed13194b9_baseline_1/type_repair.py','results/experiment_b1_20261015_v1/posthoc_causal_audit/29fd9652186ed13194b9_baseline_1/audit.json')]:
 evidence.append(dict(mechanism=mid,task=byid[tid],original=orig,minimal_historical_repair=fix,causal_audit=audit,hashes={p:file_hash(Path(p)) for p in [orig,fix,audit]},role='DEVELOPMENT_ONLY; rule provenance, not new defect discovery'))
atomic_json(ROOT/'historical_failure_evidence.json',evidence)
clusters=[dict(id='D1',sources=['c07','c08'],lineage=lineages['c07'],functional_evidence='Both repair versions truncate new_suffix to maximum pattern length minus one before forbidden comparison; maximum-length forbidden matches cannot be observed.',root_status='SOURCE_SUPPORTED_EARLY_TRUNCATION; same lineage versions merged',source_snippets=['new_suffix = new_suffix[-(max_pat_len-1):]','new_suffix = new_suffix[-suffix_len:]','if len(ft) <= len(new_suffix):']),dict(id='D2',sources=['c12'],lineage=lineages['c12'],functional_evidence='No patterns gives suffix_len=0; adjacency reads empty suf and endpoint last falls back to first even at n>1. Adjacent and endpoint witnesses share lost-last-state cause.',root_status='SOURCE_SUPPORTED_ZERO_HISTORY; separate KeyError runtime defect not counted as wrong-integer root',source_snippets=['suffix_len = max(L - 1, 0)','if adj and suf and suf[-1] == d:','last = suf[-1] if suf else first']),dict(id='D3',sources=['c13'],lineage=lineages['c13'],functional_evidence='Endpoint branch ends with pass; raw pattern candidate is truncated before match; no-pattern hist=0 also loses last for adjacency. Multiple mechanisms, conservatively one lineage cluster.',root_status='MULTIPLE_SOURCE_SUPPORTED_DEFECTS; independence within cluster not counted',source_snippets=['nh = (h + (d,))[-hist:] if hist > 0 else ()','if adj and h and h[-1] == d:','pass'])]
for c in clusters:
 c['minimal_witnesses']=[x for x in diag['reduced'] if x['candidate'] in c['sources']]
 c['line_evidence']={cid:[dict(line=i,text=line) for i,line in enumerate(Path(next(x for x in inventory if x['id']==cid)['source']).read_text().splitlines(),1) if any(sn in line for sn in c['source_snippets'])] for cid in c['sources']}
 c['novel_confirmatory_defect']=False
atomic_json(ROOT/'defect_inventory.json',dict(functionally_wrong_source_versions=4,conservative_lineage_clusters=3,independent_root_count='UNKNOWN; not equated to input count, source count, or lineage count',confirmed_new_independent_defects=0,clusters=clusters,excluded_runtime_defect=dict(source='c12',error='KeyError on str pattern char in integer-key sym2idx',functional_mismatch=False),minimality='Constraint deletion and n reduction only. Pattern-list/string minimality and globally shortest reproducer not established.'))
# New-sample leakage check exact historical A examples; all this pilot remains Development regardless.
historical_A=[x['problem'] for x in json.loads(Path('results/generated_operator_experiment_a_v1/posthoc_operator_checks.json').read_text())['checks']]
atomic_json(ROOT/'leakage_audit.json',dict(independent_confirmation_candidates=0,all_targets_development=True,all_tests_development=True,F_selection_inputs='public problem JSON + frozen scalar priority only; no candidate source, errors, output, oracle answer, or root label',G_pool_includes_F_transform_cases=True,exact_A_posthoc_inputs_reused=sum(x['problem'] in historical_A for x in plans),A_derivative_inputs='Not certified disjoint; all cases development, never new confirmation',cross_source='Different implementations exercised, all previously validated/reviewed; exploratory only',cross_defect='No independently confirmed transfer; root overlap and prior target exposure prevent claim',scope_correction='See scope_correction.json; retained erroneous-family executions.'))
cost=dict(new_API_requests=0,new_API_tokens=0,new_API_cny=0,primary_executions=len(primary),excluded_schema_executions=len(rows)-len(primary),primary_execution_seconds=sum(x['wall_seconds'] for x in primary),excluded_execution_seconds=sum(x['wall_seconds'] for x in rows if x['candidate'] not in ids),oracle_unique_inputs=len(truth),oracle_dp_seconds=sum(t['dp_wall_seconds'] for t in truth.values()),oracle_brute_seconds=sum(t['brute_wall_seconds'] for t in truth.values()),diagnostic_executions=diag['executions'],diagnostic_execution_seconds=sum(x['wall_seconds'] for x in diag['trials']),diagnostic_reference_seconds=sum(x['reference_seconds'] for x in diag['trials']),all_raw_records_retained=True,human_assistant_build_cost='UNPRICED_NOT_ZERO',no_monetized_local_compute=True)
atomic_json(ROOT/'cost_accounting.json',cost)
for name,data in [('paired_results',paired),('budget_curves',curves)]:
 with (ROOT/(name+'.csv')).open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
atomic_json(ROOT/'research_decision.json',dict(status='STOP_FAILURE_GUIDED_TEST_STRATEGY_FOR_CURRENT_PILOT',reason='No F-only wrong operator coverage vs strong G; one G-only source-seed; no independent candidates. All failure transforms also public-spec tests.',independent_information_gain='NOT_ESTABLISHED',regression_test_value='DEVELOPMENT_ONLY',cross_source_transfer='EXPLORATORY_PREVIOUSLY_SEEN_TARGETS',cross_defect_generalization='NOT_ESTABLISHED',Agent_workflow_effect='NOT_MEASURED',new_API=False,next_stage=False))
# Report includes links relative to its artifact folder for portability.
summary_table='\n'.join(f"| {s['method']} | {len(s['unique_source_versions'])}/10 | {len(s['lineages'])}/5 | {s['source_seed_detected']}/30 | {s['mismatching_calls']} | {s['execution_seconds']:.3f} |" for s in summaries)
curve_table='\n'.join(f"| {b} | "+' | '.join(str(next(x for x in curves if x['budget']==b and x['method']==m)['source_seed_detections'])+'/30' for m in methods)+' |' for b in [4,8,12,16,24])
first_table='\n'.join(f"| {c} | {seed} | "+' | '.join(str(next(x for x in outcomes if x['candidate']==c and x['seed']==seed and x['method']==m)['first_counterexample_call'] or '未发现') for m in methods)+' |' for c in ['c07','c08','c12','c13'] for seed in SEEDS)
source_table='\n'.join(f"| {c['id']} | {c['aliases'][0]} | {'适用' if c['id'] in ids else '范围错误：素数任务' if c['id'] in ['c01','c02'] else '空生成结果'} |" for c in inventory)
minimal_table='\n'.join(f"| {x['candidate']} | {x['reduced']['n']} | {json.dumps(x['reduced']['constraints'],ensure_ascii=False)} | {x['witness']['expected']} | {x['witness']['execution'].get('value')} |" for x in diag['reduced'])
report=f'''# Experiment C0：失败证据指导的算子验证增量实验

## 结论

本轮完成本地、低成本、开发性 Pilot。**F 没有发现 G 在同预算下遗漏的额外错误源码；停止当前失败指导测试策略，不进入 Agent Workflow 或新 API 阶段。** 这不是“失败信息在任何验证任务上都无用”的结论。

10 份适用的历史 LLM 生成计数源码中，G/F 均发现 4 份源码有功能错误，覆盖相同 3 个生成谱系；R 发现其中 2 份。按配对的源码×种子计，G 为 12/30、F 为 11/30，F 无独有发现，G 有 1 个独有配对发现。三种策略均没有独立确认对象，故**确认性新缺陷发现数为 0**；不得把历史错误的新触发输入称为新缺陷。

## 研究设计与冻结

核心问题：失败见证及最小因果修复是否改善未见算子的验证效率？保持 B1.8 停止决定，不构造 M1/M2/M3 Checker，不调整 Agent Prompt，不修复历史程序或生成新完整求解器。本轮只使用原源码、输入生成/优先选择及离线参考验证。

冻结文件为 `protocol.json`、`candidate_inventory.json`、`failure_rule_manifest.json`、`test_plans.json` 与 `shared_public_pools.json`。先生成所有输入和顺序，再计算答案、执行候选；输入选择函数只接收公共问题与种子，不接收目标源码、输出、标签或参考答案。

每个源码、每种策略、每个种子均执行 24 次；种子为 261015001/2/3，报告 4/8/12/16/24 次前缀。所有单元同一稳定化运行器，1 秒墙钟、3 秒 CPU、256 MiB；首次失败后仍完成额度。策略在执行时交错随机调度，首反例时间按该策略逻辑顺序累加实际执行时间，不是整个交错实验的日历时延。协议允许的是**等调用机会**，不声称构建加运行的总成本完全相等。

继续门槛：F 在独立候选上稳定发现同预算 G 遗漏的真实功能缺陷，且总成本可接受。没有独立候选不能通过门槛。没有增量则停止；仅已知缺陷近邻优势最多是回归价值。无追加 API；上限 0 元。不使用人工注入缺陷。

## 清点错误与分析范围修正

初始清点有 13 份去重源码，其中 12 份非空且通过静态准入。执行后核对原始生成规格发现 c01/c02 实际为早期**素数计数**候选，需要 digits/length/repeat/lower/upper；本轮 n/alphabet/constraints 不在其任务范围。初始范围判定有误，不能把它们的拒绝视为计数缺陷。

已追加 `scope_correction.json`，原协议和所有执行记录均未覆盖，不重跑。主分析排除这两份不适用源码的 432 次调用，但全部计入实际开销；c03 空结果作为生成失败保留，不计功能缺陷。该修正依据公开任务契约而非结果，但发生在执行后，因此进一步限制本轮为探索性分析。

适用源码为 c04–c13 共 10 份，属于 5 个谱系（A 的 3 个候选、B0 的 F/M 候选）。修复版本相关，不能将其当成独立算法样本。所有源码均曾参加验证或审查，**独立确认对象为 0**。B1–B1.8 的任务特定 Agent 程序不冒充新的通用算子。

| ID | 原始路径 | 范围 |
| --- | --- | --- |
{source_table}

## 阶段 A：证据到测试变换

`historical_failure_evidence.json` 保存原始输入、正确参考、源码/最小修复路径及 SHA256、历史反事实审计路径。M1/M3 使用 B1 的原始与最小修复程序，M2 使用 B1 29fd... 的 original/type_repair 与其历史审计。已有 B1.5/B1.6/B1.7/B1.8 为复现边界和无独立 Checker 增量提供背景，不将其结果改写。

| 冻结规则 | 历史因果证据 | 合法变换及条件 | 与公共规格的关系 |
| --- | --- | --- | --- |
| endpoint_dependency | M1 首元素状态局部修复 | 加入首尾约束，改变正长度及 count/position 组合；调整数量上限保持合法 | 首尾边界和交互本就是普通规格测试；无新契约 |
| pattern_history_boundary | M3 历史截断修复 | 模式长度 1–4，n 位于 L 前/等/后，混合长度及 exact=0 | 模式长度与流式边界可由规格直接得到；无新契约 |
| representation_and_overlap | M2 类型转换局部修复 | 对模板数字双射重命名，变化重叠及出现次数边界 | 字母表一致性和重叠均为公共条件；无新契约 |

变换作用于合法基础输入，数量参数裁剪到新长度合法范围。所有输出先由 validate_problem 验证。数字改名仅用于生成合法测试，**未假定带模约束时答案不变**；每个期望答案都重新计算。没有把出处来自失败自动认定为创新。

这些规则不是新的检查谓词。可检验的剩余假设是：失败证据给出的优先级能否在 24 次额度内提高发现率。本轮只检验一个预先冻结的简单优先级，不在结果后调权、换种子或追加规则。

## 阶段 B：公平验证对照

- R：在合法 schema 参数范围内随机采样，n=1–7，0–7 种约束，模式长度 1–4；不是所有数学输入的严格均匀分布。
- G：完整公共规格的结构化覆盖。共享候选池包括随机合法样例及全部历史变换样例；按未覆盖的约束种类、两两交互、约束数量、长度、边界、重叠、奇偶位置、模数、禁止模式数量/混合长度等贪心选择。G 未被禁用任何 F 的廉价输入。
- F：前 12 次与 G 完全一致；后 12 次从同一池按冻结的端点/模式相关优先级排序，再以公共覆盖打破平局。目标源码与结果不参与选择。

G 并非自动代码覆盖引导器；本项目未发现可直接复用的成熟算子输入反馈覆盖实现。此 Pilot 的强对照是结构覆盖及交互测试，不能宣称已比较所有模糊测试或性质测试技术。

172 个唯一输入全部以独立暴力枚举对照已有 exact_dp，答案全部一致。生成、排序过程未读取参考结果。候选只能在原 native sandbox 中运行；不开放历史文件、参考求解器、网络或额外工具。返回整数成功但值不一致才记为功能计数错误；KeyError、资源/接口/隔离错误、拒绝分别保留。

## 主要检测结果

以下源码和谱系覆盖是三个种子的并集，不代表无偏错误率或独立算法复现。

| 方法 | 发现错误源码 | 发现错误谱系 | 检出源码×种子 | 错误整数返回次数 | 主验证执行秒 |
| --- | --- | --- | --- | --- | --- |
{summary_table}

每组主验证 720 次，30 个源码×种子配对；重复触发次数仅用于审计，不当作独立缺陷数。G/F 共同发现 c07/c08/c12/c13，R 发现 c12/c13。F 独有错误源码 0，G 独有错误源码 0；G 独有源码×种子 1（c12/261015001，第 17 次），F 独有配对 0。F 增加的相关测试未带来新增覆盖，反而替换掉该配对中的有效通用测试。

| 每源码每种子调用额度 | R 检出配对 | G 检出配对 | F 检出配对 |
| --- | --- | --- | --- |
{curve_table}

这不是检出率的总体估计：候选是历史选择后的相关版本，且只用三个种子。未做显著性或部署收益推断。其余源码在这些输入上未见错误，不能称为完整正确或通过安全准入。

## 首个有效反例及时间

| 源码 | 种子 | R 首次调用 | G 首次调用 | F 首次调用 |
| --- | --- | --- | --- | --- |
{first_table}

所有 30×3 配对的首反例、累计执行秒与未发现时的右删失标记见 `paired_first_counterexamples.json`，逐项胜负见 `paired_results.csv`。没有把“24 次未发现”记成第 25 次，也未仅对成功检出的样本比较平均时间以制造优势。前 12 次 G/F 设计相同，首反例相同不构成失败证据收益。

## 最小复现与根因去重

主比较结束后追加一次诊断，预先冻结 180 次上限，实际 98 次隔离执行；不修改任何候选源码，不把诊断结果追加到主预算中。反例通过删除约束与降低 n 达到固定点。这里的“最小”仅指这些操作下的 1-minimal；未证明模式列表、字符或全局输入长度的绝对最小性。

| 源码 | n | 缩减后约束 | 参考值 | 原源码返回 |
| --- | --- | --- | --- | --- |
{minimal_table}

程序级证据及行号保存在 `defect_inventory.json`：

1. D1：c07/c08 同一 A 候选谱系的两个修复版本，禁止模式比较前把候选裁至最大长度减一，最大长度模式无法匹配。合并为一个谱系缺陷簇，不因两个版本或多个反例翻倍。
2. D2：c12 无模式时 suffix_len=0，相邻检查失去末符号；端点分支亦把末符号回退为 first。n=2 的 adjacent 应为 12 却返回 16，endpoints 应为 12 却返回 0。这些按共同状态信息问题保守合并。字符串 pattern 在整数键映射上发生 KeyError 是另列运行异常，不当作错误整数计数或 M2 检测成功。
3. D3：c13 同时存在端点分支 pass、匹配前截断以及零历史相邻约束问题。多个机制可能交互，保守合并为一个源码谱系簇；不强行拆成多个独立缺陷。

因此已验证有错源码 4 份、保守有错谱系簇 3 个；**独立根因总数 UNKNOWN**，不能把 84 等重复错误输出数或这些谱系数直接当作独立根因数。每个簇都由 G 与 F 检出，无 F 独有已确认根因。该结论不声称穷尽所有潜在缺陷。

## 跨源码、跨缺陷与泄漏边界

规则见证来自历史 Agent 失败，目标是其他生成算子实现；可观察到不同源码上相关错误被触发。但目标均已被验证或审查，只能叫开发性的跨实现回归，不是未见源码确认。

没有独立确认的跨缺陷迁移。即使某个触发包含相邻约束或不同模式长度，也不能仅据输入标签认定发现了全新根因。未使用人工故障注入。Experiment A 已公开反例及衍生条件只具有开发意义；本轮全部输入均标 Development，不以“新随机种子”洗成确认样本。确切重用及候选范围错误见 `leakage_audit.json`。

F 不含目标源码分析，G 可使用所有 F 池内测试；不过本轮设计者已知历史研究，不能视为盲规则合成实验，无法据此估计历史信息降低多少构建 Token。

## 成本与工程复核

新模型 API 调用 **0**，API Token **0**，API 费用 **0 元**。实际资源包含以下全部开销，不删除错误范围单元：

| 项目 | 数量/时间 |
| --- | --- |
| 主验证 | {cost['primary_executions']} 次，{cost['primary_execution_seconds']:.3f} 秒 |
| 错误任务范围调用 | {cost['excluded_schema_executions']} 次，{cost['excluded_execution_seconds']:.3f} 秒 |
| 唯一参考输入 | {len(truth)} 个；DP {cost['oracle_dp_seconds']:.6f} 秒，暴力 {cost['oracle_brute_seconds']:.6f} 秒 |
| 后验反例缩减 | {diag['executions']} 次执行，{cost['diagnostic_execution_seconds']:.3f} 秒；参考 {cost['diagnostic_reference_seconds']:.6f} 秒 |

测试计划构建计时见 `construction_accounting.json`；每策略实际执行与若单独缓存其参考输入的工作量见 `strategy_summary.json`。参考答案实际一次共享计算，不能将其重复收费为每算子各算一次。人工/助手设计、历史诊断及工程成本未单独货币化，明确为 UNPRICED_NOT_ZERO，不声称低 API 花费即全部构建成本为零。未调用新模型合成规则，也就没有 G/F 盲合成成本对照。

沿用稳定化运行环境和隔离机制，未修改旧驱动、参考求解器、历史算子或冻结结果。完整历史哈希复核见 `final_integrity_audit.json`。此次真实计数验证中执行异常按原始类别保留，所有预定单元完整执行，没有因网络或错误追加重跑。

## 研究判断与停止

已验证：公共结构化测试可在小调用额度下发现历史真实算子的功能缺陷；部分变换可用于回归；原始程序证据和反例可复核。

未建立：失败信息相对 G 的独立检测增量、未见源码或跨缺陷泛化、构建成本节省。F 在本轮未超过 G，门槛不通过，停止该冻结测试策略，不改权重、追加规则或扩大历史回放追求正结果。

未测量：算子准入/隔离如何影响错误复用次数、Agent 最终正确率和总成本。离线发现错误不等于 Agent 能力提升，本轮不启动端到端 Workflow。

**核心回答：当前失败驱动规则主要把公共规格的端点、长度、重叠和表示条件组织成定向测试；在这次开发 Pilot 中没有提高强通用测试之上的验证效率。未见算子上的效率收益仍未建立，不能据几个已知错误宣称创新。**

## 交付索引

冻结源码 `sources/`；原始来源 `candidate_inventory.json`；历史因果证据 `historical_failure_evidence.json`；规则 `failure_rule_manifest.json`；协议 `protocol.json`；范围修正 `scope_correction.json`；测试输入 `test_plans.json` / `shared_public_pools.json`；双参考输出 `oracle_outputs.json`；所有执行 `executions.jsonl`；配对与预算 `paired_results.csv` / `budget_curves.csv`；反例缩减 `diagnostic_protocol.json` / `diagnostic_outputs.json`；根因 `defect_inventory.json`；泄漏与成本 `leakage_audit.json` / `cost_accounting.json`；停止决定 `research_decision.json`；源文件哈希 `artifact_manifest.json`。
'''
known=json.loads(Path('results/generated_operator_experiment_a_v1/posthoc_operator_checks.json').read_text())
misses=[x for x in known['checks'] if not x['passed']]
assert next(c for c in inventory if c['id']=='c11')['sha256']==known['source_sha256']
atomic_json(ROOT/'known_defect_miss_audit.json',dict(source='c11',historical_counterexamples=misses,detected_by_pilot={m:any(x['candidate']=='c11' and x['method']==m and x['functional_failure'] for x in primary) for m in methods},new_execution=False,new_defect=False,interpretation='All methods missed an already-known counterexample family within frozen pilot budget; no correctness/admission claim for c11.'))
report=report.replace('## 首个有效反例及时间','本轮存在明确的测试能力边界：c11（A 最终冻结算子）的历史公开反例已有 1 对 0 的错误结果，但 R/G/F 在本轮冻结输入中均未发现 c11 的错误。该历史事实未计为新发现，也未追加到主测试中；见 `known_defect_miss_audit.json`。因此 G 是具有完整规格访问权的结构化对照，不能称为完备验证器，本轮也不能估计真实缺陷召回率。\n\n## 首个有效反例及时间')
(ROOT/'experiment_c0_report.md').write_text(report)
before=json.loads((ROOT/'history_before.json').read_text());after=history();assert before==after
atomic_json(ROOT/'final_integrity_audit.json',dict(protected_files=len(before),unchanged=before==after,changed=[],frozen_protocol_valid=True,primary_completed=2160,all_completed=2592,diagnostic_completed=98,scope_correction_preserved=True))
atomic_json(ROOT/'artifact_manifest.json',{str(p):file_hash(p) for p in ROOT.rglob('*') if p.is_file() and p.name!='artifact_manifest.json'})
print(json.dumps(dict(history_files=len(before),API=0,wrong_sources=4,G=12,F=11,report=str(ROOT/'experiment_c0_report.md')),ensure_ascii=False))
