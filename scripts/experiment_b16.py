"""B1.6 gated execution, new files only; reuse frozen runtime-v1."""
import argparse,asyncio,hashlib,json,random,shutil
from pathlib import Path
from scripts.offline_loop_protocol import atomic_json,read_json,file_hash
from scripts.operator_runtime import Transport,solve
from scripts.python_tool_interface_v3 import InterfaceTransport,executor,CONTRACT,SCHEMA_DESCRIPTION
from scripts.compositional_counting import SPEC,KINDS,make_instance,exact_dp,brute_force
from scripts.experiment_b1 import load_codes
ROOT=Path('results/experiment_b16_20261015_v1')
PROTECTED=['results/generated_operator_experiment_a_v1','results/experiment_b0_20261014_v1','results/experiment_b1_20261015_v1','results/experiment_b15_20261015_v1','scripts/operator_runtime','scripts/python_tool_interface_v2.py']
DEPENDENCIES=['scripts/experiment_a_calibration.py','scripts/experiment_a_pilot.py','scripts/counting_worker.py','scripts/frozen_math_worker.py','scripts/compositional_counting.py']
INTERFACE_ERRORS={'CODE_CONTRACT_ERROR','CODE_IMPORT_ERROR','CODE_SYNTAX_ERROR','TOOL_ARGUMENT_ERROR'}
def snapshot():
 out={}
 for d in PROTECTED:
  q=Path(d)
  for p in ([q] if q.is_file() else q.rglob('*')):
   if p.is_file() and '__pycache__' not in str(p):out[str(p)]=file_hash(p)
 return out

def prior_inputs():
 used=set()
 for p in Path('results').rglob('*.json'):
  if ROOT in p.parents:continue
  try:v=read_json(p)
  except Exception:continue
  def walk(v):
   if isinstance(v,dict):
    if isinstance(v.get('problem'),str):
     try:used.add(json.dumps(json.loads(v['problem']),sort_keys=True))
     except Exception:pass
    for x in v.values():walk(x)
   elif isinstance(v,list):
    for x in v:walk(x)
  walk(v)
 return used

def make_rows(rng,count,used,role):
 rows=[];cross=[]
 for i in range(count):
  for attempt in range(10000):
   kinds=rng.sample(KINDS,1+i%5);n=rng.choice([6,7,9,11,14,16]);p=make_instance(rng,kinds,n);s=json.dumps(p,sort_keys=True)
   if s not in used:break
  else:raise RuntimeError('Dedup exhausted, no outcome-based sampling')
  used.add(s);expected=exact_dp(p)['answer'];small=json.loads(s);small['n']=min(7,n)
  for c in small['constraints']:
   if c['kind']=='count':c['exact']=min(c['exact'],small['n'])
   if c['kind']=='pattern_count':c['exact']=min(c['exact'],max(0,small['n']-len(c['pattern'])+1))
  a=exact_dp(small)['answer'];b=brute_force(small);assert a==b
  tid=hashlib.sha256(s.encode()).hexdigest()[:20];rows.append(dict(id=tid,problem=s,expected=expected,tier=role,scale=str(n)));cross.append(dict(id=tid,small=small,dp=a,brute=b))
 return rows,cross

def manifest(root,name,budget):
 root.mkdir(parents=True,exist_ok=True)
 atomic_json(root/'manifest.json',dict(runtime_version='operator-runtime-v1',experiment='B1.6 '+name,model_alias='deepseek-v4-flash',max_requests=600,api_budget_cny=budget,retry_policy=dict(max_retries=2,base_delay=1,max_delay=60),extra_limits=dict(max_input_tokens=1500000,max_output_tokens=300000,max_wall_seconds=180),temperature=0,thinking='disabled'))

