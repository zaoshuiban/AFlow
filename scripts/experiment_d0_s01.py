"""S0.1 offline protocol. No API client or model-call path."""
import ast,asyncio,copy,difflib,hashlib,itertools,json,random,shutil,time
from pathlib import Path
from scripts.experiment_d0 import history as prior_history
from scripts.offline_loop_protocol import atomic_json,file_hash
from scripts.d0_s01_runtime.runner import execute_case,judge,admit,public_task
from scripts.d0_s01_runtime.evaluation import evaluate_task,EngineeringSession
from scripts.d0_s01_runtime.contracts import taco_task,lcb_task
from scripts.d0_s01_references import REFERENCES
OLD=Path('results/experiment_d0_20261019_s0_v1');ROOT=Path('results/experiment_d0_s01_20261019_v1')
def history():
 d=prior_history()
 for p in OLD.rglob('*'):
  if p.is_file() and '__pycache__' not in str(p):d[str(p)]=file_hash(p)
 for p in list(Path('scripts/d0_programming_runtime').glob('*.py'))+[Path('scripts/experiment_d0.py'),Path('scripts/d0_dataset_audit.py'),Path('scripts/d0_integration_acceptance.py'),Path('scripts/d0_integration_acceptance_v2.py'),Path('scripts/report_experiment_d0.py')]:d[str(p)]=file_hash(p)
 return d

def extra_cases():
 fn={'mode':'function','function':'solve'};cl={'mode':'class','class':'Solution','method':'solve','context':'lcb-safe-v1'}
 return [
 dict(name='lcb_List_annotations',code='class Solution:\n def solve(self,a: List[int]) -> List[int]:return a[::-1]',interface=cl,case={'args':[[1,2]],'expected':[2,1]},expected_verdict='PASS'),
 dict(name='lcb_optional_dict_tuple_annotations',code='def solve(a: Optional[List[int]]) -> Dict[str,List[int]]:return {"a":a}',interface={**fn,'context':'lcb-safe-v1'},case={'args':[[1]],'expected':{'a':[1]}},expected_verdict='PASS'),
 dict(name='lcb_preloaded_Counter',code='class Solution:\n def solve(self,a: List[int]) -> int:return Counter(a)[1]',interface=cl,case={'args':[[1,1,2]],'expected':2},expected_verdict='PASS'),
 dict(name='lcb_preloaded_cache_heap',code='@cache\ndef f(x):return x+1\nclass Solution:\n def solve(self,a: List[int]) -> int:\n  heapify(a);return f(heappop(a))',interface=cl,case={'args':[[4,2]],'expected':3},expected_verdict='PASS'),
 dict(name='taco_declared_single_return',code='def solve():return [4,63]',interface=fn,case={'expected':[[4,63]]},judge={'mode':'taco_single_return','return_schema':'int_pair'},expected_verdict='PASS'),
 dict(name='taco_no_actual_extra_unwrap',code='def solve():return [[4,63]]',interface=fn,case={'expected':[[4,63]]},judge={'mode':'taco_single_return','return_schema':'int_pair'},expected_verdict='WRONG_ANSWER'),
 dict(name='taco_bool_not_integer',code='def solve():return [True,63]',interface=fn,case={'expected':[[1,63]]},judge={'mode':'taco_single_return','return_schema':'int_pair'},expected_verdict='WRONG_ANSWER'),
 dict(name='exact_nested_list_preserved',code='def solve():return [4,63]',interface=fn,case={'expected':[[4,63]]},expected_verdict='WRONG_ANSWER'),
 dict(name='missing_function_interface',code='def wrong():return 1',interface=fn,case={'expected':1},expected_verdict='INTERFACE_REJECTION'),
 dict(name='missing_class_method',code='class Solution:pass',interface=cl,case={'expected':1},expected_verdict='INTERFACE_REJECTION'),
 dict(name='syntax_separate',code='def broken(',interface=fn,case={'expected':1},expected_verdict='SYNTAX_ERROR'),
 dict(name='dynamic_eval_still_forbidden',code='def solve():return eval("1")',interface=fn,case={'expected':1},expected_verdict='POLICY_VIOLATION'),
 dict(name='unsupported_import_still_forbidden',code='import os\ndef solve():return 1',interface=fn,case={'expected':1},expected_verdict='POLICY_VIOLATION'),
 dict(name='lcb_context_no_private_access',code='class Solution:\n def solve(self,a):return a.__class__',interface=cl,case={'args':[[1]],'expected':1},expected_verdict='POLICY_VIOLATION')]

