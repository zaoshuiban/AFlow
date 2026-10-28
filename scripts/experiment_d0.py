"""D0 S0 only. No model client imports or API execution path."""
import argparse,asyncio,copy,json,os,sys,tempfile,time
from pathlib import Path
from scripts.offline_loop_protocol import atomic_json,file_hash
from scripts.experiment_c0 import history as previous_history
from scripts.d0_programming_runtime.runner import execute_case,admit,public_task
from scripts.operator_runtime.executor import sandbox_profile
ROOT=Path('results/experiment_d0_20261019_s0_v1')
def history():
 d=previous_history()
 for p in Path('results/experiment_c0_20261015_v1').rglob('*'):
  if p.is_file() and '__pycache__' not in str(p):d[str(p)]=file_hash(p)
 return d

def cases():
 stdin={'mode':'stdin'};fn={'mode':'function','function':'solve'};out=[]
 def add(name,code,case,verdict='PASS',interface=None,judge=None,**kw):out.append(dict(name=name,code=code,case=case,expected_verdict=verdict,interface=interface or stdin,judge=judge or {'mode':'exact'},**kw))
 add('stdin_correct','a,b=map(int,input().split());print(a+b)',{'stdin':'2 3\n','expected':'5\n'})
 add('stdin_wrong','print(4)',{'stdin':'2 3\n','expected':'5\n'},'WRONG_ANSWER')
 add('multiline','print("one");print("two")',{'expected':'one\ntwo\n'})
 add('tokens_whitespace','print("1  2")',{'expected':'1\n2'},judge={'mode':'tokens'})
 add('exact_whitespace','print("1  2")',{'expected':'1 2\n'},'WRONG_ANSWER')
 add('function_integer','def solve(a,b):return a+b',{'args':[2,3],'expected':5},interface=fn)
 add('negative_integer','def solve():return -3',{'expected':-3},interface=fn)
 add('function_string','def solve(s):return s[::-1]',{'args':['abc'],'expected':'cba'},interface=fn)
 add('nested_list','def solve(x):return [x,[1,2]]',{'args':[3],'expected':[3,[1,2]]},interface=fn)
 add('class_method','class Solution:\n def add(self,a,b):return a+b',{'args':[2,3],'expected':5},interface={'mode':'class','class':'Solution','method':'add'})
 add('kwargs','def solve(a,b=2):return a*b',{'args':[3],'kwargs':{'b':4},'expected':12},interface=fn)
 add('float_tolerance_pass','print(0.3333334)',{'expected':'0.3333333'},judge={'mode':'float','atol':1e-6,'rtol':1e-6})
 add('float_tolerance_fail','print(0.35)',{'expected':'0.3333333'},'WRONG_ANSWER',judge={'mode':'float','atol':1e-6,'rtol':1e-6})
 add('casefold','print("YES")',{'expected':'yes'},judge={'mode':'casefold'})
 add('bool_not_integer','def solve():return True',{'expected':1},'WRONG_ANSWER',interface=fn)
 add('stdlib_and_stdin_read','import sys,heapq\na=list(map(int,sys.stdin.read().split()));heapq.heapify(a);print(heapq.heappop(a))',{'stdin':'3 1 2','expected':'1\n'})
 add('runtime_error','print(1//0)',{'expected':'5\n'},'RUNTIME_ERROR')
 add('wall_timeout','while True:pass',{'expected':''},'TIME_LIMIT',wall=.2)
 add('memory_limit','a=[0]*12000000\nwhile True:pass',{'expected':''},'MEMORY_LIMIT',limits={'memory_bytes':32*1024**2})
 add('output_limit','print("x"*10000)',{'expected':''},'RESOURCE_LIMIT',limits={'output_bytes':512})
 add('file_read_then_correct','try:\n open("/private/tmp/d0-secret-not-in-payload")\nexcept Exception:pass\nprint(5)',{'expected':'5\n'},'POLICY_VIOLATION')
 add('file_write_then_correct','try:\n open("/private/tmp/d0-disallowed-write","w")\nexcept Exception:pass\nprint(5)',{'expected':'5\n'},'POLICY_VIOLATION')
 add('network_then_correct','import socket\nprint(5)',{'expected':'5\n'},'POLICY_VIOLATION')
 add('fork_then_correct','import subprocess\nprint(5)',{'expected':'5\n'},'POLICY_VIOLATION')
 add('introspection','print((1).__class__)\nprint(5)',{'expected':'5\n'},'POLICY_VIOLATION')
 add('recursion_change_then_correct','import sys\ntry:sys.setrecursionlimit(1000000)\nexcept Exception:pass\nprint(5)',{'expected':'5\n'},'POLICY_VIOLATION')
 out += [dict(name='unsupported_spj_admission',special='admission'),dict(name='hidden_information_boundary',special='boundary'),dict(name='native_fail_closed',special='fail_closed'),dict(name='native_kernel_enforcement',special='kernel')]
 return out