def prepare():
 if (ROOT/'protocol.json').exists():raise FileExistsError('Run already prepared')
 ROOT.mkdir(parents=True,exist_ok=True)
 rows,cross=make_rows(random.Random(2610151601),20,prior_inputs(),'interface_development')
 atomic_json(ROOT/'history_before.json',snapshot());atomic_json(ROOT/'stage_A_tasks.json',rows);atomic_json(ROOT/'stage_A_crosschecks.json',cross)
 sources=['scripts/experiment_b16.py','scripts/python_tool_interface_v3.py','tests/test_experiment_b16_interface.py']+DEPENDENCIES+[str(p) for p in sorted(Path('scripts/operator_runtime').glob('*.py'))]
 atomic_json(ROOT/'protocol.json',dict(stage_A_n=20,interface_version='python-tool-interface-v3',max_candidate_versions=1,stage_A_thresholds=dict(first_accepted_at_least=18,protocol_error_tasks_at_most=2,both_arms_required=True),total_budget_cny=10,allocated=dict(stage_A_baseline=1,stage_A_reflection=1,pilot_R=.6,pilot_P=.6,pilot_C=.6,pilot_O=.6,hidden_R=1.4,hidden_P=1.4,hidden_C=1.4,hidden_O=1.4),single_task_budget=dict(calls=4,output_tokens=8192,tool_wall_seconds=10,worker_cpu_seconds=5,wall_seconds=180),seeds=dict(stage_A=2610151601,schedule=2610151602),sources={f:file_hash(Path(f)) for f in sources},stage_A_sha256=file_hash(ROOT/'stage_A_tasks.json'),adapter='identity; no new permissions or math repair',stage_B_gate='Only after BOTH Stage A arms pass',operator_pilot_decision='Freeze concrete rule before Stage B Pilot; no hidden outcomes consulted'))
 for arm in ['baseline','reflection']:manifest(ROOT/('stage_A_'+arm),arm+' calibration',1)
 shutil.copyfile('/Users/majiatong/.codex/attachments/434abd87-58c1-45d6-a358-305840016ff1/已粘贴的文本.txt',ROOT/'user_protocol.txt')
 print('Stage A frozen: 20 new tasks, both arms, 2 CNY ceiling; total protocol <=10 CNY',flush=True)

def verify():
 p=read_json(ROOT/'protocol.json')
 for f,h in p['sources'].items():assert file_hash(Path(f))==h,f
 assert file_hash(ROOT/'stage_A_tasks.json')==p['stage_A_sha256']
 assert snapshot()==read_json(ROOT/'history_before.json')
 return p

async def stage_A():
 verify();transports={a:Transport(ROOT/('stage_A_'+a)) for a in ['baseline','reflection']};work=[(r,a) for r in read_json(ROOT/'stage_A_tasks.json') for a in transports];random.Random(2610151602).shuffle(work);sem=asyncio.Semaphore(3)
 atomic_json(ROOT/'stage_A_schedule.json',[dict(id=r['id'],arm=a) for r,a in work])
 async def one(row,arm):
  async with sem:
   root=ROOT/('stage_A_'+arm);dest=root/'observations'/f"{row['id']}_{arm}.json"
   if dest.exists():return
   await solve(root,InterfaceTransport(transports[arm]),row,arm,SPEC,executor=executor)
   print(json.dumps(dict(arm=arm,done=len(list((root/'observations').glob('*.json'))),total=20)),flush=True)
 try:await asyncio.gather(*(one(r,a) for r,a in work))
 finally:
  for a,t in transports.items():atomic_json(ROOT/('stage_A_'+a)/'usage.json',t.usage());await t.client.close()

