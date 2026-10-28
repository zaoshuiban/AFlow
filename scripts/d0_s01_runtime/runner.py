"""Parent-side scoring. No expected value or reference code enters sandbox payload."""
import asyncio,hashlib,json,math,os,shutil,sys,tempfile,time
from pathlib import Path
from scripts.operator_runtime.executor import sandbox_profile,bounded_read
VERSION='d0-programming-v2-s01'
DEFAULT_LIMITS=dict(cpu_seconds=2,memory_bytes=256*1024**2,recursion_depth=2000,output_bytes=65536)
def sha(x):return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
def admit(task):
 if task.get('interface',{}).get('mode') not in ['stdin','function','class']:return {'admitted':False,'reason':'UNSUPPORTED_INTERFACE'}
 if task.get('judge',{}).get('mode','exact') not in ['exact','tokens','float','casefold','taco_single_return']:return {'admitted':False,'reason':'UNSUPPORTED_SPECIAL_JUDGE'}
 if not task.get('cases'):return {'admitted':False,'reason':'NO_TESTS'}
 if task.get('judge',{}).get('mode')=='taco_single_return':
  if task['judge'].get('return_schema')!='int_pair' or any(type(c.get('expected')) is not list or len(c['expected'])!=1 or type(c['expected'][0]) is not list or len(c['expected'][0])!=2 or any(type(x) is not int for x in c['expected'][0]) for c in task['cases']):return {'admitted':False,'reason':'AMBIGUOUS_RETURN_WRAPPER'}
 return {'admitted':True}
def judge(actual,expected,mode='exact',atol=1e-6,rtol=1e-6,return_schema=None):
 if mode=='taco_single_return':
  return return_schema=='int_pair' and type(expected) is list and len(expected)==1 and type(actual) is list and len(actual)==2 and all(type(x) is int for x in actual) and judge(actual,expected[0])
 if mode=='tokens':return isinstance(actual,str) and isinstance(expected,str) and actual.split()==expected.split()
 if mode=='casefold':return isinstance(actual,str) and isinstance(expected,str) and actual.strip().casefold()==expected.strip().casefold()
 if mode=='float':
  try:
   aa=actual.split() if isinstance(actual,str) else actual if isinstance(actual,list) else [actual];bb=expected.split() if isinstance(expected,str) else expected if isinstance(expected,list) else [expected]
   return len(aa)==len(bb) and all(math.isfinite(float(a)) and math.isfinite(float(b)) and math.isclose(float(a),float(b),abs_tol=atol,rel_tol=rtol) for a,b in zip(aa,bb))
  except (ValueError,TypeError,OverflowError):return False
 if type(actual)!=type(expected):return False
 if isinstance(actual,list):return len(actual)==len(expected) and all(judge(a,b) for a,b in zip(actual,expected))
 if isinstance(actual,dict):return actual.keys()==expected.keys() and all(judge(actual[k],expected[k]) for k in actual)
 return actual==expected
async def execute_case(code,interface,case,judge_spec=None,*,role='hidden',wall=2,limits=None,log_dir=None):
 if role not in ['self_test','hidden','diagnostic']:raise ValueError('Explicit role required')
 start=time.monotonic();ls={**DEFAULT_LIMITS,**(limits or {})};payload={'code':code,'interface':interface,'stdin':case.get('stdin',''),'args':case.get('args',[]),'kwargs':case.get('kwargs',{}),'limits':ls}
 # Whitelist payload fields; never pass the original case dict with expected/reference.
 identity=dict(adapter=VERSION,role=role,code_sha256=hashlib.sha256(code.encode()).hexdigest(),input_sha256=sha({k:v for k,v in payload.items() if k not in ['code','limits']}),payload_fields=sorted(payload))
 result={}
 if sys.platform!='darwin' or not Path('/usr/bin/sandbox-exec').exists():result={'status':'INFRASTRUCTURE_ERROR','error':'Native isolation unavailable; fail closed'}
 else:
  with tempfile.TemporaryDirectory(prefix='d0-program-') as cwd:
   shutil.copyfile(Path(__file__).with_name('worker.py'),Path(cwd)/'worker.py')
   proc=None
   try:
    proc=await asyncio.create_subprocess_exec('/usr/bin/sandbox-exec','-p',sandbox_profile(cwd),str(Path(sys.executable).resolve()),'-I','-B',str(Path(cwd)/'worker.py'),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,cwd=cwd,env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
    proc.stdin.write(json.dumps(payload).encode());await proc.stdin.drain();proc.stdin.close()
    stdout,stderr,_=await asyncio.wait_for(asyncio.gather(bounded_read(proc.stdout,ls['output_bytes']*3),bounded_read(proc.stderr,8192),proc.wait()),wall)
    if len(stdout)>ls['output_bytes']*3:result={'status':'RESOURCE_LIMIT','error':'protocol_output_limit'}
    elif proc.returncode:
     result={'status':'TIME_LIMIT' if proc.returncode==-24 else 'INFRASTRUCTURE_ERROR' if b'sandbox_apply' in stderr else 'PROCESS_ERROR','exit_code':proc.returncode,'stderr':stderr.decode(errors='replace')[:2000]}
    else:result=json.loads(stdout)
   except asyncio.TimeoutError:result={'status':'TIME_LIMIT','error':'wall_limit'}
   except (OSError,ValueError) as e:result={'status':'RESOURCE_LIMIT' if 'output_limit' in str(e) else 'INFRASTRUCTURE_ERROR','error':str(e)[:300]}
   finally:
    if proc is not None and proc.returncode is None:proc.kill();await proc.wait()
 if result.get('status')=='EXECUTED':
  value=result['stdout'] if interface['mode']=='stdin' else result['value'];js=judge_spec or {'mode':'exact'}
  try:result['verdict']='PASS' if judge(value,case['expected'],**js) else 'WRONG_ANSWER'
  except (TypeError,ValueError,KeyError) as exc:result['verdict']='JUDGE_ERROR';result['judge_error']=str(exc)
 else:result['verdict']=result.get('status','UNKNOWN')
 result.update(identity);result['elapsed_seconds']=time.monotonic()-start
 if log_dir:
  p=Path(log_dir)/role;p.mkdir(parents=True,exist_ok=True);name=str(time.time_ns())
  (p/(name+'.json')).write_text(json.dumps({'source':code,'input_payload':payload,'result':result,'judge_spec':judge_spec,'expected':case['expected']},indent=2))
 return result

def public_task(task):
 """Explicit allowlist for model-visible task metadata, not code isolation."""
 return {k:task[k] for k in ['id','source','prompt','interface','public_examples'] if k in task}
