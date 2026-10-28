"""Shared isolated counting worker; large exact integers, no IO capabilities."""
import ast
import builtins
import json
import runpy
from pathlib import Path

_base=runpy.run_path(str(Path(__file__).with_name('frozen_math_worker.py')),run_name='counting_validator_definition')
MODULES=_base['MODULES'];MODULES.add('json')
ATTRIBUTES=_base['ATTRIBUTES'];ATTRIBUTES.update(('loads','dumps','cache','cache_clear','cache_info','popleft','appendleft','clear','insert','find','rfind','isdigit','bit_length','bit_count','rotate','bisect_left','bisect_right','insort','heappop','heappush','heapify','comb','perm'))
MODULES.update(('heapq','bisect'))
BUILTINS=_base['BUILTINS']+['bytearray','bytes','divmod','type','slice','iter','next','print','repr','int','TypeError','IndexError','KeyError','StopIteration']
validate=_base['validate']


def main():
    import sys,resource,signal,contextlib,io
    resource.setrlimit(resource.RLIMIT_CPU,(5,5))
    resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
    resource.setrlimit(resource.RLIMIT_NOFILE,(16,16))
    def memory_check(*_):
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if (peak if sys.platform=='darwin' else peak*1024)>256*1024**2:raise SystemExit('memory_threshold')
    signal.signal(signal.SIGALRM,memory_check);signal.setitimer(signal.ITIMER_REAL,.05,.05)
    try:
        payload=json.loads(sys.stdin.read(200000));validate(payload['code'])
        def safe_import(name,globals=None,locals=None,fromlist=(),level=0):
            if level or name not in MODULES:raise ImportError('Blocked import')
            return builtins.__import__(name,globals,locals,fromlist,level)
        ns={'__builtins__':{**{n:getattr(builtins,n) for n in BUILTINS},'__import__':safe_import}}
        # Captured stdout is never interpreted as an answer.
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture):
            exec(compile(payload['code'],'<counting-tool>','exec'),ns)
            value=ns['solve'](payload['problem'])
        if value is not None and (type(value) is not int or value<0 or value.bit_length()>4096):raise ValueError('Return nonnegative int or None')
        result=dict(status='declined' if value is None else 'success',value=value,cpu_seconds=resource.getrusage(resource.RUSAGE_SELF).ru_utime)
    except BaseException as exc:
        result=dict(status='error',error=type(exc).__name__+': '+str(exc)[:500],value=None)
    print(json.dumps(result))


if __name__=='__main__':main()
