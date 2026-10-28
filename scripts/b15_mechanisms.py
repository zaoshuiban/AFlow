"""Predefined paired mechanism reverification; no operator generation."""
import asyncio,hashlib,json,random,copy
from pathlib import Path
from scripts.experiment_b15 import ROOT,snapshot
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.compositional_counting import make_instance,exact_dp,brute_force
from scripts.operator_runtime import Transport,solve
from scripts.python_tool_interface_v2 import InterfaceTransport,executor,CONTRACT
from scripts.experiment_b1 import SEMANTICS,load_codes
from scripts.b1_detection import detect

HYPOTHESES={
'M1':dict(mechanism_id='M1',observed_defect='A necessary first-symbol value is absent from the effective DP state, producing an incorrect endpoint-dependent count.',causal_hypothesis='States with different first symbols are conflated; terminal endpoint decision lacks the needed information.',trigger_conditions=['endpoints constraint active'],control_conditions=['same task with endpoints constraint removed'],predicted_failure='Incorrect endpoint count when the missing state matters; no universal failure prediction.',falsification_conditions=['Equivalent first information is encoded elsewhere','A first-state-only repair has no relevant effect','Defect requires independent count/state repairs: COMPOUND_DEFECT, not strict M1'],minimal_repair='Add or propagate first symbol through effective state and terminal comparison only.',source_evidence=['B1 strict occurrence ba6eb2700b1bdd340c0e; historical source evidence preserved in B1']),
'M2':dict(mechanism_id='M2',observed_defect='Pattern matcher compares integer sequence elements against character pattern elements.',causal_hypothesis='Mismatched element types suppress or corrupt otherwise intended pattern matching.',trigger_conditions=['forbidden or pattern_count active'],control_conditions=['same task with target pattern matching constraint removed'],predicted_failure='Incorrect count when suppressed matches change admissibility.',falsification_conditions=['Representations actually agree','Pattern error arises from overlap/boundary logic only','Type-only conversion does not causally affect target defect'],minimal_repair='Convert only compared pattern/state element representation; no counting or automaton redesign.',source_evidence=['B1 posthoc 29fd9652186ed13194b9 and 793693faf1ca8376aa68; not independent M2 confirmation'])}

def prepare():
 assert read_json(ROOT/'interface_calibration.json')['gate_passed'],'Stage A gate'
 if (ROOT/'mechanism_plan.json').exists():raise FileExistsError('Mechanism design already frozen')
 prot=read_json(ROOT/'protocol.json')
 freeze=dict(interface_version='python-tool-interface-v2',runtime_version='operator-runtime-v1',adapter='identity',sources={f:file_hash(Path(f)) for f in ['scripts/python_tool_interface_v2.py']+[str(p) for p in sorted(Path('scripts/operator_runtime').glob('*.py'))]},contract=CONTRACT,tool_schema_transform='Only python.description replaced by exact CONTRACT; argument schema and submit unchanged',executor='operator-runtime-v1 execute + Resources(cpu_seconds=5)',sandbox='unchanged sandbox_profile; no user repo/home/network/write/fork',budget=dict(calls=4,output_tokens=8192,tool_wall_seconds=10,total_seconds=180,cpu_seconds=5,memory_bytes=268435456),stage_A=prot)
 atomic_json(ROOT/'interface_freeze.json',freeze);atomic_json(ROOT/'mechanism_hypotheses.json',HYPOTHESES)
 # Only identities are used for exclusions; outcomes are not consulted.
 used=set()
 for p in Path('results').rglob('*.json'):
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
 rng=random.Random(2610151511);diag=[];confirm=[];cross=[]
 def pair(mid,index,stage):
  while True:
   n=rng.choice([7,8,9,11,12,14,16]);kinds=['count']
   if mid=='M1':
    if index%3!=1:kinds+=['adjacent']
    if index%3==1:kinds+=['pattern_count']
    if index%4==3:kinds+=['mod']
    kinds+=['endpoints'];target='endpoints'
   else:
    target='pattern_count' if index%2==0 else 'forbidden';kinds+=[target]
    if index%3==0:kinds+=['adjacent']
    if index%3==1:kinds+=['endpoints']
    if index%4==3:kinds+=['position']
   p=make_instance(rng,kinds,n);q=copy.deepcopy(p);q['constraints']=[c for c in q['constraints'] if c['kind']!=target]
   ss=[json.dumps(x,sort_keys=True) for x in [p,q]]
   if all(s not in used for s in ss):break
  out=[]
  for variant,x,s in zip(['trigger','control'],[p,q],ss):
   used.add(s);ans=exact_dp(x)['answer']
   small=copy.deepcopy(x);small['n']=7
   for c in small['constraints']:
    if c['kind']=='count':c['exact']=min(c['exact'],7)
    if c['kind']=='pattern_count':c['exact']=min(c['exact'],max(0,8-len(c['pattern'])))
   a=exact_dp(small)['answer'];b=brute_force(small);assert a==b
   tid=hashlib.sha256(s.encode()).hexdigest()[:20];cross.append(dict(task_id=tid,small_problem=small,dp=a,brute=b))
   out.append(dict(id=tid,problem=s,expected=ans,mechanism=mid,pair=index,variant=variant,stage=stage,tier=mid,scale=str(n)))
  return out
 for mid in HYPOTHESES:
  for i in range(10):diag+=pair(mid,i,'diagnostic')
  for i in range(3):confirm+=pair(mid,i+100,'confirmation')
 atomic_json(ROOT/'mechanism_inputs/diagnostics.json',diag);atomic_json(ROOT/'sealed/confirmation.json',confirm);atomic_json(ROOT/'reference_crosschecks.json',cross)
 plan=dict(seed=2610151511,repeat_pairs=[0,4],extra_repetitions=2,diagnostic_tasks=40,confirmation_tasks=12,trials=136,agent_input_fields=['problem'],no_mechanism_hints=True,confirmation_rule='3 trigger/control pairs per mechanism; 6 tasks each, all included',source_audit_process=dict(candidate='All executed wrong full-task returns plus explicit runtime/type evidence; deterministic state/comparison syntax screens are candidates only',verification='Independent source review and minimal first-state or type-only repair; compare original/repair/oracle on trigger and matched control, small brute check when applicable',compound='Separate one-defect and joint repairs; do not credit compound to strict M1/M2',unknown='Retain UNKNOWN when no minimal causal evidence',distinct_input='Count canonical task IDs once; repeats reported separately',confirmation='Freeze source audit records and process before opening; apply same criteria without rule repair'),hashes={str(p):file_hash(p) for p in [ROOT/'mechanism_inputs/diagnostics.json',ROOT/'sealed/confirmation.json',ROOT/'mechanism_hypotheses.json',Path(__file__),Path('scripts/python_tool_interface_v2.py')]})
 atomic_json(ROOT/'mechanism_plan.json',plan)
 for arm in ['baseline','reflection']:
  d=ROOT/('agent_'+arm);d.mkdir(exist_ok=True)
  atomic_json(d/'manifest.json',dict(runtime_version='operator-runtime-v1',experiment='B1.5 mechanisms '+arm,model_alias='deepseek-v4-flash',max_requests=816,api_budget_cny=3.5,retry_policy=dict(max_retries=2,base_delay=1,max_delay=60),extra_limits=dict(max_input_tokens=1500000,max_output_tokens=500000,max_wall_seconds=180)))
 print('Frozen interface, hypotheses, 40 diagnostic and 12 sealed confirmation tasks.',flush=True)

