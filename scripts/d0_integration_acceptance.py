"""Supplementary frozen S0 integration acceptance on actual source formats."""
import asyncio,json,time
from pathlib import Path
from scripts.d0_programming_runtime.evaluation import evaluate_task,EngineeringSession
from scripts.offline_loop_protocol import atomic_json,file_hash
ROOT=Path('results/experiment_d0_20261019_s0_v1')
async def main():
 if (ROOT/'integration_results.json').exists():raise FileExistsError('No rerun')
 sourcefiles=list(Path('scripts/d0_programming_runtime').glob('*.py'))+[Path(__file__)]
 atomic_json(ROOT/'integration_protocol.json',dict(source_hashes={str(p):file_hash(p) for p in sourcefiles},API_cap_cny=0,checks=['multi_case_all_pass','multi_case_wrong_answer','actual_A_reference','actual_TACO_reference','actual_LCB_reference','mock_selftest_submit_hidden_boundary'],actual_source_checks='S0 format/reference subset only; not full dataset reference validation or Agent API smoke',no_historical_source_edits=True))
 results=[]
 def add(name,passed,details):results.append(dict(name=name,passed=bool(passed),details=details))
 task={'interface':{'mode':'function','function':'solve'},'cases':[{'args':[2],'expected':4},{'args':[3],'expected':6}]}
 r=await evaluate_task('def solve(x):return x*2',task,log_dir=ROOT/'integration_logs');add('multi_case_all_pass',r['verdict']=='PASS' and r['all_cases_executed'],r)
 r=await evaluate_task('def solve(x):return 4',task,log_dir=ROOT/'integration_logs');add('multi_case_wrong_answer',r['verdict']=='WRONG_ANSWER',r)
 # Trusted existing exact reference used only offline, never supplied to candidate/model input.
 from scripts.compositional_counting import exact_dp,brute_force
 A=json.loads((ROOT/'dataset_A_smoke.json').read_text());check=[dict(id=t['id'],dp=exact_dp(t['problem'])['answer'],brute=brute_force(t['problem'])) for t in A];add('actual_A_reference',all(x['dp']==x['brute'] for x in check),check)
 taco=json.loads((ROOT/'taco_parsed_admitted.json').read_text());refs=json.loads((ROOT/'taco_references.json').read_text());inventory=json.loads((ROOT/'dataset_inventory.json').read_text())
 # Select by public metadata before executing references; no replacement on failure.
 tid=min(x['id'] for x in inventory if x['source']=='TACO' and x['original_difficulty']=='EASY' and x['admitted_metadata'] and x['interface']=='stdin');t=taco[tid]
 cases=[dict(stdin=i,expected=o) for i,o in zip(t['io']['inputs'][:3],t['io']['outputs'][:3])]
 r=await evaluate_task(refs[tid][0],{'interface':{'mode':'stdin'},'judge':{'mode':'tokens'},'cases':cases},log_dir=ROOT/'integration_logs');add('actual_TACO_reference',r['verdict']=='PASS',dict(id=tid,reference_sha256=__import__('hashlib').sha256(refs[tid][0].encode()).hexdigest(),limited_to_first_cases=3,available_cases=len(t['io']['inputs']),evaluation=r))
 public=json.loads((ROOT/'lcb_public_tasks.json').read_text());private=json.loads((ROOT/'lcb_private_cases.json').read_text());lid='lcb-v6-abc387_b';t=public[lid]
 # abc387_b: sum of 1..9 multiplication table with one supplied value removed =2025-x.
 code='print(2025-int(input()))';cases=[dict(stdin=x['input'],expected=x['output']) for x in private[lid]]
 r=await evaluate_task(code,{'interface':{'mode':'stdin'},'judge':{'mode':'tokens'},'cases':cases},log_dir=ROOT/'integration_logs');add('actual_LCB_reference',r['verdict']=='PASS',dict(id=lid,reference='Independent arithmetic identity sum(i*j,i,j=1..9)=2025',evaluation=r))
 task={'id':'mock','source':'engineering','prompt':'Double input','interface':{'mode':'function','function':'solve'},'cases':[{'args':[3],'expected':6}],'reference':'NOT_FOR_MODEL','public_examples':[]}
 session=EngineeringSession(task,ROOT/'integration_logs');pub=session.model_input();visible=await session.self_test('def solve(x):return 0',{'args':[3],'expected':6});session.submit('def solve(x):return x*2');r=await session.final_evaluate();denials=[]
 for action in [lambda:session.model_input(),lambda:session.submit('def solve(x):return 0')]:
  try:action();denials.append(False)
  except ValueError:denials.append(True)
 try:await session.self_test('def solve(x):return 6',{'args':[3]});denials.append(False)
 except ValueError:denials.append(True)
 add('mock_selftest_submit_hidden_boundary','cases' not in pub and 'reference' not in pub and 'verdict' not in visible and visible.get('value')==0 and r['verdict']=='PASS' and all(denials),dict(public_task=pub,self_test_feedback=visible,hidden_result=r,continuation_denied=denials,online_tool_seconds=session.tool_seconds))
 atomic_json(ROOT/'integration_results.json',results);atomic_json(ROOT/'integration_gate.json',dict(passed=all(x['passed'] for x in results),passed_checks=sum(x['passed'] for x in results),checks=len(results),API_calls=0));print([(x['name'],x['passed']) for x in results],flush=True)
if __name__=='__main__':asyncio.run(main())
