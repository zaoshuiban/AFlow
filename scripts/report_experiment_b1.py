"""Descriptive B1 statistics, separate natural/targeted/repeats/confirmation."""
import csv,json,copy
from pathlib import Path
from collections import Counter,defaultdict
from scripts.experiment_b1 import ROOT,verify,counts,SEMANTICS
from scripts.offline_loop_protocol import atomic_json,read_json,file_hash,digest
from scripts.compositional_counting import exact_dp


def history_review():
 events=read_json(ROOT/'normalized_historical_events.json');mechanisms=read_json(ROOT/'mechanism_evidence.json');reviewed=[]
 for e in mechanisms:
  # A correct alternate solve2 was written but not called by the tool entrypoint.
  if e['task_id']=='3e95a2523f857f347e7e':continue
  # Only the replayed final source is promoted to verified M3 event-level attribution.
  if e['mechanism']=='M3_premature_history_truncation' and e['step_id']!=2:continue
  reviewed.append(e)
 atomic_json(ROOT/'reviewed_mechanism_evidence.json',reviewed)
 mapping={(e['task_id'],e['step_id']):e['mechanism'] for e in reviewed};families={}
 for e in events:
  if not e['local_failure']:continue
  mid=mapping.get((e['task_id'],e['step_id']));family='State Representation' if mid in ('M1_missing_first_symbol','M2_pattern_type_mismatch') else 'State Transition' if mid=='M3_premature_history_truncation' else e['failure_family']
  if e['task_id']=='3e95a2523f857f347e7e' and e['step_id']==0:family='Tool Integration'
  families.setdefault(family,[]).append(e)
 for family in ['State Representation','State Transition','Constraint Omission','Counting Error','Runtime Error','Tool Integration','Other / Unknown']:families.setdefault(family,[])
 table={family:dict(tasks=len({e['task_id'] for e in es}),verified_events=len(es),recovered_tasks=len({e['task_id'] for e in es if e['final_task_success']})) for family,es in families.items()}
 functional=[e for e in events if e['error_type']=='CODE_WRONG_RESULT' and e['local_failure']]
 finalonly={e['mechanism'] for e in reviewed if not e['recovered']};allmech={e['mechanism'] for e in reviewed}
 return dict(table2=table,functional=dict(wrong_integer_tasks=len({e['task_id'] for e in functional}),wrong_integer_steps=len(functional),recovered_wrong_integer_tasks=len({e['task_id'] for e in functional if e['final_task_success']})),final_only_candidates=sorted(finalonly),all_candidates=sorted(allmech),added_candidates=sorted(allmech-finalonly),discovery_yield=dict(numerator=len(allmech),denominator=60),reviewed=reviewed,notes=['Candidate yield is within3 assisted reviewed mechanisms, not exhaustive sensitivity/precision estimate','M3 is already B0-known, not a novel B1 discovery','3e95 helper entrypoint ambiguity excluded from M1 attribution','M3 earlier compound/unreplayed versions not counted as verified same mechanism events'])


def strict_target_evidence(row,events):
 """Conservative candidate screen; causal verification required before same-mechanism count."""
 p=json.loads(row['problem']);has_endpoint=any(c['kind']=='endpoints' for c in p['constraints'])
 if not has_endpoint:return []
 q=copy.deepcopy(p);q['constraints']=[c for c in q['constraints'] if c['kind']!='endpoints'];abl=exact_dp(q)['answer'];out=[]
 for e in events:
  if e['error_type']!='CODE_WRONG_RESULT' or not e['local_failure'] or e['output']['value']!=abl:continue
  code=e['code'] or ''
  # Explicit absence of any endpoint handling is source evidence; comments alone never enough.
  literal_omission='endpoints' not in code
  explicit_stub='pass' in code and ('first' in code and ('track' in code or 'need' in code))
  if literal_omission or explicit_stub:out.append(dict(task_id=row['id'],step_id=e['step_id'],source=code,ablation_answer=abl,screen='candidate source+ablation, requires independent replay/minimal intervention; not yet verified same mechanism'))
 return out