async def run(stage):
 plan=read_json(ROOT/'mechanism_plan.json')
 for p,h in plan['hashes'].items():assert file_hash(Path(p))==h,p
 for p,h in read_json(ROOT/'interface_freeze.json')['sources'].items():assert file_hash(Path(p))==h,p
 assert snapshot()==read_json(ROOT/'history_before.json')
 tasks=read_json(ROOT/'mechanism_inputs/diagnostics.json')
 if stage=='diagnostic':schedule=[dict(task=t,rep=0) for t in tasks]+[dict(task=t,rep=r) for t in tasks if t['pair'] in plan['repeat_pairs'] for r in (1,2)]
 else:
  assert (ROOT/'source_audit_freeze.json').exists(),'Audit process/diagnostic review must be fixed first'
  schedule=[dict(task=t,rep=0) for t in read_json(ROOT/'sealed/confirmation.json')]
 transports={a:Transport(ROOT/('agent_'+a)) for a in ['baseline','reflection']};work=[(r,a) for r in schedule for a in transports];random.Random(2610151512+(stage=='confirmation')).shuffle(work)
 atomic_json(ROOT/(stage+'_schedule.json'),[dict(task_id=r['task']['id'],rep=r['rep'],arm=a) for r,a in work]);sem=asyncio.Semaphore(3)
 async def one(item,arm):
  async with sem:
   t=item['task'];row=dict(t,id=t['id']+f"_r{item['rep']}");root=ROOT/('agent_'+arm);dest=root/'observations'/f"{row['id']}_{arm}.json"
   if dest.exists():return
   obs=await solve(root,InterfaceTransport(transports[arm]),row,arm,SEMANTICS,executor=executor)
   codes=load_codes(root,obs);ev=detect(row,obs,source='B15_'+stage,codes=codes)
   atomic_json(ROOT/'mechanism_events'/f"{row['id']}_{arm}.json",dict(task=t,repeat=item['rep'],arm=arm,stage=stage,observation=obs,events=ev))
   print(json.dumps(dict(stage=stage,arm=arm,done=len(list((ROOT/'mechanism_events').glob('*.json'))),total=136)),flush=True)
 try:await asyncio.gather(*(one(r,a) for r,a in work))
 finally:
  for a,t in transports.items():atomic_json(ROOT/('agent_'+a)/'usage.json',t.usage());await t.client.close()

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','diagnostic','confirmation']);p.add_argument('--allow-api',action='store_true');a=p.parse_args()
 if a.command=='prepare':prepare()
 else:
  if not a.allow_api:p.error('--allow-api required')
  asyncio.run(run(a.command))
