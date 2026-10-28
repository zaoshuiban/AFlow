"""Trusted bootstrap. Payload has code and test input; never expected outputs."""
import ast,builtins,contextlib,io,json,os,resource,signal,sys,time,types
import typing,collections,functools,heapq,bisect,math,itertools
class PolicyError(Exception):pass
class OutputExceeded(Exception):pass
class Capture(io.StringIO):
 def __init__(self,cap):super().__init__();self.cap=cap;self.used=0
 def write(self,s):
  self.used+=len(s.encode())
  if self.used>self.cap:raise OutputExceeded('output bytes exceeded')
  return super().write(s)

def main():
 payload=json.loads(sys.stdin.read());limits=payload['limits'];start=time.monotonic();violations=[]
 resource.setrlimit(resource.RLIMIT_CPU,(limits['cpu_seconds'],limits['cpu_seconds']+1));resource.setrlimit(resource.RLIMIT_FSIZE,(0,0));resource.setrlimit(resource.RLIMIT_NOFILE,(32,32));sys.setrecursionlimit(limits['recursion_depth'])
 def mem(*_):
  if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss>limits['memory_bytes']:
   signal.setitimer(signal.ITIMER_REAL,0);raise MemoryError('rss limit')
 signal.signal(signal.SIGALRM,mem);signal.setitimer(signal.ITIMER_REAL,.02,.02)
 roots=[os.path.realpath(sys.prefix),os.path.realpath(sys.base_prefix),'/System','/usr','/Library/Apple',os.getcwd()]
 def deny(what):violations.append(what);raise PolicyError(what)
 def audit(event,args):
  if event=='open':
   path=args[0];mode=args[1];flags=args[2]
   if isinstance(path,int):return
   path=os.path.realpath(os.fsdecode(path))
   if (mode and any(x in mode for x in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):deny('file_write')
   if not any(path==r or path.startswith(r+'/') for r in roots):deny('file_read')
  if event.startswith(('socket.','subprocess.')) or event in ['os.fork','os.forkpty','os.system','os.posix_spawn','os.exec','ctypes.dlopen','ctypes.dlsym']:deny(event)
 sys.addaudithook(audit)
 output=Capture(limits['output_bytes']);errors=Capture(limits['output_bytes']);stdin=io.TextIOWrapper(io.BytesIO(payload.get('stdin','').encode()),encoding='utf-8')
 def safe_input(prompt=''):
  if prompt:output.write(str(prompt))
  s=stdin.readline()
  if not s:raise EOFError()
  return s.rstrip('\r\n')
 facade=types.SimpleNamespace(stdin=stdin,stdout=output,stderr=errors,maxsize=sys.maxsize,version=sys.version,exit=lambda n=0:(_ for _ in ()).throw(SystemExit(n)))
 def recursion(n):
  if type(n) is not int or n>limits['recursion_depth']:deny('recursion_limit_change')
  sys.setrecursionlimit(n)
 facade.setrecursionlimit=recursion
 modules={'sys','json','math','collections','itertools','functools','heapq','bisect','string','re','statistics','fractions','decimal','operator','array','typing','copy'}
 def importer(name,globals=None,locals=None,fromlist=(),level=0):
  if level or name not in modules:deny('unsupported_import:'+name)
  if name=='sys':return facade
  return builtins.__import__(name,globals,locals,fromlist,level)
 try:
  tree=ast.parse(payload['code'])
  forbidden={'eval','exec','compile','globals','locals','vars','getattr','setattr','delattr','breakpoint','help','dir','__import__'}
  for n in ast.walk(tree):
   if isinstance(n,ast.Attribute) and n.attr.startswith('_'):deny('private_attribute')
   if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load) and n.id in forbidden:deny('introspection_or_dynamic_code')
   if isinstance(n,(ast.Import,ast.ImportFrom)):
    names=[n.module] if isinstance(n,ast.ImportFrom) else [a.name for a in n.names]
    if any(name not in modules for name in names) or getattr(n,'level',0):deny('unsupported_import')
  safe=dict(builtins.__dict__);safe.update(__import__=importer,input=safe_input)
  for k in forbidden-{'__import__'}:safe.pop(k,None)
  mode=payload['interface']['mode'];ns={'__builtins__':safe,'__name__':'__main__' if mode=='stdin' else 'd0_candidate'}
  if payload['interface'].get('context')=='lcb-safe-v1':
   # Task framework context, not a rewrite of candidate algorithm source.
   ns.update({k:getattr(typing,k) for k in ['List','Optional','Tuple','Dict','Set','Deque','DefaultDict','Any','Iterable']})
   ns.update({k:getattr(collections,k) for k in ['Counter','defaultdict','deque']})
   ns.update({k:getattr(functools,k) for k in ['cache','lru_cache','reduce']})
   ns.update({k:getattr(heapq,k) for k in ['heappush','heappop','heapify','heappushpop','heapreplace']})
   ns.update({k:getattr(bisect,k) for k in ['bisect_left','bisect_right','insort']})
   ns.update(inf=math.inf,gcd=math.gcd,accumulate=itertools.accumulate)
  value=None
  with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
   try:exec(compile(tree,'<d0-candidate>','exec'),ns)
   except SystemExit as e:
    if mode!='stdin' or e.code not in (0,None):raise
   if mode=='function' and payload['interface']['function'] not in ns:raise PolicyError('missing_function_interface')
   if mode=='class' and (payload['interface'].get('class','Solution') not in ns or not hasattr(ns[payload['interface'].get('class','Solution')],payload['interface']['method'])):raise PolicyError('missing_class_method_interface')
   if mode=='function':value=ns[payload['interface']['function']](*payload.get('args',[]),**payload.get('kwargs',{}))
   elif mode=='class':value=getattr(ns[payload['interface'].get('class','Solution')](),payload['interface']['method'])(*payload.get('args',[]),**payload.get('kwargs',{}))
   elif mode!='stdin':raise ValueError('Unknown interface')
  if violations:result={'status':'POLICY_VIOLATION','violations':violations}
  else:result={'status':'EXECUTED','value':value,'stdout':output.getvalue(),'stderr':errors.getvalue()}
  json.dumps(result,allow_nan=False)
 except BaseException as e:
  status='INTERFACE_REJECTION' if isinstance(e,PolicyError) and str(e).startswith('missing_') else 'POLICY_VIOLATION' if violations or isinstance(e,PolicyError) else 'MEMORY_LIMIT' if isinstance(e,MemoryError) else 'RESOURCE_LIMIT' if isinstance(e,(RecursionError,OutputExceeded)) else 'SYNTAX_ERROR' if isinstance(e,SyntaxError) else 'RUNTIME_ERROR'
  result={'status':status,'error':type(e).__name__+': '+str(e)[:300],'violations':violations}
 signal.setitimer(signal.ITIMER_REAL,0)
 result.update(cpu_seconds=resource.getrusage(resource.RUSAGE_SELF).ru_utime,peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,worker_seconds=time.monotonic()-start)
 print(json.dumps(result,allow_nan=False))
if __name__=='__main__':main()
