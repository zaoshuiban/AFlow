"""Fail-closed OS sandbox, deterministic output envelope, legacy result keys."""
import ast,asyncio,json,os,shutil,sys,tempfile,time
from pathlib import Path
from dataclasses import dataclass,asdict
from scripts.counting_worker import validate
from .events import hash_value

@dataclass(frozen=True)
class Resources:
 cpu_seconds:int=2
 memory_bytes:int=256*1024**2
 recursion_depth:int=256
 output_bytes:int=16384
 integer_bits:int=4096
 def __post_init__(self):
  if any(type(v) is not int or v<=0 for v in self.__dict__.values()):raise ValueError('Positive integral resource limits required')


def static_check(code):
 tree=validate(code)
 if any(not isinstance(n,(ast.Import,ast.ImportFrom,ast.FunctionDef)) for n in tree.body):raise ValueError('Top-level definitions/imports only')
 solves=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='solve']
 if len(solves)!=1:raise ValueError('Exactly one solve(problem)')
 a=solves[0].args
 if len(a.args)!=1 or a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg or a.defaults or a.kw_defaults:raise ValueError('Signature solve(problem)')
 for node in ast.walk(tree):
  if isinstance(node,ast.FunctionDef) and (node.decorator_list or node.returns is not None or any(x.annotation for x in node.args.args)):raise ValueError('No executable decorators/annotations')
 return tree


def envelope(raw,operator_id='anonymous',version='unregistered'):
 success=raw.get('status')=='success';value=raw.get('value');contract=success and type(value) is int and value>=0
 if success and not contract:raw={**raw,'status':'error','error':'Invalid result type/range','error_type':'CODE_CONTRACT_ERROR','value':None};success=False
 status='SUCCESS' if success else 'DECLINED' if raw.get('status')=='declined' else 'ERROR'
 return {**raw,'operator_id':operator_id,'operator_version':version,'execution_status':status,'result':raw.get('value') if success else None,'validation_status':'NOT_CHECKED' if success else 'FAILED','warnings':['Exact result not independently checked'] if success else [raw.get('error','Execution did not produce result')], 'runtime_ms':raw.get('elapsed_seconds',0)*1000,'error_type':raw.get('error_type')}


def sandbox_profile(cwd):
 # Broad syscall allowance plus explicit filesystem deny-by-default and no network/fork.
 # Read-only interpreter/library + copied worker dirs only. Never allow repository/results/home.
 allowed={str(Path(sys.prefix).resolve()),str(Path(sys.base_prefix).resolve()),str(Path(sys.executable).resolve().parent.parent),str(Path(cwd).resolve()),'/System','/usr','/Library/Apple','/dev/null','/dev/random','/dev/urandom','/private/var/db/timezone'}
 clauses='(literal "/") (literal "/private") '+ ' '.join('(subpath '+json.dumps(p)+')' for p in sorted(allowed))
 return '(version 1)(allow default)(deny file-read-data (require-not (require-any '+clauses+')))(deny file-write*)(deny network*)(deny process-fork)' 


async def bounded_read(stream,cap):
 data=bytearray()
 while True:
  part=await stream.read(4096)
  if not part:return bytes(data)
  data.extend(part)
  if len(data)>cap:raise ValueError('output_limit')


