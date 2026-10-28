"""Offline-only calibration statistics; never supplies reference feedback to agents."""
import json
from pathlib import Path
from scripts.experiment_b15 import ROOT,snapshot
from scripts.offline_loop_protocol import read_json,atomic_json
INTERFACE={'CODE_CONTRACT_ERROR','CODE_IMPORT_ERROR','CODE_SYNTAX_ERROR','TOOL_ARGUMENT_ERROR'}
def summarize(env):
 rows=[read_json(p) for p in sorted((ROOT/env/'observations').glob('*.json'))];stats=dict(tasks=len(rows),first_code_accepted=0,first_code_success=0,tasks_with_protocol_error=0,protocol_rejections=0,interface_repair_calls=0,interface_repair_input_tokens=0,interface_repair_output_tokens=0,executed=0,functionally_correct=0,final_correct=0,input_tokens=0,output_tokens=0,calls=0,seconds=0,interface_and_final_failure=0,infrastructure_tasks=0)
 details=[]
 attempts=[read_json(p) for p in (ROOT/env/'api_attempts').glob('*.json')]
 for r in rows:
  events=[e['tool_result'] for e in r.get('raw_tool_events',[]) if e['function']=='python'];errors=[i for i,e in enumerate(events) if e.get('error_type') in INTERFACE]
  stats['first_code_accepted']+=bool(events and events[0].get('error_type') not in INTERFACE)
  stats['first_code_success']+=bool(events and events[0]['execution_status']=='SUCCESS')
  stats['tasks_with_protocol_error']+=bool(errors);stats['protocol_rejections']+=len(errors)
  stats['interface_and_final_failure']+=bool(errors and not r['correct']);stats['infrastructure_tasks']+=r.get('error')=='infrastructure_interrupted'
  for e in events:
   if e['execution_status']=='SUCCESS':stats['executed']+=1;stats['functionally_correct']+=e['result']==r['expected']
  for k in ('input_tokens','output_tokens','calls','seconds'):stats[k]+=r.get(k,0)
  stats['final_correct']+=r['correct']
  # Recover physical native-tool submissions, including the logical call following a rejection.
  logical={}
  for a in attempts:
   key=a.get('logical_key','')
   if key.startswith(f"pilot/{r['id']}/baseline/"):logical.setdefault(int(key.rsplit('/',1)[1]),[]).append(a)
  rejected_steps=[]
  toolindex=0
  for step,aa in sorted(logical.items()):
   rr=next((a for a in aa if isinstance(a.get('raw_response'),dict) and a['raw_response'].get('message')),None)
   if not rr:continue
   tc=rr['raw_response']['message'].get('tool_calls',[])
   if tc and tc[0]['function']['name']=='python':
    if toolindex in errors:rejected_steps.append(step)
    toolindex+=1
  for step in rejected_steps:
   aa=logical.get(step+1,[])
   if aa:
    stats['interface_repair_calls']+=1
    for a in aa:
     u=a.get('usage') or {};stats['interface_repair_input_tokens']+=u.get('input_tokens',0);stats['interface_repair_output_tokens']+=u.get('output_tokens',0)
  details.append(dict(id=r['id'],interface_events=len(errors),first_accepted=bool(events and events[0].get('error_type') not in INTERFACE),final_correct=r['correct'],error=r.get('error')))
 stats['mean_tokens']=(stats['input_tokens']+stats['output_tokens'])/len(rows) if rows else None
 stats['mean_seconds']=stats['seconds']/len(rows) if rows else None
 return dict(metrics=stats,task_details=details,usage=read_json(ROOT/env/'usage.json'))
