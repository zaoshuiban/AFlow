"""B1.5 interface calibration, independently frozen and budget bounded."""
import argparse,asyncio,hashlib,json,random
from pathlib import Path
from scripts.offline_loop_protocol import atomic_json,read_json,file_hash
from scripts.operator_runtime import Transport,solve
from scripts.python_tool_interface_v2 import InterfaceTransport,executor,CONTRACT
from scripts.experiment_b1 import SEMANTICS
from scripts.compositional_counting import make_instance,exact_dp,brute_force,KINDS
ROOT=Path('results/experiment_b15_20261015_v1')
PROTECTED=['results/generated_operator_experiment_a_v1','results/experiment_b0_20261014_v1','results/experiment_b1_20261015_v1','scripts/operator_runtime']

def snapshot():
 return {str(p):file_hash(p) for d in PROTECTED for p in Path(d).rglob('*') if p.is_file() and '__pycache__' not in str(p)}

def prepare():
 if (ROOT/'protocol.json').exists():raise FileExistsError('Already frozen')
 old=set()
 # Task identities compared by canonical problem, across all existing JSON artifacts.
 for p in Path('results').rglob('*.json'):
  if ROOT in p.parents:continue
  try:v=read_json(p)
  except Exception:continue
  def walk(v):
   if isinstance(v,dict):
    if isinstance(v.get('problem'),str):
     try:old.add(json.dumps(json.loads(v['problem']),sort_keys=True))
     except Exception:pass
    for x in v.values():walk(x)
   elif isinstance(v,list):
    for x in v:walk(x)
  walk(v)
 rng=random.Random(2610151501);rows=[]
 for i in range(20):
  kinds=rng.sample(KINDS,1+i%5);n=[6,7,10,14][i%4]
  while True:
   p=make_instance(rng,kinds,n);s=json.dumps(p,sort_keys=True)
   if s not in old:break
   kinds=rng.sample(KINDS,1+i%5);n=rng.choice([6,7,10,14,15,16])
  old.add(s);expected=exact_dp(p)['answer']
  if n<=7:assert expected==brute_force(p)
  rows.append(dict(id=hashlib.sha256(s.encode()).hexdigest()[:20],problem=s,expected=expected,tier='interface_development',scale=str(n),generation_index=i))
 atomic_json(ROOT/'history_before.json',snapshot());atomic_json(ROOT/'development.json',rows)
 sources=['scripts/experiment_b15.py','scripts/python_tool_interface_v2.py','tests/test_python_tool_interface_v2.py','scripts/compositional_counting.py']
 atomic_json(ROOT/'protocol.json',dict(stage='A',seed=2610151501,interface='python-tool-interface-v2-candidate',max_interface_revisions=1,stage_A_cap_cny=3,stage_B_reserved_cny=7,total_cap_cny=10,gate=dict(first_code_acceptance=.90,tasks_with_protocol_error=.10),sources={p:file_hash(Path(p)) for p in sources},task_hash=file_hash(ROOT/'development.json'),adapter='identity',confirmation_not_generated_or_opened=True))
 for env in ('original','normalized'):
  d=ROOT/env;d.mkdir(exist_ok=True)
  atomic_json(d/'manifest.json',dict(runtime_version='operator-runtime-v1',experiment='B1.5 Stage A '+env,model_alias='deepseek-v4-flash',max_requests=240,api_budget_cny=1.5,retry_policy=dict(max_retries=2,base_delay=1,max_delay=60),extra_limits=dict(max_input_tokens=1000000,max_output_tokens=200000,max_wall_seconds=180),temperature=0,thinking='disabled'))
 print('Prepared 20 paired development tasks; API ceiling Stage A 3 CNY; B remains gated.',flush=True)

async def run():
 p=read_json(ROOT/'protocol.json')
 for f,h in p['sources'].items():assert file_hash(Path(f))==h,f
 assert file_hash(ROOT/'development.json')==p['task_hash']
 assert snapshot()==read_json(ROOT/'history_before.json')
 transports={e:Transport(ROOT/e) for e in ('original','normalized')};work=[(r,e) for r in read_json(ROOT/'development.json') for e in transports];random.Random(2610151502).shuffle(work)
 atomic_json(ROOT/'schedule.json',[dict(id=r['id'],environment=e) for r,e in work]);sem=asyncio.Semaphore(3)
 async def one(r,e):
  async with sem:
   dest=ROOT/e/'observations'/f"{r['id']}_baseline.json"
   if dest.exists():return
   t=InterfaceTransport(transports[e]) if e=='normalized' else transports[e]
   await solve(ROOT/e,t,r,'baseline',SEMANTICS,executor=executor)
   print(json.dumps(dict(environment=e,completed=len(list((ROOT/e/'observations').glob('*.json'))),total=20)),flush=True)
 try:await asyncio.gather(*(one(r,e) for r,e in work))
 finally:
  for e,t in transports.items():atomic_json(ROOT/e/'usage.json',t.usage());await t.client.close()

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('command',choices=['prepare','run']);a.add_argument('--allow-api',action='store_true');args=a.parse_args()
 if args.command=='prepare':prepare()
 else:
  if not args.allow_api:a.error('--allow-api required')
  asyncio.run(run())