async def execute(code,problem,wall,*,resources=None,operator_id='anonymous',version='unregistered',runtime_validator=None):
 start=time.monotonic();resources=resources or Resources();raw={};trace=dict(code_sha256=hash_value(code),problem_sha256=hash_value(problem))
 if not isinstance(problem,str) or wall<=0 or len(problem.encode())>60000:return envelope(dict(status='error',value=None,error='Invalid tool input or wall budget',error_type='TOOL_ARGUMENT_ERROR',elapsed_seconds=time.monotonic()-start),operator_id,version)
 try:static_check(code)
 except (ValueError,SyntaxError) as exc:
  kind='CODE_SYNTAX_ERROR' if isinstance(exc,SyntaxError) else 'CODE_IMPORT_ERROR' if 'Import' in str(exc) else 'CODE_CONTRACT_ERROR'
  raw=dict(status='error',value=None,error=str(exc),error_type=kind)
 if not raw:
  if sys.platform!='darwin' or not Path('/usr/bin/sandbox-exec').exists():raw=dict(status='error',value=None,error='Native isolation unavailable; no whitelist-only fallback',error_type='TOOL_INTEGRATION_ERROR')
  else:
   with tempfile.TemporaryDirectory(prefix='operator-isolated-') as cwd:
    for src in [Path(__file__).with_name('worker.py'),Path('scripts/counting_worker.py'),Path('scripts/frozen_math_worker.py')]:shutil.copyfile(src,Path(cwd)/src.name)
    cmd=['/usr/bin/sandbox-exec','-p',sandbox_profile(cwd),str(Path(sys.executable).resolve()),'-I','-B',str(Path(cwd)/'worker.py')]
    proc=None;tasks=[]
    try:
     proc=await asyncio.create_subprocess_exec(*cmd,cwd=cwd,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
     payload=json.dumps(dict(code=code,problem=problem,limits=asdict(resources))).encode()
     proc.stdin.write(payload);await proc.stdin.drain();proc.stdin.close()
     tasks=[asyncio.create_task(bounded_read(proc.stdout,32768)),asyncio.create_task(bounded_read(proc.stderr,8192)),asyncio.create_task(proc.wait())]
     out,err,rc=await asyncio.wait_for(asyncio.gather(*tasks),max(.001,wall));trace.update(exit_code=rc,stderr=err.decode(errors='replace'))
     if rc:
      kind='TOOL_INTEGRATION_ERROR' if b'sandbox_apply' in err else 'CODE_RESOURCE_LIMIT' if rc==-24 else 'UNKNOWN' if rc<0 else 'CODE_RUNTIME_ERROR'
      raw=dict(status='error',value=None,error='worker_exit_'+str(rc),error_type=kind)
     else:raw=json.loads(out)
    except asyncio.TimeoutError:raw=dict(status='error',value=None,error='worker_timeout',error_type='CODE_TIMEOUT')
    except asyncio.CancelledError:
     if proc is not None and proc.returncode is None:proc.kill()
     if proc is not None:await proc.wait()
     raise
    except Exception as exc:raw=dict(status='error',value=None,error=type(exc).__name__+': '+str(exc)[:300],error_type='CODE_RESOURCE_LIMIT' if 'output_limit' in str(exc) else 'TOOL_INTEGRATION_ERROR')
    finally:
     if proc is not None and proc.returncode is None:proc.kill();await proc.wait()
     for task in tasks:
      if not task.done():task.cancel()
     if tasks:await asyncio.gather(*tasks,return_exceptions=True)
 r=envelope({**raw,'elapsed_seconds':time.monotonic()-start,'trace':trace},operator_id,version)
 # Optional public validator; never receives row.expected. Entire time charged to tool budget.
 if r['execution_status']=='SUCCESS' and runtime_validator is not None:
  try:
   check=runtime_validator(problem,r['result'])
   if not isinstance(check,dict) or not {'verified','passed'}<=set(check) or type(check['verified']) is not bool or type(check['passed']) is not bool:raise ValueError('Explicit verification result required')
   r['validation_status']='VERIFIED' if check.get('verified') is True and check.get('passed') is True else 'FAILED' if check.get('passed') is False else 'NOT_CHECKED'
   if r['validation_status']=='FAILED':r.update(status='error',value=None,result=None,error_type='CODE_CONTRACT_ERROR');r['warnings'].append(check.get('warning','Runtime check failed'))
  except Exception:r['validation_status']='UNAVAILABLE';r['warnings'].append('Runtime verification unavailable')
 r['elapsed_seconds']=time.monotonic()-start;r['runtime_ms']=r['elapsed_seconds']*1000
 return r
