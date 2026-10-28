"""Frozen S1 engineering smoke. No retries, no later-stage execution."""
import asyncio, hashlib, json, time, sys
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
from scripts.d0_s01_runtime.evaluation import EngineeringSession, evaluate_task
OLD=Path('results/experiment_d0_s01_20261019_v1')
OUT=Path('results/experiment_d0_s1_20261019_v1')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str))
def load(p): return json.loads(Path(p).read_text())
TOOLS=[{'type':'function','function':{'name':name,'description':desc,'parameters':{'type':'object','properties':props,'required':list(props),'additionalProperties':False}}} for name,desc,props in [
 ('self_test','Run complete Python source on a public or self-created input. Returns execution output only, not correctness.',{'code':{'type':'string'},'test_input':{'type':'object','properties':{'stdin':{'type':'string'},'args':{'type':'array','items':{}},'kwargs':{'type':'object'}},'additionalProperties':False}}),
 ('submit','Freeze complete final Python source. No further continuation or changes.',{'code':{'type':'string'}})]]
SYSTEM='''Solve the supplied programming task using complete Python source and the stated interface. Use self_test on a public example or your own input first, examine its output, then submit complete final source. No hidden-test tool is available. No file or network access, eval/exec, private attributes or subprocess; use normal Python and safe standard libraries. Available modules: sys,json,math,collections,itertools,functools,heapq,bisect,string,re,statistics,fractions,decimal,operator,array,typing,copy. At most 4 model calls, total 8192 output tokens and 20000 input tokens, 10 seconds of self tests, 180 seconds online. Prefer submitting promptly after a useful self test; avoid long explanations. The last available call will require submit. Tool execution success does not certify correctness.'''
def input_bound(messages): return len(json.dumps({'messages':messages,'tools':TOOLS},ensure_ascii=False).encode())+2048
async def one(tid,client,ledger,public,backend):
 d=OUT/'tasks'/tid;d.mkdir(parents=True,exist_ok=True)
 session=EngineeringSession({'interface':public[tid]['interface']},d/'execution')
 messages=[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(public[tid],ensure_ascii=False)}]
 row={'id':tid,'source':public[tid]['source'],'split':'ENGINEERING_SMOKE','status':'RUNNING','calls':0,'input_tokens':0,'output_tokens':0,'self_tests':0,'cost_upper_cny':0,'versions':[]}
 start=time.monotonic(); first=None
 for k in range(4):
  remaining=180-(time.monotonic()-start);ib=input_bound(messages);ob=8192-row['output_tokens']
  final=k==3 or ob<=4096 or (k>0 and ib*2>20000-row['input_tokens'])
  cap=min(ob,4096 if final else max(0,ob-2048),4096)
  reserve=(ib*2+cap*8)/1e6
  if remaining<=0 or cap<256 or row['input_tokens']+ib>20000 or ledger['committed_upper_cny']+reserve>2:
   row['status']='BUDGET_STOP';break
  name='submit' if final else ('self_test' if k==0 else None)
  request={'model':'deepseek-v4-flash','messages':messages,'tools':TOOLS,'tool_choice':{'type':'function','function':{'name':name}} if name else 'required','parallel_tool_calls':False,'temperature':0,'max_tokens':cap,'extra_body':{'thinking':{'type':'disabled'}}}
  call={'task':tid,'index':k,'status':'IN_FLIGHT','utc':datetime.now(timezone.utc).isoformat(),'reserved_cny':reserve,'input_upper_tokens':ib,'request':request}
  ledger['committed_upper_cny']+=reserve;ledger['calls'].append(call);save(OUT/'ledger.json',ledger);save(d/f'request_{k}.json',call)
  ts=time.monotonic()
  try: response=await client.chat.completions.create(**request,timeout=remaining)
  except Exception as e:
   call.update(status='UNKNOWN_DELIVERY',exception_type=type(e).__name__,seconds=time.monotonic()-ts);row['status']='API_INFRASTRUCTURE_ERROR';save(OUT/'ledger.json',ledger);save(d/f'request_{k}.json',call);break
  raw=response.model_dump();save(d/f'response_{k}.json',raw)
  u=response.usage
  if u is None:
   call.update(status='UNKNOWN_USAGE');row['status']='ACCOUNTING_ERROR';save(OUT/'ledger.json',ledger);break
  charge=(u.prompt_tokens*2+u.completion_tokens*8)/1e6
  ledger['committed_upper_cny']+=charge-reserve;call.update(status='COMPLETED',usage=u.model_dump(),cost_upper_cny=charge,seconds=time.monotonic()-ts,returned_model=response.model);save(OUT/'ledger.json',ledger)
  row['calls']+=1;row['input_tokens']+=u.prompt_tokens;row['output_tokens']+=u.completion_tokens;row['cost_upper_cny']+=charge
  if u.prompt_tokens>ib or u.completion_tokens>cap:
   row['status']='ACCOUNTING_BOUND_ERROR';break
  m=response.choices[0].message
  messages.append({'role':'assistant','content':m.content or '',**({'tool_calls':[{'id':t.id,'type':'function','function':{'name':t.function.name,'arguments':t.function.arguments}} for t in m.tool_calls]} if m.tool_calls else {})})
  if not m.tool_calls:
   row['status']='MODEL_FORMAT_ERROR';break
  if len(m.tool_calls)!=1:
   row['status']='MODEL_TOOL_PROTOCOL_ERROR';break
  t=m.tool_calls[0]
  try:
   args=json.loads(t.function.arguments);code=args['code'];assert isinstance(code,str) and code.strip()
   if t.function.name not in ['self_test','submit']:raise ValueError('tool')
  except Exception:
   feedback={'error':'Invalid tool arguments; provide complete code and valid JSON.'}
  else:
   f=d/f'program_{k}.py';f.write_text(code);row['versions'].append({'call':k,'sha256':sha(f),'tool':t.function.name})
   if first is None:first=code
   if t.function.name=='submit':
    session.submit(code);(d/'final.py').write_text(code);row['status']='SUBMITTED';break
   if final:
    feedback={'error':'Final call required submit; no budget remains.'}
   else:
    feedback=await session.self_test(code,args.get('test_input',{}));row['self_tests']+=1;save(d/f'self_test_{k}.json',feedback)
  displayed=json.dumps(feedback,ensure_ascii=False)
  if len(displayed)>5000:displayed=json.dumps({'truncated':True,'output_prefix':displayed[:4800]})
  messages.append({'role':'tool','tool_call_id':t.id,'content':displayed})
  save(d/'conversation.json',messages)
 row['online_seconds']=time.monotonic()-start;row['tool_seconds']=session.tool_seconds
 save(d/'conversation.json',messages)
 if row['status']=='RUNNING':row['status']='NO_FINAL_SUBMISSION'
 # Close the online session before researcher-only scoring.
 if row['status']=='SUBMITTED':
  session.phase='CLOSED';r=await evaluate_task(session.source,backend[tid],log_dir=d/'execution',task_wall=75);save(d/'hidden_final.json',r);row['verdict']=r['verdict'];row['hidden_seconds']=r['elapsed_seconds']
  if first==session.source:row['first_verdict']=r['verdict']
  elif first is not None:
   r0=await evaluate_task(first,backend[tid],role='offline_first',log_dir=d/'execution',task_wall=75);save(d/'hidden_first.json',r0);row['first_verdict']=r0['verdict']
 save(d/'result.json',row);return row
