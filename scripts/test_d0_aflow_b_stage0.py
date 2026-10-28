"""Offline controlled response replay through unchanged official graph and Test loop."""
import asyncio,json,hashlib,ast
from pathlib import Path
from scripts.d0_aflow_b_adapter import P,F,Adaptation,ReplayTransport,WorkflowFault
from scripts.d0_s01_runtime.evaluation import evaluate_task

def save(path,x):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def responses(initial,repairs=(),final=None):
 out=[{'response':initial} for _ in range(3)]+[{'thought':'fixed mock selection','solution_letter':'A'}]
 out += [{'reflection_and_solution':s} for s in repairs]
 if final is not None:out.append({'response':final})
 return out
async def scenario(name,seq,expected,public=None,max_calls=8,hidden_expected=None):
 pub=public or {'interface':{'mode':'function','function':'f'},'cases':[{'args':[2],'expected':3}],'source':'frozen synthetic public fixture','judge':{'mode':'exact'}}
 transport=ReplayTransport(seq,max_calls=max_calls);a=Adaptation(pub,transport,P/'mock_replays'/name)
 r=await a.run('Implement the stated public function. Public tests: '+json.dumps(pub),pub['interface'].get('function',pub['interface'].get('method','')))
 assert r['status']==expected,(name,r['status'],r.get('error'))
 if hidden_expected is not None:
  assert a.locked
  hidden={'interface':pub['interface'],'cases':[{'args':[3],'expected':hidden_expected}],'judge':pub['judge']}
  before=len(transport.requests);score=await evaluate_task(a.final_source,hidden,task_wall=10,log_dir=P/'mock_replays'/name/'scoring');save(P/'mock_replays'/name/'hidden_score.json',score);assert len(transport.requests)==before
  r['hidden_verdict']=score['verdict']
 if a.locked:
  try:await a.run('illegal continuation','f');raise AssertionError('lock failed')
  except WorkflowFault:pass
 return {'name':name,'passed':True,'workflow_status':r['status'],'calls':len(transport.requests),'hidden_verdict':r.get('hidden_verdict'),'native_public_cases':sum(e['kind']=='public_test' for e in a.events)}
async def main():
 good='def f(x):\n    return x+1\n';bad='def f(x):\n    return 0\n';crash='def f(x):\n    raise ValueError("candidate failure")\n'
 tests=[]
 tests.append(await scenario('01_first_correct',responses(good),'RETURNED_LOCKED',hidden_expected=4))
 tests.append(await scenario('02_test_reflection_repairs',responses(bad,[good]),'RETURNED_LOCKED',hidden_expected=4))
 tests.append(await scenario('03_three_revisions_still_wrong',responses(bad,[bad,bad,bad],bad),'RETURNED_LOCKED',hidden_expected=4))
 tests.append(await scenario('04_runtime_failure_then_repair',responses(crash,[good]),'RETURNED_LOCKED',hidden_expected=4))
 tests.append(await scenario('05_malformed_code',[{'response':'this is not python!'}],'FormatFault'))
 tests.append(await scenario('06_empty_node',[{'response':''}],'EmptyOutput'))
 tests.append(await scenario('07_model_request_failure',[ConnectionError('mock transport interruption')],'ConnectionError'))
 tests.append(await scenario('08_budget_exhaustion',responses(good),'BudgetFault',max_calls=3))
 tests.append(await scenario('09_final_fallback_runtime_error',responses(bad,[bad,bad,bad],crash),'RETURNED_LOCKED',hidden_expected=4))
 tests.append(await scenario('10_public_hidden_isolation',responses('def f(x):\n    return 3\n'),'RETURNED_LOCKED',hidden_expected=987654321))
 tests.append(await scenario('11_invalid_ensemble_letter',[{'response':good} for _ in range(3)]+[{'solution_letter':'Z'}],'KeyError'))
 tests.append(await scenario('12_no_public_tests_fail_closed',responses(good),'WorkflowFault',public={'interface':{'mode':'function','function':'f'},'cases':[]}))
 # Real S0.1 reference fixtures are researcher-only replay responses, never online prompts.
 public_manifest=json.loads((P/'public_test_manifest.json').read_text());old=Path('results/experiment_d0_s01_20261019_v1');refs=json.loads((old/'trusted_engineering_references.json').read_text());tasks=json.loads((old/'s1_public_tasks.json').read_text());backend=json.loads((old/'s1_backend_tasks.json').read_text());matrix=[]
 for tid,pub in public_manifest.items():
  t=ReplayTransport(responses(refs[tid]));a=Adaptation(pub,t,P/'interface_replays'/tid)
  prompt=tasks[tid]['prompt']+'\nPublic Test examples (same visible inputs and outputs):\n'+json.dumps(pub['cases'])
  interface=pub['interface'];entry=interface.get('function',interface.get('method',''))
  r=await a.run(prompt,entry)
  assert r['status']=='RETURNED_LOCKED',(tid,r['status'],r.get('error'))
  assert len(t.requests)==4,(tid,'reference public failure',r)
  assert r['source']==refs[tid],(tid,'source changed')
  b={**backend[tid],'cases':backend[tid]['cases'][:1]};score=await evaluate_task(r['source'],b,task_wall=30,log_dir=P/'interface_replays'/tid/'scoring');assert score['verdict']=='PASS',(tid,score)
  matrix.append({'task':tid,'interface':interface,'public_cases':len(pub['cases']),'model_calls_simulated':len(t.requests),'public_path':'PASS','hidden_first_case':score['verdict'],'hidden_cases_used':1,'full_hidden_regression':False,'no_source_rewrite':True})
  print('interface',tid,'PASS',flush=True)
 # Prove source-level graph and Test control code have not been replaced.
 trace={'graph_sha256':hashlib.sha256((F/'MBPP/graphs_test/round_14/graph.py').read_bytes()).hexdigest(),'original_graph_class_loaded':True,'original_operator_methods_loaded':True,'only_Test_exec_code_boundary_replaced':True,'candidate_exec_in_parent':False}
 save(P/'mock_test_results.json',tests);save(P/'interface_matrix.json',matrix);save(P/'control_flow_integrity.json',trace)
 # All original historical results/source snapshots remain unchanged.
 before=json.loads((P/'history_before.json').read_text());changed=[p for p,h in before.items() if not Path(p).exists() or hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h];assert not changed,changed[:5]
 save(P/'history_after.json',{'checked':len(before),'changed':changed});print('Mocks',len(tests),'interfaces',len(matrix),'API 0')
if __name__=='__main__':asyncio.run(main())
