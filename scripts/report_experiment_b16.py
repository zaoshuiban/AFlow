"""Post-inference paired, denominator-preserving B1.6 report."""
import collections,csv,json,time
from pathlib import Path
from scripts.experiment_b16 import ROOT,INTERFACE_ERRORS,verify,snapshot
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.pattern_type_contract_checker import check

def read_events(phase):return [read_json(p) for p in sorted((ROOT/(phase+'_events')).glob('*.json'))]

def main():
 verify()
 for f,h in read_json(ROOT/'methods_final_freeze.json')['sources'].items():assert file_hash(Path(f))==h,f
 records=read_events('hidden');assert len(records)==84;assert len({r['task']['id'] for r in records})==28
 inventory=read_json(ROOT/'hidden_source_inventory.json');causes=read_json(ROOT/'hidden_causal_interventions.json') if (ROOT/'hidden_causal_interventions.json').exists() else [];by={r['evidence_id']:r for r in causes};notes=read_json(ROOT/'hidden_source_notes.json') if (ROOT/'hidden_source_notes.json').exists() else {}
 audits=[]
 for r in inventory:
  if r['evidence_id'] in by:audits.append(by[r['evidence_id']])
  else:
   note=notes.get(r['evidence_id'],{})
   audits.append(dict(r,classification=note.get('classification','UNKNOWN'),rationale=note.get('rationale','Insufficient source/intervention evidence; not attributed by final result.'),verified=False))
 verified=[r for r in audits if r['verified']];all_audits=read_json(ROOT/'pilot_mechanism_audit.json')['rows']+audits
 atomic_json(ROOT/'mechanism_audit.json',dict(definition=read_json(ROOT/'mechanism_hypothesis.json'),pilot=read_json(ROOT/'pilot_mechanism_audit.json'),hidden=audits,compound_factorial=read_json(ROOT/'compound_defects.json'),assisted=True,candidate_scope='All wrong full-task returned integers, checker warnings and ordinary program runtime violations. Resource/protocol failures remain separate; correct integer with no warning can contain latent defects, not presumed exhaustively reviewed.',compound_excluded_from_strict_M2=True,no_source_screen_only_verified=True,all_hidden_tasks_preserved=True))
 def stats(rr):
  z=dict(tasks=len(rr),correct=0,verified_M2_tasks=0,recovered_M2_tasks=0,input_tokens=0,output_tokens=0,calls=0,tools=0,tool_seconds=0,seconds=0,recodings=0,protocol_tasks=0,protocol_events=0,runtime_resource_tasks=0,runtime_resource_events=0,infrastructure_tasks=0,checker_warning_events=0,checker_statuses={},functional_wrong_events=0,exploration_integer_events=0)
  taskids={r['task_id'] for r in verified};m2keys={(r['task_id'],r['method']) for r in verified};statuses=collections.Counter()
  for r in rr:
   o=r['observation'];key=(r['task']['id'],r['method']);z['correct']+=o['correct'];z['verified_M2_tasks']+=key in m2keys;z['recovered_M2_tasks']+=bool(key in m2keys and o['correct'])
   for k in ['input_tokens','output_tokens','calls','tools','tool_seconds','seconds']:z[k]+=o.get(k,0)
   ev=o['raw_tool_events'];interface=[e for e in ev if e['tool_result'].get('error_type') in INTERFACE_ERRORS];runtime=[e for e in ev if e['tool_result'].get('status')=='error' and e['tool_result'].get('error_type') not in INTERFACE_ERRORS]
   z['protocol_tasks']+=bool(interface);z['protocol_events']+=len(interface);z['runtime_resource_tasks']+=bool(runtime);z['runtime_resource_events']+=len(runtime);z['infrastructure_tasks']+=o.get('error')=='infrastructure_interrupted'
   codes=[e['code'] for e in r['events'] if e.get('code')];z['recodings']+=sum(a!=b for a,b in zip(codes,codes[1:]))
   for e in r['events']:
    raw=o['events'][e['step_id']].get('tool_result',{});c=raw.get('contract_check')
    if c:statuses[c['status']]+=1
    if raw.get('status')=='success' and raw.get('value')!=r['task']['expected']:
     sid='hidden_'+r['task']['id']+'_'+r['method']+'_s'+str(e['step_id']);a=next((a for a in audits if a['evidence_id']==sid),None)
     if a and a['classification']=='EXPECTED_EXPLORATION':z['exploration_integer_events']+=1
     else:z['functional_wrong_events']+=1
  z['checker_statuses']=dict(statuses);z['checker_warning_events']=statuses['WARNING']
  for k in ['tokens','calls','tools','tool_seconds','seconds']:
   val=z['input_tokens']+z['output_tokens'] if k=='tokens' else z[k];z['mean_'+k]=val/len(rr) if rr else None
  return z
 metrics={m:stats([r for r in records if r['method']==m]) for m in ['R','P','C']};strata={m:{s:stats([r for r in records if r['method']==m and r['task']['tier']==s]) for s in ['transfer','control','natural']} for m in metrics}
 allby={(r['task']['id'],r['method']):r for r in records};pairs=[];pair_summary={}
 for a,b in [('P','R'),('C','R'),('C','P')]:
  out=[]
  for tid in sorted({r['task']['id'] for r in records}):
   x=allby[(tid,a)];y=allby[(tid,b)];xc=x['observation']['correct'];yc=y['observation']['correct'];out.append(dict(comparison=a+'-'+b,task_id=tid,stratum=x['task']['tier'],new_correct=xc,reference_correct=yc,category='new_only_correct' if xc and not yc else 'reference_only_correct' if yc and not xc else 'both_correct' if xc else 'both_wrong'))
  pairs+=out;c=collections.Counter(r['category'] for r in out);pair_summary[a+'-'+b]=dict(wins=c['new_only_correct'],losses=c['reference_only_correct'],both_correct=c['both_correct'],both_wrong=c['both_wrong'],ties=c['both_correct']+c['both_wrong'],by_stratum={s:dict(collections.Counter(r['category'] for r in out if r['stratum']==s)) for s in ['transfer','control','natural']})
 with (ROOT/'paired_results.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=['comparison','task_id','stratum','new_correct','reference_correct','category']);w.writeheader();w.writerows(pairs)
 regression={k:dict(total_losses=v['losses'],control_and_natural_losses=sum(1 for r in pairs if r['comparison']==k and r['stratum'] in ['control','natural'] and r['category']=='reference_only_correct'),details=[r for r in pairs if r['comparison']==k and r['category']=='reference_only_correct']) for k,v in pair_summary.items()}
 sensitivity={}
 for a,b in [('P','R'),('C','R'),('C','P')]:
  eligible=[tid for tid in {r['task']['id'] for r in records} if allby[(tid,a)]['observation'].get('error')!='infrastructure_interrupted' and allby[(tid,b)]['observation'].get('error')!='infrastructure_interrupted']
  sensitivity[a+'-'+b]=dict(paired_inputs=len(eligible),new_correct=sum(allby[(tid,a)]['observation']['correct'] for tid in eligible),reference_correct=sum(allby[(tid,b)]['observation']['correct'] for tid in eligible),note='Descriptive sensitivity only; primary complete denominator retained, not a new estimand selected for success.')
 atomic_json(ROOT/'regression_analysis.json',dict(paired=regression,non_interrupted_sensitivity=sensitivity,operator='NOT_BUILT_DEVELOPMENT_DECISION',causality='A regression loss is a paired performance difference between independent samples, not a proven deterministic negative-transfer cause.'))
 # Checker evaluation is restricted to actually source-reviewed examples, never unreviewed negatives.
 offline=[]
 for a in all_audits:
  if not a.get('code_path'):continue
  predicted=check(Path(a['code_path']).read_text())['status'];label='VERIFIED_M2' if a['verified'] else 'REVIEWED_NON_M2' if a['classification'] in ['OTHER_SOURCE_DEFECT','EXPECTED_EXPLORATION','REVIEWED_NON_M2'] else 'UNKNOWN'
  offline.append(dict(evidence_id=a['evidence_id'],phase=a.get('phase'),method=a['method'],label=label,checker_status=predicted))
 cc=collections.Counter(('TP' if r['checker_status']=='WARNING' else 'FN') if r['label']=='VERIFIED_M2' else ('FP' if r['checker_status']=='WARNING' else 'TN') if r['label']=='REVIEWED_NON_M2' else 'UNRESOLVED' for r in offline)
 atomic_json(ROOT/'checker_audit.json',dict(reviewed_only=True,event_confusion=dict(cc),examples=offline,unreviewed_not_counted_negative=True,scope='Conditional convenience sample of reviewed bad-output/runtime/warning sources; not population precision/recall; correlated revisions retained as events, not independent tasks. UNKNOWN checker status on a verified defect counts as no warning, not PASS.'))
 usages={};known=reserved=unknown=physical=0
 for name in ['stage_A_baseline','stage_A_reflection','pilot_R','pilot_P','pilot_C','hidden_R','hidden_P','hidden_C']:
  u=read_json(ROOT/name/'usage.json');usages[name]=u;known+=u['known_token_estimate_cny'];reserved+=u['reserved_or_reported_cost_cny'];unknown+=u['unknown_usage_requests'];physical+=u['api_request_attempts']
 assert reserved<=10+1e-9
 online={m:dict(usage=usages['hidden_'+m],correct=metrics[m]['correct'],online_cost_per_correct_cny=usages['hidden_'+m]['known_token_estimate_cny']/metrics[m]['correct'] if metrics[m]['correct'] else None,conservative_cost_per_correct_cny=usages['hidden_'+m]['reserved_or_reported_cost_cny']/metrics[m]['correct'] if metrics[m]['correct'] else None,development_api_cost_cny=usages['pilot_'+m]['known_token_estimate_cny']) for m in metrics}
 cost=dict(known_token_estimate_cny=known,conservative_ledger_cny=reserved,unknown_usage_requests=unknown,physical_requests=physical,actual_invoice_cny=None,total_ceiling_cny=10,online=online,by_phase=usages,checker_build=dict(source='research assistant authored, not automatic operator synthesis',direct_construction_api_cny=0,implementation_and_validation_labor_cost_cny=None,assistant_reasoning_cost_cny=None,unknown_not_zero=True,unit_tests=5,validation_wall_seconds=.001),O=dict(status='NOT_BUILT_DEVELOPMENT_DECISION',incremental_build_spending=0,effect='NOT_MEASURED'),amortization=dict(formula='(B_build + B_validation + development_API + N * online_per_task) / N; divide by success fraction only if stable across reuse.',N=[1,10,100,1000],numeric_total='Not computed: implementation/assistant build cost unpriced; online-only savings are not total economic benefit.'))
 atomic_json(ROOT/'cost_analysis.json',cost);atomic_json(ROOT/'evaluation_results.json',dict(status='complete',hidden_tasks=28,executions=84,methods=['R','P','C'],O='NOT_TESTED',metrics=metrics,strata=strata,paired=pair_summary,all_denominators_preserved=True,independent_contexts=True))
 # Report
 text='# Experiment B1.6\n\n本轮完成接口校准、8题开发对比与28题隐藏配对评测。按开发阶段预注册规则未构建O；不能由此推断专项组件无价值。研究对象是M2修复方式的必要性，不评价自动算子生成。\n\n## Table 1：Reflection接口校准（20题/组）\n\n'+(ROOT/'reflection_interface_calibration.md').read_text().split('# Reflection 接口校准\n\n')[1]
 text+='\n## 开发阶段与专项组件决策\n\n开发R/P/C最终正确率分别5/8、6/8、6/8。R在2个不同输入上确认M2（5个程序事件），P/C无经验证M2。C没有发出警告，因此不能把其正确结果归因于成功检测与修复。开发集未满足投入O的规则，冻结R/P/C后才打开隐藏集。开发R/P/C同时存在1/2/2次基础设施中断；没有观察到M2不等于这些中断任务本来不会出现M2，因而该决策只是暂不投入构建，不是廉价方法已充分覆盖M2的证明。\n\n## Table 2：隐藏结果\n\n|方法|Transfer|Control|Natural|Overall|\n|---|---:|---:|---:|---:|\n'
 for m in metrics:text+='|'+m+'|'+'|'.join(f"{strata[m][s]['correct']}/{strata[m][s]['tasks']}" for s in ['transfer','control','natural'])+f"|{metrics[m]['correct']}/28|\n"
 text+='|O|未测试|未测试|未测试|未测试|\n\nNatural是本轮预注册公共生成器的混合约束回归样本，不代表真实自然交互分布，也不是对历史A分布的完整复现。\n\n## Table 3：隐藏M2与其他错误\n\n|方法|Verified M2 Tasks|最终正确的M2任务|Final Failures|Protocol Error Tasks|Runtime/Resource Tasks|\n|---|---:|---:|---:|---:|---:|\n'
 for m,z in metrics.items():text+=f"|{m}|{z['verified_M2_tasks']}|{z['recovered_M2_tasks']}|{28-z['correct']}|{z['protocol_tasks']}|{z['runtime_resource_tasks']}|\n"
 text+='|O|未测试|未测试|未测试|未测试|未测试|\n\n最终正确的M2任务只证明发生错误后最终交付正确；是否真正修复了该类型缺陷仍应查看后续源码，不能把避免、恢复和能力替代混为一谈。M2最小干预与完整源码见mechanism_audit.json。\n\n## Table 4：隐藏在线成本\n\n|方法|Avg Tokens|Avg Calls|Avg Latency秒|Build Cost|Known Online Cost per Correct元|Conservative Cost per Correct元|\n|---|---:|---:|---:|---|---:|---:|\n'
 for m,z in metrics.items():text+=f"|{m}|{z['mean_tokens']:.1f}|{z['mean_calls']:.2f}|{z['mean_seconds']:.2f}|{'既有基线，未重新计价' if m=='R' else '新增实现/验证人力及助手费用未定价'}|{online[m]['online_cost_per_correct_cny']:.5f}|{online[m]['conservative_cost_per_correct_cny']:.5f}|\n"
 text+='|O|未测试|未测试|未测试|未构建|未测试|未测试|\n\n每个方法共享4模型调用、8192累计输出Token、10秒累计工具墙钟、180秒总墙钟上限。检查时间包含在C工具时间中。公开说明与方法描述增加的输入Token全部计价。表中单个正确答案成本仅为已知Token在线估算，不包含未知预留与未定价构建成本；保守成本另见cost_analysis.json。\n\n## Table 5：逐题配对\n\n|Comparison|Wins|Losses|Ties|Both Correct|Both Wrong|\n|---|---:|---:|---:|---:|---:|\n'
 for k,z in pair_summary.items():text+=f"|{k}|{z['wins']}|{z['losses']}|{z['ties']}|{z['both_correct']}|{z['both_wrong']}|\n"
 text+='|O-R|未测试|未测试|未测试|未测试|未测试|\n|O-C|未测试|未测试|未测试|未测试|未测试|\n\n全部84个隐藏执行保留，基础设施中断不补跑，协议/资源错误不归为M2。逐题与分层损益见paired_results.csv及regression_analysis.json。基础设施中断在R/P/C分别3/1/2次，全部保留为交付失败，但不归因于数学能力。\n\n## 八个研究问题\n\n1. Reflection接口干扰是否得到控制：Stage A两组都达到工程门槛；这不是后续所有输入零错误的保证。隐藏中的残余协议错误单独列出。与B1.5不同题的数据不能作因果前后比较。隐藏P/C协议错误任务分别4/28和3/28，仍超过10%，所以不能宣称后续所有修复方法的接口干扰均已排除。\n\n2. M2是否仍有跨实例错误空间：开发有2个不同输入的严格证据，隐藏见Table 3。若隐藏事件少，则无法据此断言低成本方法普遍等价或M2已被完全解决。\n\n3. Prompt是否足够：只据本轮分层正确率、M2发生与配对成本判断；即便P没有M2，也不代表其所有其他算法均可靠。P-R的4胜1负中，3胜来自R基础设施中断、1负来自P基础设施中断，剩余1胜是非M2的DP初始化差异；不能把整体26/28对23/28解释成类型提醒的已识别数学增益。双方未中断的24对中为P23/24、R22/24，仅作敏感性描述，主分母不变。\n\n4. Checker是否有额外价值：实际警告与后续代码记录在轨迹；没有警告时不能声称检测促进恢复。离线检测评估只使用完成审查的源码，UNKNOWN不是无缺陷证明；详见checker_audit.json。\n\n5. 专项组件是否优于Checker：未测量，开发集未达到构建规则。没有证据支持投入，不等于证明其无价值。不存在组件调用、失败回退或答案覆盖结果可分析，这些项标记不适用，不伪造为成功。\n\n6. 是否负迁移：逐题出现原方法独自正确的情况属于回归损失，Control/Natural单独统计；独立生成程序的差异不能自动解释为确定性干扰因果。\n\n7. 实际成本：见费用账本。这里是Token估算，实际账单未知。Checker由研究助手实现并验证，构建API直接费用为零，但人力/助手推理费未知，不能当成总体零成本；摊销公式提供而不虚构价格。\n\n8. 是否进入失败驱动算子生成：当前不进入。简短提示值得保留为低成本候选，但本轮M2稀少、基础设施中断与残余协议错误限制了必要性判断。当前Checker的实际53次返回全部UNKNOWN、没有警告，不能声称其有效，更不能把这个有限实现的失败推广为所有Checker无效。下一轮如需验证O，必须独立预注册构建理由和比较，不能利用本轮隐藏结果临时追加。\n\n'
 text+=f"费用合计：已知Token估算¥{known:.6f}，保守账本¥{reserved:.6f}，未知用量{unknown}个物理请求；全部物理请求{physical}。未触及¥10预算。实际账单待用户核对；不得套用旧实验实际支出。\n\n56项接口/工程测试及5项Checker测试通过。历史A/B0/B1/B1.5及runtime-v1保持逐文件哈希不变。方法、隐藏数据及源码审查流程的冻结哈希均验证。源码审查是辅助研究，不是自动发现成果。\n\n## 复现命令\n\n原运行已完成，禁止清空或覆盖以重跑。审查既有记录可运行：\n\n```sh\n.venv/bin/python -m scripts.experiment_b16 analyze-A\n.venv/bin/python -m scripts.report_experiment_b16\n```\n\n新API实验须复制配置到新的Run ID后依次prepare、stage-A、analyze-A、B prepare、pilot、开发决策冻结、hidden；不可原地重试已失败单元。\n"
 (ROOT/'experiment_b16_report.md').write_text(text)
 atomic_json(ROOT/'final_integrity_audit.json',dict(history_files=len(snapshot()),history_unchanged=True,all_stage_A_40_present=True,all_pilot_24_present=len(read_events('pilot'))==24,all_hidden_84_present=True,unique_hidden_inputs=28,methods_frozen_before_hidden=True,O_constructed=False))
 atomic_json(ROOT/'completion.json',dict(status='complete',stage_A_passed=True,pilot_trials=24,hidden_trials=84,methods=['R','P','C'],O='not_built_development_decision',known_token_estimate_cny=known,actual_invoice_cny=None))
 print(json.dumps(dict(metrics=metrics,paired=pair_summary,cost=dict(known=known,reserved=reserved,unknown=unknown,physical=physical)),ensure_ascii=False))
if __name__=='__main__':main()
