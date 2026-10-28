"""Frozen B1 program interventions and stable Agent diagnosis, never synthesis."""
import argparse,asyncio,copy,json,random,time
from pathlib import Path
from scripts.experiment_b1 import ROOT,SEMANTICS,verify,load_codes,counts
from scripts.b1_detection import detect
from scripts.offline_loop_protocol import atomic_json,read_json,file_hash,run_lock
from scripts.operator_runtime import Transport,solve,execute,Resources
from scripts.compositional_counting import exact_dp,brute_force

async def bounded_execute(code,problem,wall,**kwargs):
 return await execute(code,problem,wall,resources=Resources(cpu_seconds=5),**kwargs)


def verify_plan():
 verify();p=read_json(ROOT/'diagnosis_plan.json')
 for f,h in p['source_hashes'].items():assert file_hash(Path(f))==h,f
 for f,h in p['causal_program_hashes'].items():assert file_hash(Path(f))==h,f
 assert file_hash(ROOT/'inputs/diagnostics.json')==p['diagnostic_sha256'];assert file_hash(ROOT/'sealed/confirmation.json')==p['confirmation_sha256']
 return p


def freeze():
 verify_plan()
 files=['scripts/run_experiment_b1.py','scripts/b1_detection.py','scripts/b1_prepare_diagnosis.py','tests/test_experiment_b1.py']
 atomic_json(ROOT/'execution_freeze.json',dict(sources={p:file_hash(Path(p)) for p in files},protocol=read_json(ROOT/'diagnosis_plan.json'),before_new_agent_api=True,initial_detector_tests_passed=7))
 for arm in ('baseline','reflection'):
  d=ROOT/('agent_'+arm);d.mkdir(exist_ok=True)
  atomic_json(d/'manifest.json',dict(runtime_version='operator-runtime-v1',experiment='B1 targeted diagnosis '+arm,model_alias='deepseek-v4-flash',max_requests=432,api_budget_cny=4,retry_policy=dict(max_retries=2,base_delay=1,max_delay=60),extra_limits=dict(max_input_tokens=1000000,max_output_tokens=200000,max_wall_seconds=180),thinking='disabled',temperature=0,trial_calls=4,trial_output_tokens=8192,trial_tool_wall=10,trial_cpu_seconds=5,trial_wall=180))
 print({'frozen':True,'trials':72,'api_reservation_ceiling_cny':8},flush=True)


async def programs():
 if (ROOT/'program_diagnosis.json').exists():return read_json(ROOT/'program_diagnosis.json')
 tasks=read_json(ROOT/'inputs/diagnostics.json');source=read_json(ROOT/'inputs/natural_discovery.json');by={t['id']:t for t in source};rows=[]
 for r in tasks+[by['ed6d58a5f459a1d86a68']]:
  result={}
  for variant in ('original','repaired'):
   code=(ROOT/f'causal_programs/M1_{variant}.py').read_text();out=await bounded_execute(code,r['problem'],6)
   result[variant]=dict(execution=out,correct=out['status']=='success' and out['value']==r['expected'])
  rows.append(dict(id=r['id'],role=r.get('variant','discovery'),pair=r.get('pair'),expected=r['expected'],**result))
 # Other candidates only causal Discovery audit, not independent paired/Agent evidence.
 audits=[]
 for mid,tid,suffix in [('M2','1d2ad48261e4ba4cb28b','_compat'),('M3','e4d3bffc7350d308b0da','')]:
  t=by[tid]
  for n in (json.loads(t['problem'])['n'],7):
   p=json.loads(t['problem']);p['n']=n
   for c in p['constraints']:
    if c['kind']=='count':c['exact']=min(c['exact'],n)
   expected=exact_dp(p)['answer']
   if n==7:assert expected==brute_force(p)
   case=dict(mechanism=mid,n=n,role='Discovery causal audit, not hidden confirmation',expected=expected)
   for v in ('original','repaired'):
    out=await bounded_execute((ROOT/f'causal_programs/{mid}_{v}{suffix}.py').read_text(),json.dumps(p),6);case[v]=dict(execution=out,correct=out['status']=='success' and out['value']==expected)
   audits.append(case)
 result=dict(M1=rows,other_discovery_audits=audits,all_interventions_assisted=True,no_generated_operator=True)
 atomic_json(ROOT/'program_diagnosis.json',result)
 if not all(r['repaired']['correct'] for r in rows):raise RuntimeError('M1 intervention unsupported; stop before Agent diagnosis')
 return result


async def run():
 plan=verify_plan();f=read_json(ROOT/'execution_freeze.json')
 for p,h in f['sources'].items():assert file_hash(Path(p))==h,p
 await programs()
 tasks=read_json(ROOT/'inputs/diagnostics.json')
 # All72 trial identities fixed before any new Agent output, including repeats and confirmation.
 schedule=[dict(task=t,rep=0,stage='diagnostic') for t in tasks]
 schedule += [dict(task=t,rep=rep,stage='repeat') for rep in (1,2) for t in tasks if t['pair'] in plan['repeat_pairs']]
 # Confirmation opened only after prediction/source freeze. No outcome-dependent selection.
 schedule += [dict(task=t,rep=0,stage='confirmation') for t in read_json(ROOT/'sealed/confirmation.json')]
 atomic_json(ROOT/'agent_schedule.json',dict(schedule=[dict(task_id=r['task']['id'],rep=r['rep'],stage=r['stage']) for r in schedule],n_trials=72,selection='pre-frozen, all included, both arms on every case'))
 transports={arm:Transport(ROOT/('agent_'+arm)) for arm in ('baseline','reflection')};sem=asyncio.Semaphore(3);work=[(r,arm) for r in schedule for arm in transports];random.Random(2610154103).shuffle(work)
 async def one(item,arm):
  async with sem:
   task=item['task'];row=dict(task);row['id']=task['id']+f"_r{item['rep']}";root=ROOT/('agent_'+arm);path=root/'observations'/f"{row['id']}_{arm}.json"
   if path.exists():return
   obs=await solve(root,transports[arm],row,arm,SEMANTICS,executor=bounded_execute)
   codes=load_codes(root,obs);ev=detect({**row,'expected':task['expected']},obs,source='B1_'+item['stage'],codes=codes)
   for e in ev:e.update(base_task_id=task['id'],repeat=item['rep'],arm=arm,study_stage=item['stage'])
   # This is post-execution detector output; never sent to the Agent or used to repair its code.
   atomic_json(ROOT/'detected_new'/f"{row['id']}_{arm}.json",dict(base_task=task,stage=item['stage'],repeat=item['rep'],arm=arm,observation_path=str(path),events=ev))
   done=len(list((ROOT/'detected_new').glob('*.json')));atomic_json(ROOT/'progress.json',dict(completed=done,total=72));print(dict(completed=done,total=72,arm=arm,stage=item['stage']),flush=True)
 try:
  await asyncio.gather(*(one(item,arm) for item,arm in work));atomic_json(ROOT/'state.json',dict(status='complete',new_agent_trials=72,operators_generated=0))
 finally:
  for arm,t in transports.items():atomic_json(ROOT/('agent_'+arm)/'usage.json',t.usage());await t.client.close()


def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['freeze','run']);p.add_argument('--allow-api',action='store_true');a=p.parse_args()
 if a.command=='freeze':freeze()
 else:
  if not a.allow_api:p.error('--allow-api required')
  with run_lock(ROOT):asyncio.run(run())

if __name__=='__main__':main()
