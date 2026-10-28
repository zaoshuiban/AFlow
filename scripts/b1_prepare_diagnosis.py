"""Assisted mechanism evidence + presealed B1 counterfactual tasks, no synthesis."""
import ast,copy,difflib,json,random,shutil
from pathlib import Path
from scripts.experiment_b1 import ROOT,A,B0,verify,callable_only,counts
from scripts.b1_detection import detect
from scripts.compositional_counting import exact_dp,brute_force
from scripts.offline_loop_protocol import atomic_json,read_json,digest,file_hash
from scripts.operator_runtime.history import TaskRoles


def prepare():
 verify()
 if (ROOT/'diagnosis_plan.json').exists():raise FileExistsError('Already sealed')
 tasks=read_json(ROOT/'inputs/natural_discovery.json');by={t['id']:t for t in tasks};observations=[];events=[]
 archive=ROOT/'detector_v0';archive.mkdir(exist_ok=True)
 for name in ('normalized_historical_events.json','local_failure_candidates.json','verified_local_failures.json','historical_statistics.json'):
  shutil.copyfile(ROOT/name,archive/name)
 for t in tasks:
  obs=read_json(ROOT/'natural_trajectories'/f"{t['id']}.json")['observation'];observations.append(obs);events.extend(detect(t,obs,source='A_historical_reflection'))
 atomic_json(ROOT/'normalized_historical_events.json',events);atomic_json(ROOT/'local_failure_candidates.json',[e for e in events if e['local_failure'] or e['validation_status']=='UNKNOWN']);atomic_json(ROOT/'verified_local_failures.json',[e for e in events if e['local_failure']]);stat=counts(tasks,observations,events);atomic_json(ROOT/'historical_statistics.json',stat)
 atomic_json(ROOT/'detector_refinement.json',dict(before_any_new_api=True,old_results_retained_in='detector_v0',reason='AssertionError in an explicit probe is not automatically a program defect; separate tool integration from mathematical result errors',sampling_unchanged=True,detector_sha256=file_hash(Path('scripts/b1_detection.py'))))
 # Three reviewed mechanisms; only M1 enters new Agent diagnosis. Counts require source + ablation, never just similar names.
 evidence=[]
 for e in events:
  if not e['local_failure'] or e['error_type']!='CODE_WRONG_RESULT':continue
  p=json.loads(by[e['task_id']]['problem']);code=e['code'] or ''
  if any(c['kind']=='endpoints' for c in p['constraints']) and ('pass' in code and ('first symbol' in code or 'track first' in code or 'first and last' in code)):
   q=copy.deepcopy(p);q['constraints']=[c for c in q['constraints'] if c['kind']!='endpoints'];ans=exact_dp(q)['answer']
   if ans==e['output']['value']:evidence.append(dict(mechanism='M1_missing_first_symbol',task_id=e['task_id'],step_id=e['step_id'],recovered=e['final_task_success'],evidence='Wrong output equals independent exact count after deleting endpoints; source endpoint branch is pass / missing first state',ablation_answer=ans))
  if 'forb_set = set(forb)' in code and 'pat = (a, b, d)' in code:
   evidence.append(dict(mechanism='M2_pattern_type_mismatch',task_id=e['task_id'],step_id=e['step_id'],recovered=e['final_task_success'],evidence='Integer tuple tested in a set of string patterns; exact type mismatch, requires causal intervention'))
  if e['task_id']=='e4d3bffc7350d308b0da' and 'newstate = (state + (d,))[-2:]' in code:
   evidence.append(dict(mechanism='M3_premature_history_truncation',task_id=e['task_id'],step_id=e['step_id'],recovered=e['final_task_success'],evidence='Executed source truncates2 before checking3; previously audited B0 discovery, not novel B1 discovery'))
 atomic_json(ROOT/'mechanism_evidence.json',evidence)
 defs=[('M1_missing_first_symbol','ed6d58a5f459a1d86a68',0,'DP retains recent symbols but omits first symbol, so endpoint relation is not enforced','endpoints constraint active; at least2 elements','no endpoints constraint','missing-first program overcounts when endpoints matters; augmentation with first state repairs','fresh Agent may regenerate missing-first/omitted endpoint check; it may also recover'),('M2_pattern_type_mismatch','1d2ad48261e4ba4cb28b',0,'Integer tuple membership against string patterns makes matches unreachable','forbidden patterns and integer tuple/string set comparison','no forbidden patterns','only type-consistent representation repair should recover known supported input','one observed recovered trajectory does not establish Agent stability'),('M3_premature_history_truncation','e4d3bffc7350d308b0da',2,'History discarded before contiguous pattern matching','pattern longer than checked text','short or unreachable pattern','retaining needed symbols repairs supported tasks','already known B0 mechanism; B1 does not count its name as new discovery')]
 mechanisms=[]
 for mid,tid,step,cause,trigger,control,pred,agentpred in defs:
  if not any(e['mechanism']==mid for e in evidence):continue
  mechanisms.append(dict(mechanism_id=mid,source_failures=[e for e in evidence if e['mechanism']==mid],observed_error='independently wrong executed full-task integer',root_cause_hypothesis=cause,trigger_conditions=[trigger],non_trigger_conditions=[control],counterfactual_prediction=dict(program=pred,agent=agentpred),falsification_conditions=['intervention fails on supported independent input','new Agent never repeats mechanism, weakening general gap interpretation'],possible_repair_capability='small explicit state/representation checker or transition helper, not yet synthesized',evidence_status='hypothesis with source evidence',assisted=True,deep_diagnosis=mid.startswith('M1')))
 assert len(mechanisms)==3,'Candidate source missing; stop rather than invent evidence'
 atomic_json(ROOT/'mechanism_candidates.json',mechanisms)
 # Minimal causal M1 repair: add first to the same DP state and endpoint final filter.
 original=callable_only(next(e['code'] for e in events if e['task_id']=='ed6d58a5f459a1d86a68' and e['step_id']==0));fixed=original
 replacements=[('cur[(s, None, c2, 0)]','cur[(s, s, None, c2, 0)]'),('for (l1, l2, c2, occ), ways','for (first, l1, l2, c2, occ), ways'),('nxt[(s, l1, nc2, nocc)]','nxt[(first, s, l1, nc2, nocc)]'),("            # need first symbol != last symbol; we didn't track first symbol\n            pass","            if first == l1:\n                continue")]
 for old,new in replacements:
  assert old in fixed,old;fixed=fixed.replace(old,new)
 d=ROOT/'causal_programs';d.mkdir(exist_ok=True);(d/'M1_original.py').write_text(original);(d/'M1_repaired.py').write_text(fixed);(d/'M1_intervention.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True),fixed.splitlines(True))))
 # M2 compatibility adapter changes decorator syntax only, preserves its cache semantics.
 original=callable_only(next(e['code'] for e in events if e['task_id']=='1d2ad48261e4ba4cb28b' and e['step_id']==0))
 original=original.replace('    @lru_cache(maxsize=None)\n','').replace('    return dp(0, 0, 0, -1, -1)','    dp = lru_cache(maxsize=None)(dp)\n    return dp(0, 0, 0, -1, -1)')
 fixed=original.replace('forb_set = set(forb)','forb_set = set(tuple(int(ch) for ch in f) for f in forb)');assert original!=fixed
 (d/'M2_original_compat.py').write_text(original);(d/'M2_repaired_compat.py').write_text(fixed)
 (d/'M2_compatibility_note.md').write_text('Historical @lru_cache syntax rejected by stable executor. Converted decorator to equivalent function assignment, before both original/repaired replays. Only repair intervention converts forbidden strings to integer tuples. No full solver rewrite.\n')
 m3=callable_only(next(e['code'] for e in events if e['task_id']=='e4d3bffc7350d308b0da' and e['step_id']==2));(d/'M3_original.py').write_text(m3);(d/'M3_repaired.py').write_text(m3.replace('newstate = (state + (d,))[-2:]','newstate = (state + (d,))[-3:]'))
 roles=TaskRoles(ROOT/'task_roles.json');seen={r['task_hash'] for r in roles.rows};rng=random.Random(2610154102);checks=[]
 def row(p,role,**meta):
  h=digest(p);assert h not in seen;seen.add(h);v=exact_dp(p)['answer']
  if p['n']<=7:assert v==brute_force(p);checks.append(dict(task_hash=h,exact=v,brute_crosscheck=True))
  roles.add(p,'HIDDEN_TEST' if role=='confirmation' else 'DEVELOPMENT','B1',role)
  return dict(id=h[:20],problem=json.dumps(p,sort_keys=True),expected=v,tier=role,scale='small' if p['n']<=7 else 'large',**meta)
 diagnostics=[]
 for j in range(10):
  n=[6,7,9,11,13,15,8,12,16,18][j];cs=[dict(kind='count',symbol=(j+2)%4,exact=1+j%4)]
  if j%3!=1:cs.append(dict(kind='adjacent'))
  if j%3!=0:cs.append(dict(kind='pattern_count',pattern=['121','010','232','303','012'][j%5],exact=j%2))
  for variant in ('trigger','control'):
   p=dict(n=n,alphabet=[0,1,2,3],constraints=copy.deepcopy(cs)+([dict(kind='endpoints')] if variant=='trigger' else []))
   diagnostics.append(row(p,'diagnostic',pair=j,variant=variant))
 # Freeze predictions before confirmation construction. No rule changes after these tasks are opened.
 atomic_json(ROOT/'mechanism_prediction_freeze.json',dict(mechanism='M1_missing_first_symbol',prediction='trigger missing-first implementation overcounts unless redundant; control should not suffer this defect',agent_prediction='same omission may recur, measure separately from other errors and infrastructure',source_sha256=file_hash(d/'M1_original.py'),repair_sha256=file_hash(d/'M1_repaired.py'),grade_rules=dict(A='program only, little new Agent evidence',B='source+intervention verified across>=2 distinct new tasks, may recover',C='B plus trigger specificity, Reflection resistance/cost and unmet existing-tool evidence; otherwise do not claim C'),opportunity_no_weighted_score=True))
 confirmation=[]
 for j in range(8):
  while True:
   n=rng.choice([7,10,14,17,21]);cs=[dict(kind='count',symbol=rng.randrange(4),exact=rng.randint(1,min(5,n)))]
   if j%2==0:cs.append(dict(kind='adjacent'))
   if j%3!=0:cs.append(dict(kind='pattern_count',pattern=rng.choice(['010','123','202','313']),exact=j%2))
   cs.append(dict(kind='endpoints'));p=dict(n=n,alphabet=[0,1,2,3],constraints=cs)
   if digest(p) not in seen:break
  confirmation.append(row(p,'confirmation',variant='trigger',pair=None))
 atomic_json(ROOT/'inputs/diagnostics.json',diagnostics);atomic_json(ROOT/'sealed/confirmation.json',confirmation);atomic_json(ROOT/'diagnostic_oracle_crosschecks.json',checks)
 plan=dict(runtime_version='operator-runtime-v1',mechanism='M1_missing_first_symbol',diagnostic_tasks=20,confirmation_tasks=8,arms=['baseline','reflection'],repeat_pairs=[0,4],additional_repeats=2,trials=72,total_api_reservation_cap_cny=8,per_arm_api_cap_cny=4,max_physical_requests=864,semantics_sha256=digest(__import__('scripts.experiment_b1',fromlist=['SEMANTICS']).SEMANTICS),diagnostic_sha256=file_hash(ROOT/'inputs/diagnostics.json'),confirmation_sha256=file_hash(ROOT/'sealed/confirmation.json'),source_hashes={str(p):file_hash(p) for p in [Path('scripts/b1_detection.py'),Path('scripts/b1_prepare_diagnosis.py')]},causal_program_hashes={str(p):file_hash(p) for p in d.glob('*.py')},repeat_selection='fixed pair indices0,4, both variants, two extra fresh contexts, before any new Agent output',no_operators_generated=True)
 atomic_json(ROOT/'diagnosis_plan.json',plan)
 (ROOT/'local_failure_report.md').write_text('# Local failure verification\n\n'+json.dumps(stat,ensure_ascii=False,indent=2)+'\n\nAutomatic detector refinements are pre-API and preserved as v0/v1. Assertions from self-tests remain suspected, refusals are not wrong results, process terminations remain Unknown. Tool-interface failures count only as observable integration errors, never as algorithmic gap evidence. Mechanism labeling requires separate source/causal evidence.\n')
 print(dict(statistics=stat,mechanism_counts=dict(Counter(e['mechanism'] for e in evidence)),new_trials=72),flush=True)

if __name__=='__main__':
 from collections import Counter
 prepare()
