"""Gated M2 repair pilot and hidden evaluation. Hidden material sealed before methods."""
import argparse,asyncio,copy,hashlib,json,random,time
from pathlib import Path
from scripts.experiment_b16 import ROOT,verify,prior_inputs,make_rows,manifest
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.operator_runtime import Transport,solve
from scripts.python_tool_interface_v3 import InterfaceTransport,executor,CONTRACT,SCHEMA_DESCRIPTION
from scripts.compositional_counting import SPEC,make_instance,exact_dp,brute_force,validate_problem
from scripts.experiment_b1 import load_codes
from scripts.b1_detection import detect
from scripts.pattern_type_contract_checker import check
PROMPT_REMINDER='Alphabet elements are Python integers; JSON pattern strings encode digit sequences. When matching a sequence against a pattern, explicitly ensure the compared element representations have the same type.'
CHECK_NOTICE='Each Python execution also returns a lightweight static Pattern Type Contract Checker warning. PASS means no detected mismatch among resolved comparisons, not program correctness; UNKNOWN means insufficient information. You may revise your own code based on warnings. The checker never changes code or supplies an answer.'

async def checked_executor(code,problem,wall,**kwargs):
 start=time.monotonic();warning=check(code);remaining=wall-(time.monotonic()-start)
 if remaining<=0:
  from scripts.operator_runtime.executor import envelope
  r=envelope(dict(status='error',value=None,error='Contract checker exhausted tool wall budget',error_type='CODE_RESOURCE_LIMIT',elapsed_seconds=time.monotonic()-start))
 else:r=await executor(code,problem,remaining,**kwargs)
 r['contract_check']=warning;r['elapsed_seconds']=time.monotonic()-start;r['runtime_ms']=r['elapsed_seconds']*1000
 return r