def prepare():
 if (ROOT/'protocol.json').exists():raise FileExistsError('No overwrite')
 atomic_json(ROOT/'history_before.json',history());atomic_json(ROOT/'extra_cases.json',extra_cases())
 inv=json.loads((OLD/'smoke_task_ids.json').read_text());atomic_json(ROOT/'original_smoke_ids.json',inv)
 # Reserve order frozen before any new reference results. No reserve execution planned.
 inventory=json.loads((OLD/'dataset_inventory.json').read_text());reserve={}
 original={x['id'] for x in inv}
 for source in ['TACO','LiveCodeBench-v6-increment']:
  for level in ['Low','Medium','High']:
   pool=[x['id'] for x in inventory if x['source']==source and x['difficulty']==level and x['admitted_metadata'] and x['id'] not in original|{'taco-test-11'}]
   reserve[source+'/'+level]=sorted(pool,key=lambda s:hashlib.sha256(('D0-S01-RESERVE-261019'+s).encode()).hexdigest())
 atomic_json(ROOT/'reserve_order.json',reserve)
 sources=list(Path('scripts/d0_s01_runtime').glob('*.py'))+[Path(__file__),Path('scripts/d0_s01_references.py')]
 atomic_json(ROOT/'protocol.json',dict(run='D0-S0.1',API_authorized_cny=0,S1_S4='NOT_AUTHORIZED',runtime='d0-programming-v2-s01',critical_gate='All old30 x2 + extra14 x2 engineering checks pass; fixed data conflict independently reproduced and quarantined; correct/wrong source integrations and boundaries pass; >=9 original candidates qualified across3 sources, no forced replacement.',TACO_reference_policy='Exactly original source indices0,1,2 frozen. Run all3 independently irrespective earlier pass/fail. Max75s each across ALL available tests; no new references appended. Unsupported features and Python semantics are reference issues, not dataset errors.',LCB_reference_policy='Six trusted offline references frozen before hidden validation; independent small exact checks required. No algorithm patches after results; failed evidence means quarantine.',candidate_admission='Full supplied tests pass under fixed case6s/CPU5s/task75s references, plus no known contract or special judge ambiguity. A uses exhaustive n<=6. Insufficient reference/resources=>exclude; do not alter raw answers.',TACO40='Only explicitly declared singleton return envelope around int-pair; no fallback or arbitrary nesting removal.',reserves='Frozen deterministic order recorded but no replacement required or automatic; report all original18.',full_LCB='Frozen old revision release_v6=test..test6; all6 SHA256 match LFS',sources={str(p):file_hash(p) for p in sources},extra_sha256=file_hash(ROOT/'extra_cases.json')))
 for p in Path('scripts/d0_s01_runtime').glob('*.py'):
  old=Path('scripts/d0_programming_runtime')/p.name
  if old.exists():(ROOT/(p.name+'.diff')).write_text(''.join(difflib.unified_diff(old.read_text().splitlines(True),p.read_text().splitlines(True),fromfile=str(old),tofile=str(p))))
 print('Frozen S0.1; API cap0; no backups executed',flush=True)

def verify():
 p=json.loads((ROOT/'protocol.json').read_text())
 assert history()==json.loads((ROOT/'history_before.json').read_text())
 for f,h in p['sources'].items():assert file_hash(Path(f))==h,f
 assert file_hash(ROOT/'extra_cases.json')==p['extra_sha256']

async def regression():
 verify()
 if (ROOT/'regression_results.json').exists():raise FileExistsError('No rerun')
 import scripts.experiment_d0 as original
 baseline=json.loads((OLD/'offline_cases.json').read_text());out=[]
 for repeat in range(2):
  for c in baseline+extra_cases():
   special=c.get('special');details={}
   if special=='admission':details=admit({'interface':{'mode':'stdin'},'cases':[{}],'judge':{'mode':'nonunique'}});passed=not details['admitted']
   elif special=='boundary':
    t={'id':'x','prompt':'public','source':'engineering','interface':{'mode':'function','function':'solve'},'cases':[{'args':[1],'expected':2}],'reference':'NEVER_IN_MODEL'}
    session=EngineeringSession(t,ROOT/'regression_logs');pub=session.model_input();visible=await session.self_test('def solve(x):return 0',{'args':[1],'expected':2});session.submit('def solve(x):return x+1');hidden=await session.final_evaluate();denied=False
    try:session.model_input()
    except ValueError:denied=True
    passed='cases' not in pub and 'reference' not in pub and 'verdict' not in visible and hidden['verdict']=='PASS' and denied;details=dict(public=pub,visible=visible,hidden=hidden,after_submit_denied=denied)
   elif special=='fail_closed':
    from unittest.mock import patch
    with patch('scripts.d0_s01_runtime.runner.sys.platform','linux'):details=await execute_case('print(5)',{'mode':'stdin'},{'expected':'5\n'})
    passed=details['verdict']=='INFRASTRUCTURE_ERROR'
   elif special=='kernel':details=await original.kernel_probes();passed=all(x['passed'] for x in details)
   else:
    details=await execute_case(c['code'],c['interface'],c['case'],c.get('judge'),role='hidden',wall=c.get('wall',2),limits=c.get('limits'),log_dir=ROOT/'regression_logs');passed=details['verdict']==c['expected_verdict']
   out.append(dict(name=c['name'],repeat=repeat,passed=bool(passed),details=details));print(c['name'],repeat,passed,flush=True)
 atomic_json(ROOT/'regression_results.json',out);atomic_json(ROOT/'regression_gate.json',dict(passed=all(x['passed'] for x in out),units=len(out),passed_units=sum(x['passed'] for x in out)))

if __name__=='__main__':
 import argparse
 a=argparse.ArgumentParser();a.add_argument('command',choices=['prepare','regression']);x=a.parse_args()
 if x.command=='prepare':prepare()
 else:asyncio.run(regression())