def metrics(rows,events):
 success=sum(r['observation']['correct'] for r in rows);local={e['task_id'] for e in events if e['local_failure']}
 def avg(name):
  vs=[r['observation'].get(name) for r in rows if r['observation'].get(name) is not None];return sum(vs)/len(vs) if vs else None
 return dict(n=len(rows),correct=success,final_failures=len(rows)-success,local_failure_tasks=len(local),recovered_local_tasks=len({e['task_id'] for e in events if e['local_failure'] and e['final_task_success']}),wrong_integer_tasks=len({e['task_id'] for e in events if e['error_type']=='CODE_WRONG_RESULT' and e['local_failure']}),integration_error_tasks=len({e['task_id'] for e in events if e['failure_family']=='Tool Integration' and e['local_failure']}),mean_input_tokens=avg('input_tokens'),mean_output_tokens=avg('output_tokens'),mean_calls=avg('calls'),mean_tools=avg('tools'),mean_tool_seconds=avg('tool_seconds'),mean_latency_seconds=avg('seconds'),infrastructure_interruptions=sum(r['observation']['error']=='infrastructure_interrupted' for r in rows),budget_violations=sum(r['observation']['budget_violation'] for r in rows),code_revisions=sum(max(0,r['observation']['tools']-1) for r in rows))


