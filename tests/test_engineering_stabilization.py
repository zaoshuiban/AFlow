"""Engineering-only fault injection. No research tasks or real API calls."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from scripts.operator_runtime.api import RetryController,RetryPolicy,Limits,InfrastructureInterrupted,ResponseFormatError
from scripts.operator_runtime.generation import synthesize
from scripts.operator_runtime.lifecycle import Registry,ValidationCase,ValidationProtocol
from scripts.operator_runtime.executor import execute,envelope
from scripts.operator_runtime.events import Provenance,evaluate,EventLog

GOOD='import json\ndef solve(problem):\n    return json.loads(problem)["value"] * 2\n'
BAD='def solve(problem):\n    return 0\n'

class ConnectionFault(Exception):pass
class TimeoutFault(Exception):pass
class RateFault(Exception):
 status_code=429
 headers={'Retry-After':'3'}

class FakeAPI:
 def __init__(self,items):self.items=list(items);self.calls=[]
 async def __call__(self,request,request_id):
  self.calls.append(request_id);v=self.items.pop(0)
  if isinstance(v,BaseException):raise v
  return {'response':v,'usage':{'input_tokens':10,'output_tokens':10},'reported_model':'mock'}

class RetryTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.delays=[]
 async def asyncTearDown(self):self.tmp.cleanup()
 async def sleep(self,s):self.delays.append(s)
 def controller(self,items,**kw):
  api=FakeAPI(items);c=RetryController(self.root,api,policy=RetryPolicy(base_delay=1),limits=Limits(max_requests=10,max_cost_cny=1),sleep=self.sleep,**kw);return c,api
 async def test_connection_then_success_separate_counters(self):
  c,api=self.controller([ConnectionFault(),json.dumps({'code':GOOD})]);r=await synthesize(c,[],lambda code:asyncio.sleep(0,result={'passed':True}),max_repairs=1)
  self.assertEqual(r['api_request_attempts'],2);self.assertEqual(r['initial_generation_attempts'],1);self.assertEqual(r['code_repair_attempts'],0)
  self.assertEqual(self.delays,[1]);self.assertEqual(len(set(api.calls)),2);self.assertEqual(c.usage()['unknown_usage_requests'],1)
 async def test_wrong_code_then_real_repair(self):
  c,_=self.controller([json.dumps({'code':BAD}),json.dumps({'code':GOOD})])
  r=await synthesize(c,[],lambda code:asyncio.sleep(0,result={'passed':code==GOOD}),max_repairs=1)
  self.assertEqual(r['initial_generation_attempts'],1);self.assertEqual(r['code_repair_attempts'],1);self.assertEqual(r['parsed_model_responses'],2);self.assertEqual(r['code'],GOOD)
 async def test_exhausted_connections_interrupt_without_candidate(self):
  c,_=self.controller([ConnectionFault()]*3)
  r=await synthesize(c,[],lambda _:None,max_repairs=1)
  self.assertEqual(r['status'],'INFRASTRUCTURE_INTERRUPTED');self.assertIsNone(r['code']);self.assertEqual(r['code_repair_attempts'],0);self.assertEqual(c.usage()['api_request_attempts'],3)
 async def test_timeout_unknown_and_hard_request_cap(self):
  api=FakeAPI([TimeoutFault()]*3);c=RetryController(self.root,api,limits=Limits(max_requests=1,max_cost_cny=1),sleep=self.sleep)
  with self.assertRaises(InfrastructureInterrupted):await c.request('x',{},10)
  records=c.records();self.assertEqual(len(records),1);self.assertEqual(records[0]['delivery_status'],'UNKNOWN_DELIVERY_STATUS');self.assertIsNone(records[0]['usage']);self.assertGreater(records[0]['charged_budget_cny'],0)
 async def test_parse_error_not_network_retry_and_raw_retained(self):
  c,api=self.controller(['not-json',json.dumps({'code':GOOD})]);r=await synthesize(c,[],lambda _:None,max_repairs=1)
  self.assertEqual(r['status'],'MODEL_RESPONSE_ERROR');self.assertEqual(len(api.calls),1);self.assertEqual(c.records()[0]['raw_response']['response'],'not-json')
 async def test_retry_after(self):
  c,_=self.controller([RateFault(),'{}']);await c.request('x',{},10);self.assertEqual(self.delays,[3])
 async def test_resume_unknown_does_not_duplicate(self):
  c,_=self.controller([ConnectionFault()]*3)
  with self.assertRaises(InfrastructureInterrupted):await c.request('x',{},10)
  c2,api=self.controller(['{}'])
  with self.assertRaises(InfrastructureInterrupted):await c2.request('x',{},10)
  self.assertEqual(len(api.calls),0)
 async def test_cancelled_request_keeps_uncertain_reservation(self):
  async def sender(*args):raise asyncio.CancelledError()
  c=RetryController(self.root,sender,sleep=self.sleep)
  with self.assertRaises(asyncio.CancelledError):await c.request('cancel',{},10)
  self.assertEqual(c.records()[0]['delivery_status'],'UNKNOWN_DELIVERY_STATUS');self.assertIsNone(c.records()[0]['usage'])

class LifecycleTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.reg=Registry(self.root)
 async def asyncTearDown(self):self.tmp.cleanup()
 def protocol(self):
  cats=['development','boundary','invalid','property','metamorphic','crosscheck','regression','resource']
  cs=[ValidationCase(c,{'value':i},expected=i*2) for i,c in enumerate(cats)]
  cs[2]=ValidationCase('invalid',{},expect_error=True)
  return ValidationProtocol('engineering-v1','synthetic-v1',cs)
 async def test_all_pass_freeze_register_and_hash_reject(self):
  op=await self.reg.admit('double','1',GOOD,self.protocol(),generation={'method':'fixed_engineering_fixture'})
  self.assertEqual(op['state'],'REGISTERED');self.assertEqual(self.reg.load('double','1')['code'],GOOD)
  (self.root/'double/1/source.py').write_text(BAD)
  with self.assertRaises(ValueError):self.reg.load('double','1')
 async def test_wrong_integer_and_partial_rejected(self):
  for version,code in [('bad',BAD),('partial',GOOD.replace('* 2','* 2 if json.loads(problem)["value"] < 3 else 0'))]:
   op=await self.reg.admit('double',version,code,self.protocol());self.assertEqual(op['state'],'REJECTED')
   with self.assertRaises(ValueError):self.reg.load('double',version)
 async def test_timeout_rejected(self):
  protocol=self.protocol();protocol.wall_seconds=.1
  op=await self.reg.admit('hang','1','def solve(problem):\n    while True: pass\n',protocol);self.assertEqual(op['state'],'REJECTED')
 async def test_missing_coverage_rejected(self):
  with self.assertRaises(ValueError):ValidationProtocol('x','y',[ValidationCase('development',{'value':1},expected=2)])
 async def test_frozen_version_cannot_be_reused(self):
  await self.reg.admit('double','1',GOOD,self.protocol())
  with self.assertRaises(FileExistsError):await self.reg.admit('double','1',GOOD,self.protocol())
 async def test_signature_and_import_are_static_rejections(self):
  for v,c in [('sig','def solve():\n return 1\n'),('import','import os\ndef solve(problem):\n return 1\n')]:
   r=await self.reg.admit('wrong',v,c,self.protocol());self.assertEqual(r['state'],'REJECTED')
 async def test_quarantine_refuses_load(self):
  await self.reg.admit('double','1',GOOD,self.protocol());self.reg.quarantine('double','1','engineering incident')
  with self.assertRaises(ValueError):self.reg.load('double','1')

class ExecutionTests(unittest.IsolatedAsyncioTestCase):
 async def test_correct_result_not_automatically_verified(self):
  r=await execute('def solve(problem):\n return 2\n','{}',2);self.assertEqual(r['status'],'success');self.assertEqual(r['validation_status'],'NOT_CHECKED')
 async def test_wrong_integer_success_not_verified(self):
  r=await execute(BAD,'{}',2);self.assertEqual(r['execution_status'],'SUCCESS');self.assertEqual(r['validation_status'],'NOT_CHECKED')
 async def test_exception_and_illegal_types(self):
  for code in ['def solve(problem):\n raise ValueError("boom")\n','def solve(problem):\n return "3"\n','def solve(problem):\n return {"x":3}\n']:
   r=await execute(code,'{}',2);self.assertNotEqual(r['status'],'success');self.assertEqual(r['validation_status'],'FAILED');self.assertIsNone(r['value'])
 async def test_timeout_and_stdout_limit(self):
  r=await execute('def solve(problem):\n while True: pass\n','{}',.1);self.assertEqual(r['error_type'],'CODE_TIMEOUT')
  r=await execute('def solve(problem):\n print("x"*100000)\n return 1\n','{}',2);self.assertEqual(r['error_type'],'CODE_RESOURCE_LIMIT')
 async def test_fresh_process_no_unauthorized_cache(self):
  code='def solve(problem):\n x=[]\n x.append(1)\n return len(x)\n'
  self.assertEqual((await execute(code,'{}',2))['value'],1);self.assertEqual((await execute(code,'{}',2))['value'],1)
 async def test_hidden_read_and_credentials_not_exposed(self):
  with tempfile.TemporaryDirectory() as d:
   sentinel=Path(d)/'hidden_answer.json';sentinel.write_text('SECRET_SENTINEL')
   r=await execute('def solve(problem):\n return open('+repr(str(sentinel))+').read()\n','{}',2)
   self.assertEqual(r['error_type'],'CODE_CONTRACT_ERROR');self.assertNotIn('SECRET_SENTINEL',json.dumps(r))

class ProvenanceTests(unittest.TestCase):
 def test_correct_operator_overwritten(self):
  p=Provenance('t','r');p.tool('operator',envelope({'status':'success','value':4,'elapsed_seconds':.1},'op','1'));p.tool('python',envelope({'status':'success','value':0,'elapsed_seconds':.1},'python','1'));p.submit(0)
  e=evaluate(p.record(),4);self.assertTrue(e['operator_result_overridden']);self.assertFalse(e['correct']);self.assertFalse(e['direct_operator_adoption'])
 def test_wrong_operator_recovered_and_errors_preserved(self):
  p=Provenance('t','r');p.tool('operator',envelope({'status':'success','value':0,'elapsed_seconds':.1},'op','1'));p.tool('python',envelope({'status':'error','error':'ValueError: boom','elapsed_seconds':.1},'python','1'));p.tool('python',envelope({'status':'success','value':4,'elapsed_seconds':.1},'python','1'));p.submit(4)
  e=evaluate(p.record(),4);self.assertTrue(e['correct']);self.assertTrue(e['recovered_wrong_operator']);self.assertEqual(len(e['events']),5)
 def test_infrastructure_not_math_and_unknown_preserved(self):
  p=Provenance('t','r');p.failure('request','INFRASTRUCTURE_ERROR');p.failure('unknown','UNKNOWN');p.submit(None)
  e=evaluate(p.record(),4);self.assertIn('INFRASTRUCTURE_ERROR',[x.get('error_type') for x in e['events']]);self.assertIn('UNKNOWN',[x.get('error_type') for x in e['events']])

class RunnerTests(unittest.IsolatedAsyncioTestCase):
 async def test_unaccepted_operator_cannot_enter_agent(self):
  from scripts.operator_runtime.runner import solve
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):await solve(Path(d),None,{'id':'x','problem':'{}','expected':4},'operator','fixture',BAD)
 async def test_real_registered_runner_overwrite_and_recovery(self):
  from scripts.operator_runtime.runner import solve
  class MockTransport:
   def __init__(self,python_code,answer):self.steps=[('operator',{}),('python',{'code':python_code}),('submit',{'value':answer})];self.messages=[]
   async def call(self,key,messages,cap,**kwargs):
    self.messages.append(json.dumps(messages));name,args=self.steps.pop(0)
    return dict(usage={'input_tokens':1,'output_tokens':1},message={'tool_calls':[{'id':str(len(self.steps)),'function':{'name':name,'arguments':json.dumps(args)}}]})
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);reg=Registry(root/'registry');proto=LifecycleTests.protocol(self)
   await reg.admit('double','1',GOOD,proto)
   t=MockTransport('def solve(problem):\n return 0\n',0)
   row={'id':'engineering_overwrite','problem':'{"value":2}','expected':4}
   r=await solve(root/'overwrite',t,row,'operator','value times2',registry=reg,operator_id='double',version='1')
   self.assertTrue(r['operator_result_overridden']);self.assertFalse(r['correct']);self.assertNotIn('expected',t.messages[0]);self.assertNotIn('reference answer: 4',''.join(t.messages))
   # Accepted historical tests do not guarantee universal correctness: new input exposes counterexample.
   partial=GOOD.replace('return json.loads(problem)["value"] * 2','return json.loads(problem)["value"] * 2 if json.loads(problem)["value"] != 99 else 0')
   await reg.admit('partial','1',partial,proto)
   t=MockTransport('def solve(problem):\n return 198\n',198)
   r=await solve(root/'recover',t,{'id':'engineering_recovery','problem':'{"value":99}','expected':198},'operator','value times2',registry=reg,operator_id='partial',version='1')
   self.assertTrue(r['correct']);self.assertTrue(r['recovered_wrong_operator'])
 async def test_direct_runner_bad_arguments_classified(self):
  from scripts.operator_runtime.runner import solve
  class Mock:
   async def call(self,*a,**kw):return dict(usage={'output_tokens':1},message={'tool_calls':[{'id':'1','function':{'name':'python','arguments':'{"code": 3}'}}]})
  with tempfile.TemporaryDirectory() as d:
   r=await solve(Path(d),Mock(),{'id':'arg','problem':'{}','expected':4},'reflection','fixture')
   self.assertEqual(r['error'],'tool_argument_error');self.assertIn('TOOL_ARGUMENT_ERROR',[e.get('error_type') for e in r['events']])

class NativeIsolationTests(unittest.IsolatedAsyncioTestCase):
 async def test_os_blocks_hidden_read_write_network_without_ast(self):
  import subprocess,sys,os
  from scripts.operator_runtime.executor import sandbox_profile
  with tempfile.TemporaryDirectory() as outside,tempfile.TemporaryDirectory() as inside:
   secret=Path(outside)/'hidden.json';secret.write_text('synthetic-secret-not-a-real-answer')
   target=Path(outside)/'history.json';target.write_text('immutable')
   code='import os,socket,json\nr={}\n'
   code+='try:\n open('+repr(str(secret))+').read();r["read_blocked"]=False\nexcept PermissionError:r["read_blocked"]=True\n'
   code+='try:\n open('+repr(str(target))+',"w").write("corrupted");r["write_blocked"]=False\nexcept PermissionError:r["write_blocked"]=True\n'
   code+='try:\n socket.socket().connect(("127.0.0.1",9));r["network_blocked"]=False\nexcept PermissionError:r["network_blocked"]=True\n'
   code+='r["credential_missing"]=os.getenv("ENGINEERING_SECRET") is None\nprint(json.dumps(r))'
   env={'PATH':os.defpath,'LANG':'C.UTF-8'}
   from unittest.mock import patch
   with patch.dict(os.environ,{'ENGINEERING_SECRET':'synthetic-parent-only'}):
    proc=await asyncio.create_subprocess_exec('/usr/bin/sandbox-exec','-p',sandbox_profile(inside),sys.executable,'-I','-B','-c',code,cwd=inside,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env)
   out,err=await asyncio.wait_for(proc.communicate(),5)
   self.assertEqual(proc.returncode,0,err.decode());facts=json.loads(out);self.assertTrue(all(facts.values()),facts);self.assertEqual(target.read_text(),'immutable')
 async def test_native_isolation_missing_is_fail_closed(self):
  from unittest.mock import patch
  with patch('scripts.operator_runtime.executor.sys.platform','unsupported'):
   r=await execute(BAD,'{}',2)
  self.assertEqual(r['error_type'],'TOOL_INTEGRATION_ERROR');self.assertIsNone(r['value'])
 async def test_memory_recursion_and_large_exact_integer(self):
  from scripts.operator_runtime.executor import Resources
  r=await execute('def solve(problem):\n x=[1]*20000000\n while True: pass\n','{}',2,resources=Resources(memory_bytes=32*1024**2))
  self.assertEqual(r['error_type'],'CODE_RESOURCE_LIMIT',r)
  r=await execute('def solve(problem):\n return solve(problem)\n','{}',2);self.assertEqual(r['error_type'],'CODE_RESOURCE_LIMIT')
  r=await execute('def solve(problem):\n return 2**256\n','{}',2);self.assertEqual(r['value'],2**256)

class AccountingAndIsolationTests(unittest.IsolatedAsyncioTestCase):
 async def test_cost_cap_stops_before_request_and_unknown_is_reserved(self):
  with tempfile.TemporaryDirectory() as d:
   api=FakeAPI(['{}']);c=RetryController(Path(d),api,limits=Limits(max_requests=3,max_cost_cny=0))
   with self.assertRaises(InfrastructureInterrupted):await c.request('x',{},8192)
   self.assertFalse(api.calls)
 async def test_concurrent_distinct_calls_share_request_cap(self):
  with tempfile.TemporaryDirectory() as d:
   api=FakeAPI(['{}','{}']);c=RetryController(Path(d),api,limits=Limits(max_requests=1,max_cost_cny=1))
   rs=await asyncio.gather(c.request('a',{},10),c.request('b',{},10),return_exceptions=True)
   self.assertEqual(len(api.calls),1);self.assertEqual(sum(isinstance(x,InfrastructureInterrupted) for x in rs),1)
 async def test_online_retry_reserves_unknown_output_against_same_task(self):
  with tempfile.TemporaryDirectory() as d:
   api=FakeAPI([ConnectionFault(),'{}']);c=RetryController(Path(d),api,sleep=lambda _:asyncio.sleep(0))
   with self.assertRaises(InfrastructureInterrupted):await c.request('pilot/task/operator/0',{},7680,phase='online')
   self.assertEqual(len(api.calls),1);self.assertEqual(c.usage()['unknown_usage_requests'],1)
 async def test_client_error_not_retried_and_server_error_bounded(self):
  class ClientFault(Exception):status_code=400
  class ServerFault(Exception):status_code=503
  for ex,n in [(ClientFault(),1),(ServerFault(),3)]:
   with tempfile.TemporaryDirectory() as d:
    api=FakeAPI([ex]*3);c=RetryController(Path(d),api,sleep=lambda _:asyncio.sleep(0))
    with self.assertRaises(InfrastructureInterrupted):await c.request('x',{},10)
    self.assertEqual(len(api.calls),n)
 async def test_runtime_validator_statuses_are_explicit(self):
  r=await execute(BAD,'{}',2,runtime_validator=lambda p,v:{'verified':True,'passed':True});self.assertEqual(r['validation_status'],'VERIFIED')
  r=await execute(BAD,'{}',2,runtime_validator=lambda p,v:{'verified':False,'passed':False});self.assertEqual(r['validation_status'],'FAILED');self.assertIsNone(r['result'])
  r=await execute(BAD,'{}',2,runtime_validator=lambda p,v:1/0);self.assertEqual(r['validation_status'],'UNAVAILABLE')

class EventAndHistoryTests(unittest.TestCase):
 def test_event_redaction_and_error_taxonomy(self):
  with tempfile.TemporaryDirectory() as d:
   event=EventLog(Path(d)).emit('api','request','FAILED',error_type='UNKNOWN',api_key='not-for-logs',authorization='Bearer sensitive')
   self.assertNotIn('not-for-logs',json.dumps(event));self.assertNotIn('Bearer sensitive',json.dumps(event))
   with self.assertRaises(ValueError):EventLog(Path(d)).emit('mechanism','error','FAILED',error_type='systematic_model_defect')
 def test_reused_discovery_task_cannot_be_hidden_again(self):
  from scripts.operator_runtime.history import TaskRoles
  with tempfile.TemporaryDirectory() as d:
   roles=TaskRoles(Path(d)/'roles.json');roles.add('{}','DISCOVERY','old','history')
   with self.assertRaises(ValueError):roles.add('{}','HIDDEN_TEST','new','history')
 def test_readonly_historical_counts_match_without_running_models(self):
  from scripts.operator_runtime.history import replay
  with tempfile.TemporaryDirectory() as d:
   details,diff=replay(Path(d));self.assertEqual(details['A']['observations'],720);self.assertEqual(details['B0']['observations'],132);self.assertFalse(diff);self.assertGreater(details['A']['unknown_labels_retained'],0)

class StatisticsTests(unittest.TestCase):
 def test_execution_function_and_final_success_differ(self):
  from scripts.operator_runtime.statistics import summarize
  p=Provenance('x','r');p.tool('operator',envelope({'status':'success','value':0},'o','1'));p.tool('python',envelope({'status':'success','value':4},'p','1'));p.submit(4);r=evaluate(p.record(),4)
  s=summarize([r],[{'known_cost_cny':.1,'phase':'online','usage':None}]);self.assertEqual(s['operator_execution_success'],1);self.assertEqual(s['operator_functional_correct'],0);self.assertEqual(s['agent_final_correct'],1);self.assertEqual(s['cost_per_correct_known_estimate_cny'],.1);self.assertEqual(s['unknown_usage_requests'],1)
 def test_fast_wrong_answer_is_not_cost_efficiency(self):
  from scripts.operator_runtime.statistics import summarize
  s=summarize([{'correct':False,'events':[]}]);self.assertIsNone(s['cost_per_correct_known_estimate_cny']);self.assertFalse(s['efficiency_claim_allowed'])

class AdditionalBoundaryTests(unittest.IsolatedAsyncioTestCase):
 async def test_cpu_limit_and_invalid_parameters(self):
  from scripts.operator_runtime.executor import Resources
  r=await execute('def solve(problem):\n while True: pass\n','{}',4,resources=Resources(cpu_seconds=1));self.assertEqual(r['error_type'],'CODE_RESOURCE_LIMIT',r)
  r=await execute(BAD,{},2);self.assertEqual(r['error_type'],'TOOL_ARGUMENT_ERROR')
  for value in ['-1','True']:
   r=await execute('def solve(problem):\n return '+value+'\n','{}',2);self.assertEqual(r['error_type'],'CODE_CONTRACT_ERROR')
 async def test_no_fake_verified_by_admission_or_bad_runtime_callback(self):
  r=await execute(BAD,'{}',2,runtime_validator=lambda p,v:{'passed':True});self.assertEqual(r['validation_status'],'UNAVAILABLE')
 async def test_same_logical_key_never_calls_api_twice(self):
  with tempfile.TemporaryDirectory() as d:
   a=FakeAPI(['{}']);b=FakeAPI(['{}']);c=RetryController(Path(d),a);e=RetryController(Path(d),b)
   await asyncio.gather(c.request('one',{},10),e.request('one',{},10),return_exceptions=True)
   self.assertEqual(len(a.calls)+len(b.calls),1)
 async def test_strict_unverified_policy_requires_recomputation(self):
  from scripts.operator_runtime.runner import solve
  class Mock:
   def __init__(self):self.calls=0
   async def call(self,*args,**kw):
    self.calls+=1;name='operator' if self.calls==1 else 'submit';args={} if name=='operator' else {'value':4}
    return dict(usage={'input_tokens':1,'output_tokens':1},message={'tool_calls':[{'id':str(self.calls),'function':{'name':name,'arguments':json.dumps(args)}}]})
  with tempfile.TemporaryDirectory() as d:
   reg=Registry(Path(d)/'registry');await reg.admit('double','1',GOOD,LifecycleTests.protocol(self))
   r=await solve(Path(d)/'run',Mock(),{'id':'strict','problem':'{"value":2}','expected':4},'operator','double',registry=reg,operator_id='double',version='1',unverified_policy='require_recompute')
   self.assertEqual(r['error'],'unverified_output_policy_violation');self.assertFalse(r['submitted'])
 async def test_seeded_properties_metamorphic_and_independent_crosscheck(self):
  import random
  rng=random.Random(20261015);xs=[rng.randint(0,10**15) for _ in range(8)];xs+=[0,2**130]
  cases=LifecycleTests.protocol(self).cases
  for x in xs:
   # Exact closed form independent of executing operator; also verify additive identity.
   expected=x+x;self.assertEqual(expected,2*x)
   cases.extend([ValidationCase('property',{'value':x},expected=expected,metadata={'seed':20261015}),ValidationCase('metamorphic',{'value':x+1},expected=expected+2,metadata={'transformation':'x+1','output_delta':2})])
  with tempfile.TemporaryDirectory() as d:
   reg=Registry(Path(d));r=await reg.admit('double','property',GOOD,ValidationProtocol('property-v1','seed-20261015',cases));self.assertEqual(r['state'],'REGISTERED')

class IntegrationGuardTests(unittest.IsolatedAsyncioTestCase):
 async def test_cannot_unquarantine_without_new_version(self):
  with tempfile.TemporaryDirectory() as d:
   reg=Registry(Path(d));await reg.admit('double','1',GOOD,LifecycleTests.protocol(self));reg.quarantine('double','1','incident')
   with self.assertRaises(ValueError):reg.transition(reg.path('double','1'),'REGISTERED')
 async def test_injected_sdk_retries_cannot_escape_accounting(self):
  from scripts.operator_runtime.api import Transport
  class SDK:max_retries=2
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'manifest.json').write_text(json.dumps({'runtime_version':'operator-runtime-v1','model_alias':'mock','max_requests':10,'api_budget_cny':1}))
   with self.assertRaises(ValueError):Transport(root,client=SDK())

class RepairBoundaryTests(unittest.IsolatedAsyncioTestCase):
 async def test_delivered_malformed_repair_consumes_repair_not_network_retry(self):
  with tempfile.TemporaryDirectory() as d:
   api=FakeAPI([json.dumps({'code':BAD}),'not-json']);c=RetryController(Path(d),api)
   r=await synthesize(c,[],lambda _:asyncio.sleep(0,result={'passed':False}))
   self.assertEqual(r['initial_generation_attempts'],1);self.assertEqual(r['code_repair_attempts'],1);self.assertEqual(r['model_generation_attempts'],1);self.assertEqual(r['status'],'MODEL_RESPONSE_ERROR');self.assertEqual(len(list((Path(d)/'generations').glob('*.json'))),1)
 async def test_infrastructure_failure_during_repair_keeps_real_source_and_slot(self):
  with tempfile.TemporaryDirectory() as d:
   api=FakeAPI([json.dumps({'code':BAD})]+[ConnectionFault()]*3);c=RetryController(Path(d),api,sleep=lambda _:asyncio.sleep(0))
   r=await synthesize(c,[],lambda _:asyncio.sleep(0,result={'passed':False}))
   self.assertEqual(r['code'],BAD);self.assertEqual(r['initial_generation_attempts'],1);self.assertEqual(r['code_repair_attempts'],0);self.assertEqual(r['api_request_attempts'],4);self.assertEqual(r['status'],'INFRASTRUCTURE_INTERRUPTED')

class ArchiveGuardTests(unittest.TestCase):
 def test_stable_writers_refuse_archived_tree_without_any_new_files(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'manifest.json').write_text(json.dumps({'experiment':'historical','model_alias':'mock'}));before=list(root.rglob('*'))
   for action in [lambda:EventLog(root/'new-child'),lambda:Registry(root/'registry'),lambda:RetryController(root,FakeAPI([]))]:
    with self.assertRaisesRegex(ValueError,'Historical'):action()
   self.assertEqual(list(root.rglob('*')),before)
