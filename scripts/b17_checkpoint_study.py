"""Same-checkpoint replay through the unchanged stabilized Agent runner.
Shared generation response/tool result replayed once, then only remaining calls physically sent.
"""
import argparse,asyncio,copy,json,random,time,hashlib
from pathlib import Path
from scripts.experiment_b17 import ROOT,snapshot
from scripts.experiment_b16 import manifest,prior_inputs
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.operator_runtime import Transport,solve,InfrastructureInterrupted
from scripts.python_tool_interface_v3 import InterfaceTransport,executor,CONTRACT
from scripts.experiment_a_calibration import prompt,tools_for
from scripts.compositional_counting import SPEC,make_instance,exact_dp,brute_force
from scripts.b17_checkers import check
WARNING_SUFFIX=' Tool execution SUCCESS does not prove correctness. NOT_CHECKED is unverified; follow the common protocol. Explicit failures permit generic Python fallback. No hidden reference is available.'

class ResumeTransport:
 def __init__(self,base,cp,start):self.base=InterfaceTransport(base);self.controller=base.controller;self.cp=cp;self.start=start
 async def call(self,key,messages,output_cap,**kwargs):
  step=int(key.rsplit('/',1)[1])
  if step==0:return self.cp['response']
  remaining=180-self.cp['elapsed_seconds']-(time.monotonic()-self.start)
  if remaining<=0:raise InfrastructureInterrupted('Checkpoint wall budget exhausted')
  kwargs['timeout']=min(kwargs.get('timeout',175),remaining)
  return await self.base.call(key,messages,output_cap,**kwargs)

class ResumeExecutor:
 def __init__(self,cp,arm,start):self.cp=cp;self.arm=arm;self.start=start;self.first=True;self.detection=None
 async def __call__(self,code,problem,wall,**kwargs):
  if self.first:
   self.first=False;assert code==self.cp['code'] and problem==self.cp['task']['problem'];out=copy.deepcopy(self.cp['tool_result'])
   if self.arm!='R':
    available=min(3,max(.001,wall-out['elapsed_seconds']),max(.001,180-self.cp['elapsed_seconds']-(time.monotonic()-self.start)))
    self.detection=await check(code,problem,available,'generic' if self.arm=='G' else 'mechanism')
    out['elapsed_seconds']+=self.detection['elapsed_seconds'];out['runtime_ms']=out['elapsed_seconds']*1000
    if self.detection['status']=='VIOLATION':
     # Identical factual feedback for extensionally identical contracts; do not disguise
     # a public-schema generic check as a new failure-driven mathematical capability.
     out['contract_feedback']={k:v for k,v in self.detection.items() if k not in ['checker','elapsed_seconds']}
   return out
  remaining=180-self.cp['elapsed_seconds']-(time.monotonic()-self.start)
  return await executor(code,problem,max(.001,min(wall,remaining)),**kwargs)

def prepare():
 assert read_json(ROOT/'stage_A_gate.json')['passed'];assert read_json(ROOT/'confirmation_label_validation.json')['passed']
 if (ROOT/'stage_B_protocol.json').exists():raise FileExistsError('Already frozen')
 rng=random.Random(2610151701);used=prior_inputs();tasks=[]
 for i in range(6):
  kinds=[['count','pattern_count'],['count','forbidden','adjacent'],['count','pattern_count','endpoints'],['count','mod'],['adjacent','pattern_count','position'],['count','forbidden','endpoints']][i]
  while True:
   p=make_instance(rng,kinds,[7,8,9,7,8,9][i]);s=json.dumps(p,sort_keys=True)
   if s not in used:break
  used.add(s);ans=exact_dp(p)['answer'];q=json.loads(s);q['n']=7
  for c in q['constraints']:
   if c['kind']=='count':c['exact']=min(c['exact'],7)
   if c['kind']=='pattern_count':c['exact']=min(c['exact'],max(0,8-len(c['pattern'])))
  assert exact_dp(q)['answer']==brute_force(q)
  tasks.append(dict(id=hashlib.sha256(s.encode()).hexdigest()[:20],problem=s,expected=ans,tier='fixed_checkpoint',scale=str(p['n'])))
 atomic_json(ROOT/'stage_B_tasks.json',tasks)
 protocol=dict(n=6,seed=2610151701,branch_seed=2610151702,all_inputs_preserved=True,no_failure_selection=True,scope='Conditional given the exact first generated program; not natural failure incidence.',budgets=dict(total_cny=10,common_generation=1,R=1,G=1,C=1,stage_C_reserve=6),per_trajectory=dict(model_calls=4,output_tokens=8192,tool_wall_seconds=10,wall_seconds=180),same_checkpoint=True,remaining_budget_rule='Replay shared first response and cached tool result, charge its tokens/calls/tool time; cap actual remaining wall by common elapsed plus continuation elapsed. Checker time charged to G/C tool and wall budget. No extra model turns.',stage_C_gate='At least 2 source-verified M2 recoveries gained by C over R on distinct checkpoint inputs, no harmful intervention, and at least 1 net gain over G. Otherwise do not enter C.',methods='G public-schema representation checker and C failure-based contract currently have identical observable predicates/feedback; no distinct implementation-value claim permitted.',tasks_sha256=file_hash(ROOT/'stage_B_tasks.json'),sources={f:file_hash(Path(f)) for f in ['scripts/b17_checkers.py','scripts/b17_checkpoint_study.py','scripts/python_tool_interface_v3.py','scripts/operator_runtime/runner.py','scripts/operator_runtime/api.py']})
 atomic_json(ROOT/'stage_B_protocol.json',protocol)
 for e in ['common','R','G','C']:manifest(ROOT/('stage_B_'+e),'B1.7 '+e,1)
 print('Frozen 6 common checkpoints and 18 paired continuation units, no failure-based selection.',flush=True)