def report():
 verify();assert read_json(ROOT/'state.json')['status']=='complete'
 h=read_json(ROOT/'historical_statistics.json');review=history_review();program=read_json(ROOT/'program_diagnosis.json');results=[];newcandidates=[]
 for file in (ROOT/'detected_new').glob('*.json'):
  r=read_json(file);r['observation']=read_json(Path(r['observation_path']));results.append(r)
  for c in strict_target_evidence({**r['base_task'],'id':r['observation']['id']},r['events']):c.update(arm=r['arm'],study_stage=r['stage'],repeat=r['repeat'],base_task_id=r['base_task']['id']);newcandidates.append(c)
 assert len(results)==72
 atomic_json(ROOT/'new_mechanism_screen_candidates.json',newcandidates)
 comparison={}
 for stage in ['diagnostic','repeat','confirmation','all_targeted']:
  comparison[stage]={}
  for arm in ('baseline','reflection'):
   z=[r for r in results if r['arm']==arm and (stage=='all_targeted' or r['stage']==stage)];es=[e for r in z for e in r['events']];comparison[stage][arm]=metrics(z,es)
 atomic_json(ROOT/'reflection_comparison.json',comparison)
 # Paired task outcomes; repeated same instances kept separate from task-level denominators.
 pairs={}
 for stage in ['diagnostic','repeat','confirmation']:
  a={r['observation']['id']:r['observation'] for r in results if r['arm']=='baseline' and r['stage']==stage};b={r['observation']['id']:r['observation'] for r in results if r['arm']=='reflection' and r['stage']==stage};assert set(a)==set(b)
  valid=[i for i in a if a[i]['error']!='infrastructure_interrupted' and b[i]['error']!='infrastructure_interrupted']
  pairs[stage]=dict(n=len(a),reflection_wins=sum(b[i]['correct'] and not a[i]['correct'] for i in a),baseline_wins=sum(a[i]['correct'] and not b[i]['correct'] for i in a),valid_infrastructure_pairs=len(valid),valid_reflection_wins=sum(b[i]['correct'] and not a[i]['correct'] for i in valid),valid_baseline_wins=sum(a[i]['correct'] and not b[i]['correct'] for i in valid))
 atomic_json(ROOT/'paired_agent_results.json',pairs)
 program_table={v:dict(n=len(z:=[r for r in program['M1'] if r['role']==v]),original_errors=sum(not r['original']['correct'] for r in z),repaired_errors=sum(not r['repaired']['correct'] for r in z)) for v in ['trigger','control','discovery']}
 costs={arm:read_json(ROOT/('agent_'+arm)/'usage.json') for arm in ('baseline','reflection')};total=dict(requests=sum(v['api_request_attempts'] for v in costs.values()),input_tokens=sum(v['input_tokens'] for v in costs.values()),output_tokens=sum(v['output_tokens'] for v in costs.values()),known_token_estimate_cny=sum(v['known_token_estimate_cny'] for v in costs.values()),reserved_or_reported_cost_cny=sum(v['reserved_or_reported_cost_cny'] for v in costs.values()),unknown_usage_requests=sum(v['unknown_usage_requests'] for v in costs.values()),actual_invoice_cost_cny=None)
 # Same-mechanism claims only after manual minimal-intervention audit. Screen is NOT counted as verified.
 audit=read_json(ROOT/'new_source_mechanism_audit.json') if (ROOT/'new_source_mechanism_audit.json').exists() else dict(verified=[],unknown_candidates=newcandidates)
 verified=audit.get('verified',[]);distinct_new={x['base_task_id'] for x in verified};confirmed={x['base_task_id'] for x in verified if x['study_stage']=='confirmation'}
 grade='B' if len(distinct_new)>=2 else 'A';opportunity='Uncertain' if not confirmed else 'Medium Opportunity' if grade=='B' else 'Low Opportunity'
 # C needs unmet tool coverage and expensive/resistant reflection evidence, not inferred from omissions alone.
 secondary=audit.get('secondary_posthoc',[])
 secondary_distinct={x['base_task_id'] for x in secondary}
 gap=[dict(mechanism_id='M1_missing_first_symbol',grade=grade,opportunity=opportunity,source_supported_historical_tasks=len({e['task_id'] for e in review['reviewed'] if e['mechanism']=='M1_missing_first_symbol'}),verified_new_task_instances=len(distinct_new),verified_confirmation_instances=len(confirmed),new_executions_with_verified_mechanism=len(verified),screen_candidates_not_proof=len(newcandidates),reflection_resistance='The strict M1 case was final-wrong; paired Reflection final-correct with same3 tools, so no established excess M1 repair cost beyond interface noise',programmability='explicit first-state/endpoint contract; no operator generated',existing_tool_coverage='Generic Python plus normal Reflection and exact DP programming already available; unmet reusable tool coverage not established',negative_transfer_risk='B0 observed unsafe solver effects; no new operator safety claim',grade_scope='Strict frozen M1 only one new case; related memo-context/count compound excluded from same-mechanism replication, stability unresolved'),dict(mechanism_id='M2_pattern_type_mismatch',grade='B' if len(secondary_distinct)>=2 else 'A',opportunity='Uncertain',verified_new_task_instances=len(secondary_distinct),reason='two diagnostic inputs with independently repaired integer/character comparison defects; secondary posthoc recurrence, no M2 paired Agent design or independent M2 confirmation'),dict(mechanism_id='M3_premature_history_truncation',grade='A',opportunity='Uncertain',reason='B0-known historical final failure and causal audit, not re-tested as new Agent gap in B1')]
 atomic_json(ROOT/'capability_gap_candidates.json',gap)
 stratified={}
 for variant in ('trigger','control'):
  stratified[variant]={}
  for arm in ('baseline','reflection'):
   z=[r for r in results if r['stage']=='diagnostic' and r['arm']==arm and r['base_task']['variant']==variant];es=[e for r in z for e in r['events']];v=metrics(z,es)
   v['verified_M1_executions']=sum(x['arm']==arm and x['study_stage']=='diagnostic' and x['base_task_id'] in {r['base_task']['id'] for r in z} for x in verified);stratified[variant][arm]=v
 data=dict(natural_history=h,history_review=review,program_diagnosis=program_table,reflection_comparison=comparison,diagnostic_conditions=stratified,paired=pairs,new_verified_same_mechanism=verified,secondary_posthoc_mechanism_evidence=secondary,new_screened_candidates=len(newcandidates),gaps=gap,costs=costs,total_usage=total,operators_generated=0,exploratory=True,confirmation_used_for_revision=False)
 atomic_json(ROOT/'analysis.json',data);atomic_json(ROOT/'counterfactual_diagnosis.json',dict(programs=program,agent_comparison=comparison,paired=pairs,mechanism_audit=audit))
 lines=['# Experiment B1：Local Failure-Based Capability Gap Discovery','',
 '本轮探索局部失败是否提供新增、可验证的研究信号；没有生成算子，没有重跑 A/B0 正式评测，没有改动历史数据。所有新 Agent 使用 operator-runtime-v1 和原生 macOS 隔离。','',
 '## 1. 轨迹来源与检测规则','Natural Discovery 为 Experiment A ID Reflection 固定种子、分层抽取的60条历史轨迹，各难度20条，抽样不看结果。它描述历史基准分布，不代表真实世界自然频率。已有记录足够，不新增自然 API 采集。定向诊断、重复、确认全部单列，不能并入自然比例。','',
 '完整任务 solve 返回值与独立精确参考比较；未执行草稿、调试打印、拒答和显式子问题不自动算错误。断言失败保留 Suspected；资源/进程终止根因 Unknown。工具接口失败是局部操作错误，但不是数学能力缺口。机制分析由研究助手辅助，非自主发现。','',
 '## 2. Table 1：历史轨迹统计','| Metric | Count |','|---|---:|']
 for label,key in [('Natural Tasks','tasks'),('Final Success','final_success'),('Final Failure','final_failure'),('Observable Tool Traces (tasks)','observable_tool_tasks'),('Verifiable Intermediate Tasks','verifiable_intermediate_tasks'),('Observable Tool Steps','observable_tool_steps'),('Verifiable Intermediate Steps','verifiable_intermediate_steps'),('Tasks with Verified Local Failures','verified_local_failure_tasks'),('Verified Local Failure Steps','verified_local_failure_steps'),('Recovered Local Failure Tasks','recovered_local_tasks'),('Unrecovered Local Failure Tasks','unrecovered_local_tasks'),('Suspected / Unknown Tasks','unknown_tasks'),('Suspected / Unknown Steps','unknown_steps')]:lines.append(f'| {label} | {h[key]} |')
 f=review['functional'];lines+=['',f"Local Failure Incidence={h['local_incidence']['numerator']}/{h['local_incidence']['denominator']}；Recovered Rate（成功且可验证分母）={h['recovered_rate_verifiable']['numerator']}/{h['recovered_rate_verifiable']['denominator']}；全部最终成功任务分母={h['recovered_rate_all_success']['numerator']}/{h['recovered_rate_all_success']['denominator']}。",'',f"严格的错误完整计数返回：{f['wrong_integer_tasks']}个任务、{f['wrong_integer_steps']}个步骤；其中{f['recovered_wrong_integer_tasks']}个任务最终恢复。因此不能把21个恢复局部操作任务全部理解为21个数学建模错误。",'',
 '## 3. Table 2：局部失败来源','| Failure Type | Tasks | Verified Events | Recovered Tasks |','|---|---:|---:|---:|']
 for family,v in review['table2'].items():lines.append(f"| {family} | {v['tasks']} | {v['verified_events']} | {v['recovered_tasks']} |")
 lines+=['','任务可出现在多行，不能按行相加作为独立任务数。机制归因只用已审查源码及干预证据；错误整数本身不等于 Counting Error。3e95 的 solve2/solve 入口歧义归入 Tool Integration，不强归 M1。M3仅重放过的最后源码升级到机制级事件归因，其余版本保守保留 Unknown。','',
 '## 4. Table 3：机制诊断','| Mechanism | Trigger Program Errors | Control Program Errors | Agent Same-Mechanism Errors | Evidence |','|---|---:|---:|---:|---|',f"| M1 first-symbol state omission | {program_table['trigger']['original_errors']}/{program_table['trigger']['n']} | {program_table['control']['original_errors']}/{program_table['control']['n']} | {len(verified)} executions / {len(distinct_new)} distinct new inputs | minimal first-state augmentation; all paired repairs correct |",f'| M2 tuple/string mismatch | No planned M2 pairs | No planned M2 pairs | {len(secondary)} secondary diagnostic executions / {len(secondary_distinct)} inputs | type-only repairs; posthoc recurrence, not independent M2 confirmation |','| M3 premature truncation | Discovery audit only | Not run | Not run | previously B0-known; causal source replay, no novel Agent claim |','',
 'M1原程序及修复只在 count/adjacent/length3-pattern/endpoints 的支持接口内比较，未拿不支持的约束制造触发。Trigger与Control仅增加/删除首尾条件；跨样例变化包括是否相邻限制、是否模式计数、模式重叠、长度与计数参数。修复仅给原DP状态补首元素并在终点比较，没有重写求解器。','',
 '## 5. 原始 Agent 与 Reflection 对照','| Stage | Arm | Final correct | Local failure tasks | Recovered local | Mean input/output tokens | Mean calls/tools | Mean tool/latency seconds | Infra |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
 for stage in ['diagnostic','repeat','confirmation']:
  for arm,r in comparison[stage].items():lines.append(f"| {stage} | {arm} | {r['correct']}/{r['n']} | {r['local_failure_tasks']} | {r['recovered_local_tasks']} | {r['mean_input_tokens']:.0f}/{r['mean_output_tokens']:.0f} | {r['mean_calls']:.2f}/{r['mean_tools']:.2f} | {r['mean_tool_seconds']:.3f}/{r['mean_latency_seconds']:.3f} | {r['infrastructure_interruptions']} |")
 lines+=['',f'配对结果：`{pairs}`。每组每任务首次一次，固定pair0/4两种条件再各追加2次，共16次重复作答；独立确认8题各组一次。重复执行不增加独立任务数。所有组4逻辑调用、8192累计输出及未知重试预留、10秒工具、180秒端到端、5秒CPU、256MiB监测，SDK重试关闭。两组共享相同物理重试策略及每组¥4费用上限。','',
 '## 6. Table 4：能力缺口判断','| Mechanism | Grade | Reproducibility | Reflection Resistance | Programmability | Opportunity |','|---|---|---|---|---|---|']
 for g in gap:lines.append(f"| {g['mechanism_id']} | {g['grade']} | {g.get('verified_new_task_instances','not newly measured')} distinct new inputs | not established from name alone | state/type/history contract possible | {g['opportunity']} |")
 lines+=['','Grade C 不授予：即使新任务复现局部缺陷，也还需证明反事实区分能力、普通 Reflection 的稳定成本/正确率问题、现有工具不能低成本覆盖及程序化修复净收益。后两项本轮未证明。分级是本轮操作定义，不是外部认证标准。','',
 '## 7. Table 5：局部失败相对终局失败的新增信号','| Signal Source | Verified Mechanism Candidates | Independently Supported Mechanisms | High Opportunity Candidates |','|---|---:|---:|---:|',f"| Final Failure Only | {len(review['final_only_candidates'])} | 0 newly paired mechanisms | 0 |",f"| Final + Local Failure | {len(review['all_candidates'])} | 1 program-paired mechanism (M1); Agent support separately above | 0 |",'',f"在本轮最多3个已审查机制范围内，新增候选={len(review['added_candidates'])}：{review['added_candidates']}。Discovery Yield={len(review['all_candidates'])}/60。该比较不是对所有60条轨迹机制的穷尽发现召回率；没有将接口错误命名成大量新能力，也未将B0已知M3计作新的原创发现。新增候选不等于新增Grade C能力缺口。",'',
 '## 8. 独立保留、重复与反驳证据','机制预测先冻结，再生成/封存8题确认集；全部纳入，无按错答筛选，无根据确认结果调整机制。所有历史复用题标为Discovery/历史已用；配对和程序修复验证标Development。仅8个新确认输入提供初步独立证据；若将来用于改规格，必须转Development并另建新测试集。','',
 '程序机制成立不保证Agent会生成该程序。新 Agent 正确、不出现目标机制、对照失败及其他缺陷都属于反驳/限制证据。仅候选源码筛查数不作为 Agent 同机制错误率，必须经源码/独立干预审查。详见 new_source_mechanism_audit.json。','',
 '## 9. 预算与基础设施','历史只读分析及程序因果重放不调用模型。新模型调用成本：','| Arm | Physical requests | Known input/output tokens | Unknown usage | Known estimate CNY | Reservation ledger CNY |','|---|---:|---:|---:|---:|---:|']
 for arm,u in costs.items():lines.append(f"| {arm} | {u['api_request_attempts']} | {u['input_tokens']}/{u['output_tokens']} | {u['unknown_usage_requests']} | {u['known_token_estimate_cny']:.6f} | {u['reserved_or_reported_cost_cny']:.6f} |")
 lines+=['',f'合计 `{total}`。输入¥2/百万、输出¥8/百万为继承的保守估算，非账单；此前A的实际¥4.10和B0费用均不折算本轮。本地计算及研究助手辅助成本未定价。未知交付保留非零预留，不解释成数学错误。所有72条分母保留，不静默重试实验单元。','',
 '## 10. Q1–Q10 与下一阶段','Q1：最终成功47条中21条有已验证局部操作错误，其中6条有错误完整计数返回；可验证成功分母也为47。',f"Q2：在3个审查机制内，终局失败仅见{len(review['final_only_candidates'])}个，加入局部错误新增{len(review['added_candidates'])}个；不能从步骤数推机制数。",'Q3：1个机制接受新独立配对程序诊断；M2另有诊断样本中的事后类型缺陷复现，但无独立M2配对/确认设计；M3仅历史因果审查。',f'Q4：M1程序触发{program_table["trigger"]["original_errors"]}/10、对照{program_table["control"]["original_errors"]}/10，修复均正确；新Agent经审查同机制为{len(verified)}次、{len(distinct_new)}个新输入，二者不能混为一谈。',f'Q5：新的跨实例同机制证据={len(distinct_new)}个任务；历史M1也在多个独立成功轨迹中出现，仍受单模型和抽样分布限制。','Q6：见分组正确率、局部恢复、工具/Token/时延；没有把一次快速恢复推成普遍低成本，也不把低最终错误率推成无局部弱点。',f'Q7：M1当前Grade {grade}、{opportunity}；没有Grade C或已证明的新能力。','Q8：暂不建议直接扩大算子合成。先核实真实反思成本和复现条件，再决定是否做一个小粒度、可验证的状态/约束检查组件。','Q9：现有Python/Reflection覆盖仍强、机制级样本有限、历史接口错误较多、未验证新增工具净收益；不能仅凭局部错误数量 justify 新算子。','Q10：优先研究局部功能错误与接口错误的分离、可执行机制验证、以及在相同成功率下局部错误造成的额外重编码成本。','',
 '## 11. 限制与复现','单模型别名并非不可变权重；历史与新运行环境不同，历史比例不能当作新环境自然发生率。精确结果比较在已确认完整任务契约下可靠，但候选检测测试不等于已估计真实精确率/召回率。辅助机制审查不是全自动发现。重复同题不等于跨实例证据。确认题8道不能证明普遍稳定性。没有动态路由或新算子。','',
 '```sh','.venv/bin/python -m scripts.experiment_b1 history','.venv/bin/python -m scripts.b1_prepare_diagnosis','.venv/bin/python -m scripts.run_experiment_b1 freeze','.venv/bin/python -m scripts.run_experiment_b1 run --allow-api','.venv/bin/python -m scripts.report_experiment_b1','```','已有目录不覆盖；历史源和预测/输入/执行器哈希均检查，恢复只复用已有请求记录，不重做失败单元。','']
 text='\n'.join(lines);(ROOT/'experiment_b1_report.md').write_text(text)
 for name,lo,hi in [('mechanism_validation_report.md','## 4.','## 6.'),('capability_gap_report.md','## 6.','## 8.')]:
  (ROOT/name).write_text(text[text.index(lo):text.index(hi)])
 with (ROOT/'result_tables.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=['stage','arm','n','correct','local_failure_tasks','recovered_local_tasks','mean_calls','mean_tools','mean_tool_seconds','mean_latency_seconds']);w.writeheader()
  for stage,arms in comparison.items():
   for arm,r in arms.items():w.writerow(dict(stage=stage,arm=arm,**{k:r[k] for k in w.fieldnames if k in r}))
 print(json.dumps(dict(history=h,functional=review['functional'],new_candidates=len(newcandidates),comparison=comparison,gaps=gap,cost=total),ensure_ascii=False),flush=True)

if __name__=='__main__':report()