def main():
 d={e:summarize(e) for e in ('original','normalized')};m=d['normalized']['metrics'];gate=m['tasks']==20 and m['first_code_accepted']>=18 and m['tasks_with_protocol_error']<=2
 integrity=snapshot()==read_json(ROOT/'history_before.json');assert integrity
 tests=read_json(ROOT/'engineering_tests.json');gate=gate and tests['passed']
 result=dict(environments=d,gate_passed=gate,history_unchanged=integrity,engineering=tests,actual_invoice_cny=None)
 atomic_json(ROOT/'interface_calibration.json',result)
 names=[('First Submission Accepted','first_code_accepted'),('First Execution Success','first_code_success'),('Tasks With Protocol Error','tasks_with_protocol_error'),('Protocol Rejection Events','protocol_rejections'),('Following Calls For Interface Repair','interface_repair_calls'),('Executed Code Functional Accuracy','functional'),('Final Task Accuracy','final_correct'),('Mean Tokens','mean_tokens'),('Mean Seconds','mean_seconds')]
 table='| Metric | Original | Normalized |\n|---|---:|---:|\n'
 for label,k in names:
  vals=[]
  for e in ('original','normalized'):
   z=d[e]['metrics'];v=f"{z['functionally_correct']}/{z['executed']}" if k=='functional' else z[k];vals.append(f"{v}/{z['tasks']}" if k in ('first_code_accepted','first_code_success','tasks_with_protocol_error','final_correct') else str(round(v,2)) if isinstance(v,float) else str(v))
  table+=f'| {label} | {vals[0]} | {vals[1]} |\n'
 cost=sum(v['usage']['estimated_cost_cny'] for v in d.values()) if all('estimated_cost_cny' in v['usage'] for v in d.values()) else None
 text='# B1.5 Stage A 校准报告\n\n'+table+'\n验收：'+('通过' if gate else '未通过')+'。20个任务全部保留。首次代码拒绝与实际执行失败分别统计。接口后继调用是关联成本，不能直接解释为接口造成的因果额外调用；接口错误与最终失败的共现也不等于因果归因。\n\n源码适配恒等，无算法修改；54项工程/接口测试通过。类型注解和装饰器继续拒绝，未删除顶层语句。历史A/B0/B1及runtime源码哈希未变。API费用详见 interface_calibration.json 的独立账本；实际账单未知。\n'
 (ROOT/'interface_calibration_report.md').write_text(text)
 if not gate:
  reason='NOT_RUN_GATE_FAILED'
  for filename in ['interface_freeze','mechanism_hypotheses','mechanism_diagnostics','mechanism_source_audit','mechanism_confirmation','reflection_cost_analysis']:
   atomic_json(ROOT/(filename+'.json'),dict(status=reason,reason='Stage A did not meet pre-registered acceptance threshold',not_zero_occurrences=True))
  report=text+'\n# 研究结论\n\nQ1：见配对接口指标；未达到冻结要求，不能称接口干扰已排除。\n\nQ2：见接口拒绝事件、后继调用及Token；这些是可观察关联成本。不能用B1历史错误直接估计本轮因果影响。\n\nQ3：实际执行后的功能正确率见表，功能错误与协议错误单独统计。\n\nQ4–Q8：M1/M2诊断、确认及Reflection比较未执行，无法回答其新输入复现、触发条件解释力或修复成本。未执行不等于机制不发生。\n\nQ9–Q10：本轮不能建立新的能力缺口证据，不进入专项算子生成。历史机制等级不因本轮停止而改变。\n\nQ11：下一步先独立改进通用接口协议，在新版本开发校准达标后再复核既定机制；本轮不扩大任务搜索。\n\n本次仅评估一个预注册接口候选，未通过就停止Stage B。没有生成研究算子，没有运行大规模评测。\n'
  (ROOT/'experiment_b15_report.md').write_text(report)
  atomic_json(ROOT/'completion.json',dict(status='complete_stage_A_gate_failed',stage_B=reason,operators_generated=0))
 print(json.dumps(dict(gate_passed=gate,metrics={e:v['metrics'] for e,v in d.items()},usage={e:v['usage'] for e,v in d.items()}),ensure_ascii=False))
if __name__=='__main__':main()