def analyze_A():
 verify();stats={};failures=[]
 for arm in ['baseline','reflection']:
  root=ROOT/('stage_A_'+arm);rows=[read_json(p) for p in sorted((root/'observations').glob('*.json'))];attempts=[read_json(p) for p in (root/'api_attempts').glob('*.json')]
  z=dict(tasks=len(rows),first_accepted=0,first_success=0,protocol_tasks=0,protocol_events=0,correct=0,input_tokens=0,output_tokens=0,tools=0,calls=0,seconds=0,normal_execution=0,interface_following_calls=0,interface_following_input_tokens=0,interface_following_output_tokens=0)
  for r in rows:
   ev=[e['tool_result'] for e in r['raw_tool_events']];bad=[i for i,e in enumerate(ev) if e.get('error_type') in INTERFACE_ERRORS]
   z['first_accepted']+=bool(ev and ev[0].get('error_type') not in INTERFACE_ERRORS);z['first_success']+=bool(ev and ev[0]['status']=='success');z['protocol_tasks']+=bool(bad);z['protocol_events']+=len(bad);z['correct']+=r['correct'];z['normal_execution']+=sum(e['status']=='success' for e in ev)
   for k in ['input_tokens','output_tokens','tools','calls','seconds']:z[k]+=r[k]
   logical={}
   for a in attempts:
    if a['logical_key'].startswith(f"pilot/{r['id']}/{arm}/"):logical.setdefault(int(a['logical_key'].rsplit('/',1)[1]),[]).append(a)
   ti=0
   for step,aa in sorted(logical.items()):
    rr=next((a for a in aa if (a.get('raw_response') or {}).get('message')),None)
    if not rr:continue
    tc=rr['raw_response']['message'].get('tool_calls',[])
    if not tc or tc[0]['function']['name']!='python':continue
    if ti in bad and step+1 in logical:
     z['interface_following_calls']+=1
     for a in logical[step+1]:
      u=a.get('usage') or {}
      for k in ['input_tokens','output_tokens']:z['interface_following_'+k]+=u.get(k,0)
    ti+=1
   if bad:
    codes=load_codes(root,r);failures.append(dict(arm=arm,id=r['id'],correct=r['correct'],events=[dict(step=i,error=e.get('tool_result',{}).get('error'),code=codes.get(i)) for i,e in enumerate(r['events']) if e.get('tool_result',{}).get('error_type') in INTERFACE_ERRORS]))
  for k in ['tokens','tools','seconds']:
   val=z['input_tokens']+z['output_tokens'] if k=='tokens' else z[k];z['mean_'+k]=val/len(rows) if rows else None
  stats[arm]=z
 engineering=read_json(ROOT/'engineering_tests.json');gate=engineering['passed'] and all(z['tasks']==20 and z['first_accepted']>=18 and z['protocol_tasks']<=2 for z in stats.values())
 atomic_json(ROOT/'stage_A_statistics.json',dict(gate_passed=gate,stats=stats,engineering=engineering));atomic_json(ROOT/'stage_A_protocol_errors.json',failures)
 table='|Metric|Baseline|Reflection|\n|---|---:|---:|\n'
 for label,k in [('First Submission Accepted','first_accepted'),('Tasks with Protocol Error','protocol_tasks'),('Protocol Rejection Events','protocol_events'),('First Execution Success','first_success'),('Final Accuracy','correct'),('Mean Tokens','mean_tokens'),('Mean Tool Calls','mean_tools'),('Mean Latency seconds','mean_seconds')]:
  vals=[stats[a][k] for a in ['baseline','reflection']];table+='|'+label+'|'+'|'.join(str(round(v,2)) if isinstance(v,float) else str(v)+'/20' if k in ['first_accepted','protocol_tasks','first_success','correct'] else str(v) for v in vals)+'|\n'
 text='# Reflection 接口校准\n\n'+table+'\n工程门槛：'+('通过' if gate else '未通过')+'。两组均须首次合规至少18/20、协议错误任务至多2/20。全部真实错误留存，无结果筛选或补跑。接口后继调用/Token是关联成本，不是精确的因果额外成本。真实返回整数是否正确与接口合规分别统计。\n'
 (ROOT/'reflection_interface_calibration.md').write_text(text)
 if not gate:
  for filename in ['runtime_freeze','dataset_manifest','repair_methods_manifest','evaluation_results','mechanism_audit','cost_analysis','regression_analysis']:
   atomic_json(ROOT/(filename+'.json'),dict(status='NOT_RUN_STAGE_A_GATE_FAILED',not_zero_effect=True))
  costs={a:read_json(ROOT/('stage_A_'+a)/'usage.json') for a in ['baseline','reflection']};known=sum(v['known_token_estimate_cny'] for v in costs.values());reserved=sum(v['reserved_or_reported_cost_cny'] for v in costs.values());unknown=sum(v['unknown_usage_requests'] for v in costs.values())
  atomic_json(ROOT/'accounting.json',dict(by_arm=costs,known_token_estimate_cny=known,conservative_ledger_cny=reserved,unknown_usage_requests=unknown,actual_invoice_cny=None,total_ceiling_cny=10))
  report=text+'\n# 研究结论\n\n1. Reflection接口干扰尚未达到预注册控制标准，停止Stage B。\n2. 未测试新的M2跨输入空间。\n3–5. 未比较Prompt、Checker、专项组件，不能判断其有效或无价值。\n6. 未运行隐藏集，不能判断负迁移。\n7. 仅Stage A费用已发生；估算与实际账单分离，见accounting.json。\n8. 暂不进入失败驱动算子生成，应先独立改进Reflection接口。\n\n未生成研究算子，未打开隐藏题，未改变历史实验。工程校准失败不能当作数学能力不足。\n'
  (ROOT/'experiment_b16_report.md').write_text(report);atomic_json(ROOT/'completion.json',dict(status='complete_stage_A_gate_failed',stage_B_not_run=True))
 print(json.dumps(dict(gate_passed=gate,stats=stats),ensure_ascii=False))
 return gate

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','stage-A','analyze-A']);p.add_argument('--allow-api',action='store_true');a=p.parse_args()
 if a.command=='prepare':prepare()
 elif a.command=='analyze-A':analyze_A()
 else:
  if not a.allow_api:p.error('--allow-api required')
  asyncio.run(stage_A())
