"""Final critical integration gates. Reads prior evidence; no adaptive reference search."""
import asyncio,io,json,pickle,time
from pathlib import Path
from scripts.experiment_d0_s01 import ROOT,OLD,verify
from scripts.d0_s01_runtime import execute_case,admit
from scripts.d0_s01_runtime.evaluation import evaluate_task,EngineeringSession
from scripts.d0_s01_runtime.contracts import taco_task
from scripts.d0_dataset_audit import DataOnlyUnpickler
from scripts.offline_loop_protocol import atomic_json,file_hash
async def main():
 verify()
 if (ROOT/'critical_integration_results.json').exists():raise FileExistsError('No rerun')
 atomic_json(ROOT/'critical_integration_protocol.json',dict(checks=['fixed13_conflicts_quarantined','taco_valid_function_wrapper_positive_negative','taco_admitted_real_program_positive_negative','lcb_six_real_programs_positive_negative','all_original18_reviewed','invalid_tests_not_Agent_failures','special_judge_and_ambiguous_wrapper_rejected','judge_error_not_wrong_answer','evaluator_budget_not_algorithm_TLE','hidden_context_capability_boundary','unsafe_pickle_rejected','full_release_hashes_and_dedup_prepared','complete_regression_and_resources'],API=0,script_sha256=file_hash(Path(__file__))))
 results=[]
 def add(name,passed,details):results.append(dict(name=name,passed=bool(passed),details=details))
 fixed=json.loads((ROOT/'taco11_final_audit.json').read_text());add('fixed13_conflicts_quarantined',fixed['status']=='DATA_CONTRACT_UNRESOLVED' and len(fixed['proof'])==13 and all(x['rechecked'] for x in fixed['proof']),dict(status=fixed['status'],conflicts=13))
 t=json.loads((OLD/'taco_parsed_admitted.json').read_text())['taco-test-40'];task=taco_task('taco-test-40',t);task['cases']=task['cases'][:2];source=json.loads((OLD/'taco_references.json').read_text())['taco-test-40'][1]
 # Declared public examples362/6063 are independently enumerable; whole task stays quarantined.
 import itertools
 expected=[]
 for case in task['cases']:
  num=case['args'][0];vals=set()
  for n in range(1,len(str(num))+1):
   for ds in itertools.permutations(str(num),n):
    if ds[0]!='0':
     value=int(''.join(ds))
     if value%3==0:vals.add(value)
  expected.append([len(vals),max(vals)])
 assert all(c['expected']==[e] for c,e in zip(task['cases'],expected))
 a=await evaluate_task(source,task,log_dir=ROOT/'integration_logs');w=await evaluate_task('def find_mult_3(num):return [[4,63]]',task,log_dir=ROOT/'integration_logs');add('taco_valid_function_wrapper_positive_negative',a['verdict']=='PASS' and w['verdict']=='WRONG_ANSWER',dict(scope='Two valid public examples only; entire S1 candidate40 still excluded',independent_expected=expected,correct=a,wrong_extra_wrapping=w))
 candidates=json.loads((ROOT/'candidate_admission_final.json').read_text());accepted=[c for c in candidates if c['status']=='ADMITTED'];tacos=[c for c in accepted if c['source']=='TACO'];lcbs=[c for c in accepted if c['source'].startswith('LiveCodeBench')]
 add('taco_admitted_real_program_positive_negative',bool(tacos) and all(any(a['evaluation']['verdict']=='PASS' and a['evaluation']['all_cases_executed'] for a in x['reference_attempts']) and x['wrong_program_check']['verdict']=='WRONG_ANSWER' for x in tacos),dict(admitted=[x['id'] for x in tacos],evidence='candidate_admission_final.json'))
 add('lcb_six_real_programs_positive_negative',len(lcbs)==6 and all(x['reference_check']['verdict']=='PASS' and x['reference_check']['all_cases_executed'] and x['wrong_program_check']['verdict']=='WRONG_ANSWER' for x in lcbs),dict(admitted=[x['id'] for x in lcbs]))
 add('all_original18_reviewed',len(candidates)==18 and all('contract_review' in x for x in candidates),dict(admitted=len(accepted),excluded=[x['id'] for x in candidates if x['status']!='ADMITTED']))
 add('invalid_tests_not_Agent_failures',all(x['status']=='EXCLUDED' for x in candidates if x['contract_review']['invalid_input_cases']),dict(no_Agent_executions=True,conflicting_ids=[x['id'] for x in candidates if x['contract_review']['invalid_input_cases']]))
 spj=admit({'interface':{'mode':'stdin'},'judge':{'mode':'nonunique'},'cases':[{'expected':'a'}]});wrapper=admit({'interface':{'mode':'function','function':'f'},'judge':{'mode':'taco_single_return','return_schema':'int_pair'},'cases':[{'expected':[[1,2],[3,4]]}]});add('special_judge_and_ambiguous_wrapper_rejected',not spj['admitted'] and not wrapper['admitted'],dict(special_judge=spj,ambiguous=wrapper))
 r=await execute_case('print(1)',{'mode':'stdin'},{'expected':'1\n'},{'mode':'exact','invalid_judge_option':True},log_dir=ROOT/'integration_logs');add('judge_error_not_wrong_answer',r['verdict']=='JUDGE_ERROR',r)
 r=await evaluate_task('print(1)',{'interface':{'mode':'stdin'},'cases':[{'expected':'1\n'}]},task_wall=0);add('evaluator_budget_not_algorithm_TLE',r['verdict']=='EVALUATOR_BUDGET_EXHAUSTED' and not r['results'],r)
 task={'id':'mock','source':'engineering','prompt':'Increment input','interface':{'mode':'function','function':'solve'},'cases':[{'args':[1],'expected':2}],'reference':'SECRET_SOURCE'};session=EngineeringSession(task,ROOT/'integration_logs');pub=session.model_input();v=await session.self_test('def solve(x):return 0',{'args':[1],'expected':2,'role':'hidden','reference':'ATTEMPT'});session.submit('def solve(x):return x+1');h=await session.final_evaluate();denied=[]
 for fn in [session.model_input,lambda:session.submit('pass')]:
  try:fn();denied.append(False)
  except ValueError:denied.append(True)
 try:await session.self_test('pass',{});denied.append(False)
 except ValueError:denied.append(True)
 add('hidden_context_capability_boundary','cases' not in pub and 'reference' not in pub and 'verdict' not in v and v['value']==0 and h['verdict']=='PASS' and all(denied),dict(public=pub,selftest=v,hidden=h,continuation_denied=denied,online_tool_seconds=session.tool_seconds))
 class Malicious:
  def __reduce__(self):return (print,('SHOULD_NOT_EXECUTE',))
 rejected=False
 try:DataOnlyUnpickler(io.BytesIO(pickle.dumps(Malicious()))).load()
 except ValueError:rejected=True
 add('unsafe_pickle_rejected',rejected,dict(rejected=rejected))
 pool=json.loads((ROOT/'full_pool_audit.json').read_text());add('full_release_hashes_and_dedup_prepared',pool['LCB_rows']==1055 and pool['all6_content_hashes_verified'] and pool['dedup_edges']>=0,dict(release=pool['release'],rows=pool['LCB_rows'],cross_groups=pool['cross_source_group_count']))
 reg=json.loads((ROOT/'regression_gate.json').read_text());small=json.loads((ROOT/'reference_small_results.json').read_text());add('complete_regression_and_resources',reg['passed'] and all(x['passed'] for x in small),dict(regression=reg,independent_small_checks=len(small)))
 atomic_json(ROOT/'critical_integration_results.json',results);gate=all(x['passed'] for x in results) and len(accepted)>=9 and len({x['source'] for x in accepted})==3
 atomic_json(ROOT/'acceptance_gate.json',dict(status='S0_ACCEPTED' if gate else 'S0_NOT_ACCEPTED',critical_passed=sum(x['passed'] for x in results),critical_total=len(results),admitted_original_candidates=len(accepted),scope='Only13 admitted original tasks, not excluded5, not original balanced18, not full540. No API authorization.',API=0));print([(x['name'],x['passed']) for x in results],flush=True);print('S0_ACCEPTED' if gate else 'S0_NOT_ACCEPTED',flush=True)
if __name__=='__main__':asyncio.run(main())