async def generate():
 p=read_json(ROOT/'stage_B_protocol.json')
 for f,h in p['sources'].items():assert file_hash(Path(f))==h
 assert snapshot()==read_json(ROOT/'history_before.json')
 transport=Transport(ROOT/'stage_B_common')
 try:
  for task in read_json(ROOT/'stage_B_tasks.json'):
   dest=ROOT/'checkpoints'/(task['id']+'.json')
   if dest.exists():continue
   messages=[dict(role='system',content=prompt(SPEC,'reflection')+WARNING_SUFFIX),dict(role='user',content='Count exactly for this JSON input:\n'+task['problem'])];started=time.monotonic();cp=dict(task=task,messages_before=messages,first_output_cap=7680,configuration=p)
   try:
    response=await InterfaceTransport(transport).call(f"pilot/{task['id']}/reflection/0",messages,7680,timeout=175,tools=[t for t in tools_for('reflection') if t['function']['name']=='python'])
    tc=response['message']['tool_calls'];assert len(tc)==1 and tc[0]['function']['name']=='python';args=json.loads(tc[0]['function']['arguments']);assert set(args)=={'code'} and isinstance(args['code'],str)
    out=await executor(args['code'],task['problem'],min(6,max(.001,180-(time.monotonic()-started))))
    observed={k:v for k,v in out.items() if k!='trace'}
    if out['status']=='success':observed['review_instruction']='Independently verify your draft against all constraints; reserve final call.'
    context=messages+[dict(role='assistant',content=response['message'].get('content'),tool_calls=tc),dict(role='tool',tool_call_id=tc[0]['id'],content=json.dumps(observed))]
    cp.update(status='READY',response=response,code=args['code'],tool_result=out,context_after_tool=context,elapsed_seconds=time.monotonic()-started,remaining=dict(calls=3,output_tokens=8192-response['usage']['output_tokens'],tool_wall_seconds=10-out['elapsed_seconds'],wall_seconds=180-(time.monotonic()-started)))
   except Exception as exc:cp.update(status='INFRASTRUCTURE_OR_GENERATION_ERROR',error=type(exc).__name__,elapsed_seconds=time.monotonic()-started)
   atomic_json(dest,cp);print(dict(checkpoint=task['id'],status=cp['status']),flush=True)
 finally:atomic_json(ROOT/'stage_B_common/usage.json',transport.usage());await transport.client.close()

async def branches():
 p=read_json(ROOT/'stage_B_protocol.json')
 for f,h in p['sources'].items():assert file_hash(Path(f))==h
 transports={a:Transport(ROOT/('stage_B_'+a)) for a in ['R','G','C']};work=[(read_json(cp),a) for cp in sorted((ROOT/'checkpoints').glob('*.json')) for a in transports];random.Random(p['branch_seed']).shuffle(work)
 atomic_json(ROOT/'stage_B_schedule.json',[dict(task_id=cp['task']['id'],branch=a) for cp,a in work])
 try:
  for cp,a in work:
   task=cp['task'];dest=ROOT/'branch_results'/(task['id']+'_'+a+'.json')
   if dest.exists():continue
   if cp['status']!='READY':atomic_json(dest,dict(task_id=task['id'],branch=a,status='COMMON_CHECKPOINT_UNAVAILABLE',correct=False,common_error=cp['error']));continue
   start=time.monotonic();ex=ResumeExecutor(cp,a,start);root=ROOT/('stage_B_'+a)
   obs=await solve(root,ResumeTransport(transports[a],cp,start),task,'reflection',SPEC,executor=ex)
   atomic_json(dest,dict(task_id=task['id'],branch=a,status='COMPLETED' if not obs.get('error') else obs['error'],correct=obs['correct'],observation=obs,checker=ex.detection,checkpoint_sha256=file_hash(ROOT/'checkpoints'/(task['id']+'.json')),common_elapsed_seconds=cp['elapsed_seconds'],continuation_seconds=time.monotonic()-start,total_trajectory_wall_seconds=cp['elapsed_seconds']+time.monotonic()-start))
   print(dict(task=task['id'],branch=a,correct=obs['correct']),flush=True)
 finally:
  for a,t in transports.items():atomic_json(ROOT/('stage_B_'+a)/'usage.json',t.usage());await t.client.close()

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','generate','branches']);p.add_argument('--allow-api',action='store_true');a=p.parse_args()
 if a.command=='prepare':prepare()
 else:
  if not a.allow_api:p.error('--allow-api required')
  asyncio.run(generate() if a.command=='generate' else branches())