def prepare():
 p=verify();assert read_json(ROOT/'stage_A_statistics.json')['gate_passed']
 if (ROOT/'dataset_manifest.json').exists():raise FileExistsError('Data already sealed')
 deps=['scripts/python_tool_interface_v3.py','scripts/python_tool_interface_v2.py']+list(p['sources'])
 atomic_json(ROOT/'runtime_freeze.json',dict(interface_version='python-tool-interface-v3',runtime_version='operator-runtime-v1',sources={f:file_hash(Path(f)) for f in set(deps)},public_contract=CONTRACT,schema_description=SCHEMA_DESCRIPTION,model=dict(alias='deepseek-v4-flash',temperature=0,thinking='disabled',sdk_retries=0),single_task_budget=p['single_task_budget'],retry_policy=dict(max_retries=2,base_delay=1,max_delay=60),sandbox='runtime-v1 sandbox_profile unchanged',adapter='identity',stage_A_gate=True))
 used=prior_inputs()|{r['problem'] for r in read_json(ROOT/'stage_A_tasks.json')};rng=random.Random(2610151611);cross=[]
 def row(p,role):
  s=json.dumps(validate_problem(p),sort_keys=True)
  if s in used:return None
  used.add(s);a=exact_dp(p)['answer'];q=copy.deepcopy(p);q['n']=min(7,p['n'])
  for c in q['constraints']:
   if c['kind']=='count':c['exact']=min(c['exact'],q['n'])
   if c['kind']=='pattern_count':c['exact']=min(c['exact'],max(0,q['n']-len(c['pattern'])+1))
  x=exact_dp(q)['answer'];y=brute_force(q);assert x==y
  tid=hashlib.sha256(s.encode()).hexdigest()[:20];cross.append(dict(id=tid,small=q,dp=x,brute=y));return dict(id=tid,problem=s,expected=a,tier=role,scale=str(p['n']))
 def target(i,role):
  while True:
   n=rng.choice([7,8,9,11,12,14]);kinds=['count','pattern_count' if i%2==0 else 'forbidden']
   if i%3==0:kinds+=['adjacent']
   if i%3==1:kinds+=['endpoints']
   if i%4==3:kinds+=['mod']
   p=make_instance(rng,kinds,n)
   for c in p['constraints']:
    if c['kind']=='pattern_count':c['pattern']=['0','01','010','0123'][i//2%4];c['exact']=rng.randrange(min(3,n-len(c['pattern'])+1)+1)
    if c['kind']=='forbidden':c['patterns']=[['0'],['01','121'],['010','101'],['0123','333']][i//2%4]
   r=row(p,role)
   if r:return r
 dev=[target(i,'development') for i in range(8)];transfer=[target(i,'transfer') for i in range(8)];control=[]
 for t in transfer:
  p=json.loads(t['problem']);p['constraints']=[c for c in p['constraints'] if c['kind'] not in ['pattern_count','forbidden']]
  r=row(p,'control')
  while r is None:
   # Input-only deduplication; do not inspect reference or Agent outcomes.
   p['n']=rng.choice([7,8,9,11,12,14,15,16]);r=row(p,'control')
  control.append(r)
 natural,naturalchecks=make_rows(rng,12,used,'natural');cross+=naturalchecks
 atomic_json(ROOT/'development.json',dev)
 hidden=transfer+control+natural
 atomic_json(ROOT/'sealed/hidden_problems.json',[{k:v for k,v in r.items() if k!='expected'} for r in hidden]);atomic_json(ROOT/'sealed/reference_answers.json',{r['id']:r['expected'] for r in hidden});atomic_json(ROOT/'reference_crosschecks.json',cross)
 atomic_json(ROOT/'dataset_manifest.json',dict(seed=2610151611,development=8,transfer=8,control=8,natural=12,hidden_total=28,selection='No output or correctness filtering; public generator, target pattern structure fixed by index. Natural uses same fixed count/length family as B1.6 Stage A, separate seed stream.',hidden_answers_access='Written only by reference computation; repair-method implementation has not opened hidden problem/answer files. Runner joins answers after method freeze; never sends to Agent.',sources={f:file_hash(Path(f)) for f in ['scripts/compositional_counting.py','scripts/b16_repair_study.py']},hashes={str(f):file_hash(f) for f in [ROOT/'development.json',ROOT/'sealed/hidden_problems.json',ROOT/'sealed/reference_answers.json']},pilot_decision=dict(enter_O='At least one strict source/intervention-verified M2 in P or C on Development, OR verified M2 with mean >=0.5 additional math-repair re-encodings/task in both P and C.',stop_all='No verified M2 in any of R/P/C and all have equal development final correctness with no observable M2 repair cost; hidden remains sealed.',otherwise='Run hidden R/P/C only; do not claim untested O has no value.',selection='Only development; never hidden outcomes.')))
 for method in ['R','P','C']:manifest(ROOT/('pilot_'+method),'M2 development '+method,.6)
 atomic_json(ROOT/'repair_methods_manifest.json',dict(status='pilot_pre_registered',R='Unmodified Reflection under common v3 protocol',P=dict(reminder=PROMPT_REMINDER,source_change=False),C=dict(notice=CHECK_NOTICE,source=file_hash(Path('scripts/pattern_type_contract_checker.py')),behavior='Run checker then unchanged source in existing native worker; both times charged to same tool budget',output_contract='PASS/WARNING/UNKNOWN; no guarantee of correctness'),O='Deferred until development decision; not automatic synthesis',source_hashes={f:file_hash(Path(f)) for f in ['scripts/pattern_type_contract_checker.py','scripts/b16_repair_study.py','tests/test_pattern_type_contract_checker.py']},component_cost='Research-assistant implementation and validation, unpriced labor/assistant cost UNKNOWN, never treated as zero; direct API construction cost zero unless explicitly used.'))
 print('8 development tasks and 28 hidden tasks sealed; R/P/C frozen for pilot.',flush=True)

def verify_methods():
 verify()
 for filename,key in [('runtime_freeze.json','sources'),('repair_methods_manifest.json','source_hashes'),('dataset_manifest.json','hashes')]:
  for f,h in read_json(ROOT/filename)[key].items():assert file_hash(Path(f))==h,f

async def run(stage):
 verify_methods()
 if stage=='pilot':tasks=read_json(ROOT/'development.json');methods=['R','P','C']
 else:
  decision=read_json(ROOT/'pilot_decision.json');assert decision['run_hidden'];methods=decision['methods']
  assert (ROOT/'methods_final_freeze.json').exists()
  for f,h in read_json(ROOT/'methods_final_freeze.json')['sources'].items():assert file_hash(Path(f))==h,f
  # Hidden is opened for evaluation only, after all participating methods are frozen.
  tasks=read_json(ROOT/'sealed/hidden_problems.json');answers=read_json(ROOT/'sealed/reference_answers.json');tasks=[dict(t,expected=answers[t['id']]) for t in tasks]
 transports={m:Transport(ROOT/(stage+'_'+m)) for m in methods};work=[(t,m) for t in tasks for m in methods];random.Random(2610151612+(stage=='hidden')).shuffle(work);sem=asyncio.Semaphore(3)
 atomic_json(ROOT/(stage+'_schedule.json'),[dict(id=t['id'],method=m) for t,m in work])
 async def one(t,m):
  async with sem:
   root=ROOT/(stage+'_'+m);dest=root/'observations'/f"{t['id']}_reflection.json"
   if dest.exists():return
   extra=PROMPT_REMINDER if m=='P' else CHECK_NOTICE if m=='C' else ''
   ex=checked_executor if m=='C' else executor
   obs=await solve(root,InterfaceTransport(transports[m],extra),t,'reflection',SPEC,executor=ex)
   ev=detect(t,obs,source='B16_'+stage,codes=load_codes(root,obs))
   atomic_json(ROOT/(stage+'_events')/f"{t['id']}_{m}.json",dict(task=t,method=m,observation=obs,events=ev))
   print(json.dumps(dict(stage=stage,method=m,completed=len(list((ROOT/(stage+'_events')).glob('*.json'))),total=len(work))),flush=True)
 try:await asyncio.gather(*(one(t,m) for t,m in work))
 finally:
  for m,t in transports.items():atomic_json(ROOT/(stage+'_'+m)/'usage.json',t.usage());await t.client.close()

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('command',choices=['prepare','pilot','hidden']);a.add_argument('--allow-api',action='store_true');args=a.parse_args()
 if args.command=='prepare':prepare()
 else:
  if not args.allow_api:a.error('--allow-api required')
  asyncio.run(run(args.command))
