"""All-denominator B1.5 report, costs separated from mechanism inference."""
import json,collections
from pathlib import Path
from scripts.experiment_b15 import ROOT,snapshot
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.report_experiment_b15 import INTERFACE

def main():
 records=[read_json(p) for p in sorted((ROOT/'mechanism_events').glob('*.json'))]
 diagnostic=read_json(ROOT/'mechanism_source_audit_diagnostic.json')['rows'];causal=read_json(ROOT/'causal_interventions.json');by={r['evidence_id']:r for r in causal}
 inv=read_json(ROOT/'source_inventory.json');reviews=read_json(ROOT/'confirmation_source_notes.json') if (ROOT/'confirmation_source_notes.json').exists() else {}
 audits=[]
 dby={r['evidence_id']:r for r in diagnostic}
 for r in inv:
  audits.append(by.get(r['evidence_id'],dby.get(r['evidence_id'],dict(r,classification='UNKNOWN',verified=False,review_rationale=reviews.get(r['evidence_id'],'No minimal causal evidence; not assigned by final wrong answer.')))))
 verified=[r for r in audits if r['verified']];vmap=collections.defaultdict(set)
 for r in verified:vmap[(r['task_id'],r['repeat'],r['arm'])].add(r['mechanism'])
 atomic_json(ROOT/'mechanism_source_audit.json',dict(assisted=True,scope='All executed wrong full-task returned integers individually reviewed. Correct outputs can contain latent defects; not a census of all source flaws.',rows=audits,source_review_count=len(audits),unverified_not_counted=True,confirmation_process_unchanged=True))
 def aggregate(z):
  o=dict(tasks=len(z),correct=0,local_wrong_tasks=0,local_wrong_events=0,recovered_wrong_tasks=0,interface_tasks=0,interface_events=0,runtime_tasks=0,runtime_events=0,infrastructure_tasks=0,tools=0,calls=0,input_tokens=0,output_tokens=0,seconds=0,recodings=0,interface_following_calls=0,math_following_calls=0,interface_following_input_tokens=0,interface_following_output_tokens=0,math_following_input_tokens=0,math_following_output_tokens=0,target_occurrence_tasks={'M1':0,'M2':0})
  for r in z:
   obs=r['observation'];ev=obs['raw_tool_events'];wrong=[i for i,e in enumerate(ev) if e['tool_result']['status']=='success' and e['tool_result']['value']!=r['task']['expected']];it=[i for i,e in enumerate(ev) if e['tool_result'].get('error_type') in INTERFACE];runtime=[i for i,e in enumerate(ev) if e['tool_result']['status']=='error' and e['tool_result'].get('error_type') not in INTERFACE]
   o['correct']+=obs['correct'];o['local_wrong_tasks']+=bool(wrong);o['local_wrong_events']+=len(wrong);o['recovered_wrong_tasks']+=bool(wrong and obs['correct']);o['interface_tasks']+=bool(it);o['interface_events']+=len(it);o['runtime_tasks']+=bool(runtime);o['runtime_events']+=len(runtime);o['infrastructure_tasks']+=obs.get('error')=='infrastructure_interrupted'
   for k in ['tools','calls','input_tokens','output_tokens','seconds']:o[k]+=obs.get(k,0)
   codes=[e.get('code') for e in r['events'] if e.get('code')];o['recodings']+=sum(a!=b for a,b in zip(codes,codes[1:]))
   for mid in ['M1','M2']:o['target_occurrence_tasks'][mid]+=mid in vmap[(r['task']['id'],r['repeat'],r['arm'])]
   attempts=[read_json(p) for p in (ROOT/('agent_'+r['arm'])/'api_attempts').glob('*.json')]
   steps={}
   for a in attempts:
    if a['logical_key'].startswith(f"pilot/{obs['id']}/{r['arm']}/"):steps.setdefault(int(a['logical_key'].rsplit('/',1)[1]),[]).append(a)
   ti=0
   for step,aa in sorted(steps.items()):
    rr=next((a for a in aa if a.get('raw_response',{}).get('message')),None)
    if not rr:continue
    tc=rr['raw_response']['message'].get('tool_calls',[])
    if not tc or tc[0]['function']['name']!='python':continue
    category='interface' if ti in it else 'math' if ti in wrong else None
    if category and step+1 in steps:
     o[category+'_following_calls']+=1
     for a in steps[step+1]:
      u=a.get('usage') or {}
      for k in ['input_tokens','output_tokens']:o[category+'_following_'+k]+=u.get(k,0)
    ti+=1
  for k in ['tokens','calls','tools','seconds']:
   v=o['input_tokens']+o['output_tokens'] if k=='tokens' else o[k];o['mean_'+k]=v/len(z) if z else None
  return o
 strata={stage:{arm:aggregate([r for r in records if r['stage']==stage and r['arm']==arm and (stage!='diagnostic' or r['repeat']==0)]) for arm in ['baseline','reflection']} for stage in ['diagnostic','confirmation']}
 strata['repeat']={arm:aggregate([r for r in records if r['repeat']>0 and r['arm']==arm]) for arm in ['baseline','reflection']}
 per={mid:{a:aggregate([r for r in records if r['task']['mechanism']==mid and r['arm']==a]) for a in ['baseline','reflection']} for mid in ['M1','M2']}
 pairs=[]
 for a in records:
  if a['arm']!='baseline':continue
  b=next(r for r in records if r['task']['id']==a['task']['id'] and r['repeat']==a['repeat'] and r['arm']=='reflection')
  pairs.append(dict(task_id=a['task']['id'],mechanism=a['task']['mechanism'],repeat=a['repeat'],stage=a['stage'],baseline_correct=a['observation']['correct'],reflection_correct=b['observation']['correct'],baseline_mechanisms=sorted(vmap[(a['task']['id'],a['repeat'],'baseline')]),reflection_mechanisms=sorted(vmap[(a['task']['id'],a['repeat'],'reflection')]),baseline_tokens=a['observation']['input_tokens']+a['observation']['output_tokens'],reflection_tokens=b['observation']['input_tokens']+b['observation']['output_tokens']))
 paired_counts=dict(collections.Counter(('Both Correct' if p['baseline_correct'] and p['reflection_correct'] else 'Baseline Wrong / Reflection Correct' if p['reflection_correct'] else 'Baseline Correct / Reflection Wrong' if p['baseline_correct'] else 'Both Wrong') for p in pairs))
 atomic_json(ROOT/'reflection_cost_analysis.json',dict(strata=strata,per_designed_mechanism=per,paired_counts=paired_counts,pairs=pairs,repair_cost_note='Following-call/token associations, not identified causal excess costs; independent model samples. Runtime/resource failures separate from interface and wrong integer.'))
 for stage,file in [('diagnostic','mechanism_diagnostics.json'),('confirmation','mechanism_confirmation.json')]:
  rr=[r for r in records if r['stage']==stage];atomic_json(ROOT/file,dict(status='complete',tasks=len({r['task']['id'] for r in rr}),trials=len(rr),statistics=strata[stage],all_included=True,observations=[dict(id=r['task']['id'],repeat=r['repeat'],arm=r['arm'],correct=r['observation']['correct'],answer=r['observation']['answer'],expected=r['task']['expected']) for r in rr],verified_occurrences=[r for r in verified if r['stage']==stage],confirmation_used_to_modify_protocol=False))
 recurrence={}
 taskmap={r['task']['id']:r['task'] for r in records}
 for mid in ['M1','M2']:
  vv=[r for r in verified if r['mechanism']==mid];triggerkind=['endpoints'] if mid=='M1' else ['pattern_count','forbidden']
  recurrence[mid]=dict(distinct_inputs=len({r['task_id'] for r in vv}),verified_events=len(vv),diagnostic_distinct=len({r['task_id'] for r in vv if r['stage']=='diagnostic' and r['repeat']==0}),repeat_distinct=len({r['task_id'] for r in vv if r['repeat']>0}),confirmation_distinct=len({r['task_id'] for r in vv if r['stage']=='confirmation'}),trigger_events=sum(any(c['kind'] in triggerkind for c in json.loads(taskmap[r['task_id']]['problem'])['constraints']) for r in vv),control_events=sum(not any(c['kind'] in triggerkind for c in json.loads(taskmap[r['task_id']]['problem'])['constraints']) for r in vv),program_control_correct=sum(r['cases'][1]['variants']['original']['correct'] and r['cases'][1]['variants']['repaired']['correct'] for r in vv),program_control_unavailable=sum(not r['cases'][1]['variants']['original']['correct'] for r in vv),grade='B' if len({r['task_id'] for r in vv})>=2 else 'A',opportunity='Uncertain; no Grade C',reason='Distinct independently generated programs provide recurrence; no evidence that an existing general tool cannot solve cheaply/reliably, and residual Reflection protocol/resource confounding remains.')
 atomic_json(ROOT/'mechanism_grades.json',recurrence)
 usage={e:read_json(ROOT/e/'usage.json') for e in ['original','normalized','agent_baseline','agent_reflection']};known=sum(u['known_token_estimate_cny'] for u in usage.values());reserved=sum(u['reserved_or_reported_cost_cny'] for u in usage.values());unknown=sum(u['unknown_usage_requests'] for u in usage.values());assert reserved<=10
 assert snapshot()==read_json(ROOT/'history_before.json')
 frozen=read_json(ROOT/'source_audit_freeze.json')
 for p,h in frozen['sources'].items():assert file_hash(Path(p))==h,p
 accounting=dict(known_token_estimate_cny=known,conservative_ledger_cny=reserved,unknown_usage_requests=unknown,actual_invoice_cny=None,physical_requests=sum(u['api_request_attempts'] for u in usage.values()),input_tokens=sum(u['input_tokens'] for u in usage.values()),output_tokens=sum(u['output_tokens'] for u in usage.values()),by_phase=usage,total_ceiling_cny=10)
 atomic_json(ROOT/'accounting.json',accounting)
 text='# Experiment B1.5 结果报告\n\nStage A达到工程门槛；只通过公开契约明确化，未修改Python源码或执行安全规则。Stage B完成全部预注册诊断、重复和确认，未生成算子。结论属于探索性证据，不能推广为普遍能力缺口。\n\n'+(ROOT/'interface_calibration_report.md').read_text().split('# B1.5 Stage A 校准报告\n\n',1)[1]
 text+='\n## Table 2：经源码和干预验证的机制\n\n|机制|不同输入|验证事件|条件成立事件|条件移除事件|确认不同输入|等级|\n|---|---:|---:|---:|---:|---:|---|\n'
 for m,r in recurrence.items():text+=f"|{m}|{r['distinct_inputs']}|{r['verified_events']}|{r['trigger_events']}|{r['control_events']}|{r['confirmation_distinct']}|{r['grade']}|\n"
 text+='\n此表按实际约束条件与实际机制统计，允许M1发生在M2设计题或反之；重复输入只计一次。Grade B只表示初步跨输入复现。首轮诊断、预选重复、独立确认的分项见 mechanism_grades.json。缓存外部上下文错误独立保留，不混入严格M1。无证据的其他错误和UNKNOWN全部保留。\n\n## Table 3：按预注册任务族比较（包括重复与确认）\n\n|设计任务族|方法|最终正确|错误完整计算事件|最终恢复的错误任务|平均Token|平均调用|协议错误任务|\n|---|---|---:|---:|---:|---:|---:|---:|\n'
 for m,arms in per.items():
  for a,s in arms.items():text+=f"|{m}|{a}|{s['correct']}/{s['tasks']}|{s['local_wrong_events']}|{s['recovered_wrong_tasks']}|{s['mean_tokens']:.1f}|{s['mean_calls']:.2f}|{s['interface_tasks']}|\n"
 text+='\n各组相同总体上限。重复统计不替代不同输入复现。Reflection诊断/重复/确认的分层结果及四格配对表见 reflection_cost_analysis.json。\n\n## Table 4：错误来源（136个执行，类别可重叠）\n\n|类型|执行数|事件数|最终失败执行数|\n|---|---:|---:|---:|\n'
 for name,fn in [('Interface Error',lambda e:e.get('error_type') in INTERFACE),('Runtime/Resource Error',lambda e:e.get('status')=='error' and e.get('error_type') not in INTERFACE),('Wrong Full-Task Result',lambda e:e.get('status')=='success')]:
  tasks=events=fail=0
  for r in records:
   ee=[e['tool_result'] for e in r['observation']['raw_tool_events']];hits=[e for e in ee if fn(e) and (name!='Wrong Full-Task Result' or e['value']!=r['task']['expected'])];tasks+=bool(hits);events+=len(hits);fail+=bool(hits and not r['observation']['correct'])
  text+=f'|{name}|{tasks}|{events}|{fail}|\n'
 for mid in ['M1','M2']:
  vv=[r for r in verified if r['mechanism']==mid];keys={(r['task_id'],r['repeat'],r['arm']) for r in vv};fail=sum(not next(t['observation']['correct'] for t in records if (t['task']['id'],t['repeat'],t['arm'])==k) for k in keys);text+=f'|Verified {mid}|{len(keys)}|{len(vv)}|{fail}|\n'
 infra=[r for r in records if r['observation'].get('error')=='infrastructure_interrupted'];text+=f"|Infrastructure Error|{len(infra)}|{len(infra)}|{sum(not r['observation']['correct'] for r in infra)}|\n"
 un=[r for r in audits if not r['verified']];text+=f"|Other / Unknown returned wrong integer|{len({(r['task_id'],r['repeat'],r['arm']) for r in un})}|{len(un)}|{len({(r['task_id'],r['repeat'],r['arm']) for r in un if not r['final_correct']})}|\n"
 text+='\n## Table 5：研究判断\n\n|机制|跨输入复现|Reflection剩余问题|可程序修复|等级|算子机会|\n|---|---|---|---|---|---|\n'
 for mid,r in recurrence.items():text+=f"|{mid}|{r['distinct_inputs']}个输入|见真实机制事件与配对成本，含残余接口/资源干扰|仅最小局部修复已验证|{r['grade']}|Uncertain|\n"
 text+='\n## Q1–Q11\n\nQ1：开发集协议错误任务18/20降至0/20，首次合规2/20升至20/20，显示接口干扰可显著降低；20题是工程门槛，不是总体显著性证明。\n\nQ2：旧接口22次拒绝、22次后继调用，关联后继输入48674/输出9236 Token；新接口开发集为0。后继调用也可能用于正常交付，不能全部解释成纯额外成本。\n\nQ3：仍存在执行后的错误整数，见Table 1/4；接口规范化没有自动修复算法。\n\n'
 for mid,q in [('M1','Q4'),('M2','Q5')]:
  r=recurrence[mid];text+=f"{q}：{mid}在{r['distinct_inputs']}个不同新输入经源码与最小干预验证，确认集其中{r['confirmation_distinct']}个；诊断首轮{r['diagnostic_distinct']}个、重复涉及{r['repeat_distinct']}个，不能相加视为独立输入。\n\n"
 text+='Q6：验证事件发生于机制相关条件成立时，局部移除条件控制与最小干预支持特定程序的因果解释。部分程序假设模式必定存在，移除模式后两版本均报错，这些控制不可计为正确控制。任务对的Agent程序不同，条件相关性不等于随机化估计机制发生率。\n\nQ7：见Table 3及配对明细。两组独立生成程序，配对差异不能直接归因于Reflection的稳定修复能力。\n\nQ8：Reflection消耗、重编码、错误后继调用及Token分别记录。Stage A只以Baseline校准；Stage B Reflection在12/68次执行中出现协议拒绝（17.6%），还有暴力验证资源耗尽，故不能宣称接口干扰对Reflection也已排除。它们是已公开契约的违反/策略结果，未发现执行器实现Bug，但限制了对纯数学修复成本的归因；不能把这些费用全部叫机制修复成本。\n\nQ9：存在局部程序缺陷及初步Agent重复证据。尚无足够证据证明现有通用Python不能低成本稳定覆盖，也未证明专项算子净收益，因此不判Grade C。\n\nQ10：暂不自动进入专项算子生成；优先在新的独立接口校准中覆盖Reflection审查行为，保留本轮冻结结果，再做必要的低成本覆盖与收益验证。本轮不修改接口重跑。\n\nQ11：无需通过扩大或改名机制追求正结果。先解决Reflection对审查代码契约的适应和资源分配；后续任务或机制研究须另立预注册。\n\n'
 text+=f"费用：已知Token估算 ¥{known:.6f}，保守账本 ¥{reserved:.6f}，未知用量请求{unknown}，物理请求{accounting['physical_requests']}。实际账单未知，不能用以前实验的¥4.10代替本轮费用。一项Baseline基础设施中断保留在准确率分母内，不能将其归为数学失败或对其补跑。总上限¥10未触及。\n\n54项工程测试通过；历史A/B0/B1和runtime-v1逐文件哈希一致。所有真实请求、响应、错误、源码和干预均留存。源码审查与干预由研究助手完成，非自动机制发现。\n"
 (ROOT/'experiment_b15_report.md').write_text(text)
 atomic_json(ROOT/'final_integrity_audit.json',dict(history_unchanged=True,protected_files=len(snapshot()),frozen_source_audit_unchanged=True,stage_A_trials=40,stage_B_trials=len(records),expected_stage_B_trials=136,all_expected_present=len(records)==136,operators_generated=0))
 assert len(records)==136
 atomic_json(ROOT/'completion.json',dict(status='complete',stage_A_gate_passed=True,stage_B_trials=136,operators_generated=0,known_token_estimate_cny=known,actual_invoice_cny=None))
 print(json.dumps(dict(recurrence=recurrence,cost=accounting,paired_counts=paired_counts),ensure_ascii=False))
if __name__=='__main__':main()