def prepare():
 if (ROOT/'protocol.json').exists():raise FileExistsError('Frozen run exists')
 atomic_json(ROOT/'history_before.json',history());atomic_json(ROOT/'offline_cases.json',cases())
 sourcepaths=[Path(__file__)]+list(Path('scripts/d0_programming_runtime').glob('*.py'))
 atomic_json(ROOT/'protocol.json',dict(experiment='D0_S0',runtime='d0-programming-v1',API_authorized=False,API_spending_cap_cny=0,S1='NOT_AUTHORIZED',S2_S4='NOT_AUTHORIZED',cases=30,repetitions=2,required='All 30 cases pass twice; no scoring/isolation failure. Real-source interface path validation and data readiness reported separately.',sources={str(p):file_hash(p) for p in sourcepaths},case_sha256=file_hash(ROOT/'offline_cases.json'),profile='Native macOS deny filesystem outside interpreter/runtime, all writes/network/fork denied; secondary Python language profile denies introspection/dynamic code and unsupported imports. Not a general hostile-Python security proof.',payload='No expected/reference fields; judge lives in parent; role logs independent',judges=['exact','tokens','float','casefold'],special_judges='Nonunique outputs excluded pending trusted task-specific judge',data='TACO frozen test; LCB release_v6 latest-partition test6 plus framework mapping; no claim of fresh/uncontaminated data'))
 print('Frozen S0 protocol; 30 tests x2; API cap 0',flush=True)

async def kernel_probes():
 results=[]
 with tempfile.TemporaryDirectory(prefix='d0-kernel-test-') as cwd:
  programs=[('file_read','open("/etc/passwd").read()'),('file_write','open("/private/tmp/d0-kernel-no-write","w").write("x")'),('network','import socket;socket.socket().connect(("127.0.0.1",9))'),('fork','import os;os.fork()')]
  for name,code in programs:
   p=await asyncio.create_subprocess_exec('/usr/bin/sandbox-exec','-p',sandbox_profile(cwd),str(Path(sys.executable).resolve()),'-I','-B','-c',code,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,cwd=cwd)
   o,e=await asyncio.wait_for(p.communicate(),2);text=e.decode(errors='replace');results.append(dict(probe=name,exit_code=p.returncode,stderr=text,passed=p.returncode!=0 and ('Operation not permitted' in text or 'Permission denied' in text)))
 return results

async def run():
 p=json.loads((ROOT/'protocol.json').read_text())
 for f,h in p['sources'].items():assert file_hash(Path(f))==h,f
 assert file_hash(ROOT/'offline_cases.json')==p['case_sha256']
 assert history()==json.loads((ROOT/'history_before.json').read_text())
 if (ROOT/'offline_results.json').exists():raise FileExistsError('No silent rerun; new protocol required after repair')
 results=[]
 for repeat in range(2):
  for case in json.loads((ROOT/'offline_cases.json').read_text()):
   start=time.monotonic();special=case.get('special');details={}
   if special=='admission':
    details=admit({'interface':{'mode':'stdin'},'cases':[{}],'judge':{'mode':'nonunique'}});passed=not details['admitted']
   elif special=='boundary':
    task={'id':'x','source':'engineering','prompt':'Sum numbers','interface':{'mode':'stdin'},'hidden_cases':[{'expected':'SECRET_93871'}],'reference':'PRIVATE_REFERENCE_93871'};pub=public_task(task);text=json.dumps(pub);passed='SECRET_93871' not in text and 'PRIVATE_REFERENCE' not in text and 'hidden_cases' not in pub;details={'public_task':pub}
    for role in ['self_test','hidden','diagnostic']:
     a=await execute_case('print(5)',{'mode':'stdin'},{'expected':'5\n'},role=role,log_dir=ROOT/'execution_logs');passed &= a['verdict']=='PASS' and 'expected' not in a['payload_fields'];details[role]=a
   elif special=='fail_closed':
    from unittest.mock import patch
    with patch('scripts.d0_programming_runtime.runner.sys.platform','linux'):details=await execute_case('print(5)',{'mode':'stdin'},{'expected':'5\n'})
    passed=details['verdict']=='INFRASTRUCTURE_ERROR'
   elif special=='kernel':details=await kernel_probes();passed=all(x['passed'] for x in details)
   else:
    details=await execute_case(case['code'],case['interface'],case['case'],case['judge'],role='hidden',wall=case.get('wall',2),limits=case.get('limits'),log_dir=ROOT/'execution_logs');passed=details['verdict']==case['expected_verdict']
   row=dict(name=case['name'],repeat=repeat,passed=bool(passed),details=details,elapsed_seconds=time.monotonic()-start);results.append(row)
   print(case['name'],repeat,'PASS' if passed else 'FAIL',flush=True)
 atomic_json(ROOT/'offline_results.json',results);atomic_json(ROOT/'offline_gate.json',dict(passed=all(x['passed'] for x in results),units=len(results),passed_units=sum(x['passed'] for x in results),API_calls=0))
 print('gate',all(x['passed'] for x in results),flush=True)
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('command',choices=['prepare','run']);x=a.parse_args()
 if x.command=='prepare':prepare()
 else:asyncio.run(run())