async def main():
 if (OUT/'ledger.json').exists():raise RuntimeError('Run already started; no resumption/retry permitted')
 from scripts.async_llm import LLMsConfig
 from openai import AsyncOpenAI
 cfg=LLMsConfig.default().get('deepseek-v4-flash');assert urlsplit(cfg.base_url).hostname=='api.deepseek.com'
 public=load(OLD/'s1_public_tasks.json');backend=load(OLD/'s1_backend_tasks.json');order=load(OLD/'s1_proposed_order.json')['order']
 assert len(order)==13 and set(order)==set(public)
 protocol={'run_id':OUT.name,'authorization':'用户附件最终明确要求当前首先执行S1工程冒烟，完成报告并停止','cap_cny':2,'stage':'S1_ONLY','order':order,'model_alias':cfg.model,'sdk_retries':0,'budget':{'calls':4,'output':8192,'input':20000,'self_test_seconds':10,'online_seconds':180,'hidden_seconds':75},'pricing':'Peak no-cache upper ledger: input 2/output 8 CNY per million; provider invoice pending','pricing_url':'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/','input_reservation':'UTF8 JSON bytes + 2048; stop if reported usage exceeds bound','engineering_gate':'All first9 submitted; at least one self test each; no infrastructure/judge/accounting failure; correctness is not a gate','first_call':'Forced public self_test for engineering integration, not natural research trajectory','later_stages':'NOT_AUTHORIZED_NOT_RUN','driver_sha256':sha(__file__),'frozen_utc':datetime.now(timezone.utc).isoformat()}
 protected={str(p):sha(p) for p in OLD.rglob('*') if p.is_file()}
 for p in Path('scripts/d0_s01_runtime').glob('*.py'):protected[str(p)]=sha(p)
 save(OUT/'protected_before.json',protected);save(OUT/'protocol.json',protocol)
 ledger={'cap_cny':2,'committed_upper_cny':0,'calls':[]};save(OUT/'ledger.json',ledger)
 rows=[{'id':tid,'status':'NOT_RUN','split':'ENGINEERING_SMOKE'} for tid in order];save(OUT/'results.json',rows)
 client=AsyncOpenAI(api_key=cfg.key,base_url=cfg.base_url,max_retries=0)
 for i,tid in enumerate(order):
  if i==9:
   ok=all(x.get('status')=='SUBMITTED' and x.get('self_tests',0)>0 and x.get('verdict') not in ['INFRASTRUCTURE_ERROR','JUDGE_ERROR','EVALUATOR_BUDGET_EXHAUSTED'] for x in rows[:9]) and ledger['committed_upper_cny']+4*.105536<=2
   save(OUT/'first9_gate.json',{'passed':ok,'accuracy_used':False,'remaining_cny':2-ledger['committed_upper_cny']})
   if not ok:break
  rows[i]=await one(tid,client,ledger,public,backend);save(OUT/'results.json',rows)
  print(json.dumps({k:rows[i].get(k) for k in ['id','status','verdict','calls','cost_upper_cny'] }),flush=True)
  if rows[i]['status'] in ['API_INFRASTRUCTURE_ERROR','ACCOUNTING_ERROR','ACCOUNTING_BOUND_ERROR']:break
 await client.close()
 save(OUT/'protected_after.json',{'checked':len(protected),'changed':[p for p,h in protected.items() if not Path(p).exists() or sha(p)!=h]})
if __name__=='__main__':asyncio.run(main())
