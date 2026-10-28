"""Trusted bootstrap copied into native sandbox; never imports repository/config."""
import ast,builtins,contextlib,io,json,resource,signal,sys,time
from pathlib import Path
import runpy
base=runpy.run_path(str(Path(__file__).with_name('counting_worker.py')),run_name='validation_only')

class OutputLimit(Exception):pass
class Capture(io.StringIO):
 def __init__(self,limit):super().__init__();self.limit=limit;self.used=0
 def write(self,s):
  self.used+=len(s.encode())
  if self.used>self.limit:raise OutputLimit('stdout_limit')
  return super().write(s)

def main():
 payload=json.loads(sys.stdin.read(150000));limits=payload['limits']
 resource.setrlimit(resource.RLIMIT_CPU,(limits['cpu_seconds'],limits['cpu_seconds']+1))
 resource.setrlimit(resource.RLIMIT_FSIZE,(0,0));resource.setrlimit(resource.RLIMIT_NOFILE,(32,32));sys.setrecursionlimit(limits['recursion_depth'])
 if sys.platform!='darwin':
  try:resource.setrlimit(resource.RLIMIT_AS,(limits['memory_bytes'],limits['memory_bytes']))
  except (ValueError,OSError):pass
 def memory(*args):
  rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
  if (rss if sys.platform=='darwin' else rss*1024)>limits['memory_bytes']:
   signal.setitimer(signal.ITIMER_REAL,0)
   raise MemoryError('rss_limit')
 signal.signal(signal.SIGALRM,memory);signal.setitimer(signal.ITIMER_REAL,.02,.02)
 start=time.monotonic()
 try:
  memory()
  base['validate'](payload['code'])
  def importer(name,globals=None,locals=None,fromlist=(),level=0):
   if level or name not in base['MODULES']:raise ImportError('Blocked import')
   return builtins.__import__(name,globals,locals,fromlist,level)
  ns={'__builtins__':{**{k:getattr(builtins,k) for k in base['BUILTINS']},'__import__':importer}}
  with contextlib.redirect_stdout(Capture(limits['output_bytes'])):
   exec(compile(payload['code'],'<engineering-operator>','exec'),ns)
   value=ns['solve'](payload['problem'])
  if value is not None and (type(value) is not int or value<0 or value.bit_length()>limits['integer_bits']):raise TypeError('Result contract: exact nonnegative int or explicit None')
  result=dict(status='declined' if value is None else 'success',value=value)
 except BaseException as exc:
  kind='CODE_RESOURCE_LIMIT' if isinstance(exc,(MemoryError,RecursionError,OutputLimit)) else 'CODE_CONTRACT_ERROR' if isinstance(exc,TypeError) else 'CODE_IMPORT_ERROR' if isinstance(exc,ImportError) else 'CODE_RUNTIME_ERROR'
  result=dict(status='error',value=None,error=type(exc).__name__+': '+str(exc)[:400],error_type=kind)
 result.update(cpu_seconds=resource.getrusage(resource.RUSAGE_SELF).ru_utime,peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,worker_seconds=time.monotonic()-start)
 signal.setitimer(signal.ITIMER_REAL,0)
 print(json.dumps(result))

if __name__=='__main__':main()
