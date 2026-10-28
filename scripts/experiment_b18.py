"""B1.8 offline information audit and bounded development feasibility replay.
No new checkers, full solvers, API experiments or independent-recurrence claims.
"""
import argparse,asyncio,copy,difflib,json,statistics,time
from pathlib import Path
from collections import Counter
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.experiment_b17 import HIST
from scripts.compositional_counting import SPEC,exact_dp,brute_force
from scripts.operator_runtime import execute,Resources
ROOT=Path('results/experiment_b18_20261015_v1')
HISTORY=HIST+['results/experiment_b17_20261015_v1','scripts/b17_checkers.py','scripts/b17_checkpoint_study.py']
def snapshot():
 return {str(p):file_hash(p) for d in HISTORY for p in ([Path(d)] if Path(d).is_file() else Path(d).rglob('*')) if p.is_file() and '__pycache__' not in str(p)}

def prepare():
 if (ROOT/'protocol.json').exists():raise FileExistsError('Run exists')
 ROOT.mkdir(exist_ok=True,parents=True);atomic_json(ROOT/'history_before.json',snapshot())
 b1=Path('results/experiment_b1_20261015_v1');alltasks=read_json(b1/'inputs/natural_discovery.json');m3=next(t for t in alltasks if t['id']=='e4d3bffc7350d308b0da');m1=next(t for t in alltasks if t['id']=='ed6d58a5f459a1d86a68')
 tasks=[]
 for mid,t in [('M1',m1),('M3',m3)]:
  for role in ['historical_original','small_n7','constraint_removed']:
   p=json.loads(t['problem'])
   if role=='small_n7':p['n']=7
   if role=='constraint_removed':p['constraints']=[c for c in p['constraints'] if c['kind'] not in (['endpoints'] if mid=='M1' else ['pattern_count','forbidden'])]
   expected=exact_dp(p)['answer']
   if p['n']<=7:assert expected==brute_force(p)
   tasks.append(dict(mechanism=mid,role=role,problem=json.dumps(p,sort_keys=True),expected=expected,historical_source_task_id=t['id'],data_role='DEVELOPMENT_ONLY',not_independent_algorithms=True))
 atomic_json(ROOT/'development_replay_tasks.json',tasks)
 # Generic alternative M3 repair is mechanically derived from streaming spec:
 # test the newly extended suffix before dropping old symbols, then retain L-1.
 old=(b1/'causal_programs/M3_original.py').read_text()
 generic=old.replace('newstate = (state + (d,))[-2:]','newstate = state + (d,)').replace('ndp[(newstate,nocc)]','ndp[(newstate[-2:],nocc)]')
 assert generic!=old
 (ROOT/'source_copies').mkdir(exist_ok=True)
 for mid in ['M1','M3']:
  for variant in ['original','repaired']:
   (ROOT/'source_copies'/f'{mid}_{variant}.py').write_text((b1/f'causal_programs/{mid}_{variant}.py').read_text())
 (ROOT/'source_copies/M3_generic_streaming_repair.py').write_text(generic)
 # Same endpoint dependency repair has identical public-spec and failure descriptions.
 (ROOT/'source_copies/M1_generic_dependency_repair.py').write_text((b1/'causal_programs/M1_repaired.py').read_text())
 rules=[
 dict(id='M1_endpoint_information',class_A='Accept only sequences with first != last; n=1 yields zero.',class_B='The effective state or equivalent conditioning must distinguish prefixes with different first symbols when terminal acceptance differs.',class_C='Which concrete generated key loses that information, and whether a first-only intervention causally repairs this particular program, require source/execution evidence.',generic_predicate='Any compressed states asserted equivalent must have the same future acceptance for the public endpoint predicate.',failure_predicate='If prefixes with different first symbols are merged and terminal endpoint acceptance differs, the state is insufficient.',predicate_difference=False,scope='Necessary information, not literal variable-name presence. Per-first separate runs, disjoint caches, encoded first or closure conditioning may be correct.',not_strict_M1='External mutable cache context / missing count equality / unrelated endpoint formula are separate or compound.',failure_exclusive_transferable_condition=False),
 dict(id='M2_representation',class_A='Alphabet symbols are integers; patterns encode the same digits as strings. Matching representations must agree.',class_B='Track actual comparison operands/representation conversion.',class_C='Reachable mismatched comparison and its return effect are concrete evidence, obtainable by either method using the same execution engine.',generic_predicate='Executed matching operands obey the same public digit representation contract.',failure_predicate='Same observable comparison contract as B1.7.',predicate_difference=False,scope='Keep B1.7 limited coverage and negative result; no retraining or new M2 checker.',failure_exclusive_transferable_condition=False),
 dict(id='M3_history_sufficiency',class_A='Check every contiguous substring of length L, including overlaps.',class_B='For an explicit suffix matcher, form history+new symbol, test its length-L suffix, then retain enough information for subsequent matches. Automaton state is an equivalent representation, not a length-L raw buffer requirement.',class_C='A particular early truncation line, reached dataflow, and effect of increasing history or moving the check are program-specific causal facts.',generic_predicate='Before a raw length-L suffix match, enough history must be available; later information loss must preserve future observable matching.',failure_predicate='If the only raw candidate is truncated below L before comparison, that implementation cannot recognize length L.',predicate_difference=False,scope='Explicit raw suffix form only; KMP/Aho-Corasick compressed states may be fully sufficient. Historical [-2:] -> [-3:] repair only validates tested length-3 cases, not a general variable-length solution.',failure_exclusive_transferable_condition=False)]
 atomic_json(ROOT/'mechanism_rule_comparison.json',rules)
 sources={str(p):file_hash(p) for p in (ROOT/'source_copies').glob('*.py')}
 sources.update({f:file_hash(Path(f)) for f in ['scripts/experiment_b18.py','scripts/compositional_counting.py']+[str(p) for p in Path('scripts/operator_runtime').glob('*.py')]})
 atomic_json(ROOT/'protocol.json',dict(experiment='B1.8',phase='A only until information gate',accept_B17='NO_DISTINCT_MECHANISM_VALUE',mechanisms=['M1','M2','M3'],new_API_authorized_by_this_run=False,new_API_cap_cny=0,information_gate='Promote to B only with a transferable F-only observable predicate unavailable at comparable generic cost, or a nontrivial independently testable evidence-derived selection hypothesis with a real resource bottleneck. Origin in a failure report is not enough.',stop_rule='If rules add no demonstrated predicate/evidence capability and no validated resource-constrained selection gain, stop local-failure-to-checker route. Report selection benefit unestablished, not universally impossible.',development_cost_replay=dict(cases=6,program_sources=2,versions=['historical_original','failure_repair','generic_alternative'],timing_repetitions=3,order_seed=2610151801,budgets_seconds=[3,10],meaning='Real wall/CPU measurement under stabilized executor. Historical inputs/source variants are development, not independent confirmation or an F/G/S accuracy experiment.'),no_selection_population='Existing M1/M2/M3 corpus is mechanism-targeted and unevenly reviewed. It does not support an unbiased deployment frequency prior. Do not manufacture F priorities from targeted error counts.',task_sha256=file_hash(ROOT/'development_replay_tasks.json'),rule_sha256=file_hash(ROOT/'mechanism_rule_comparison.json'),sources=sources))
 for mid in ['M1','M3']:
  old=(ROOT/f'source_copies/{mid}_original.py').read_text()
  for variant in ['repaired','generic_dependency_repair' if mid=='M1' else 'generic_streaming_repair']:
   new=(ROOT/f'source_copies/{mid}_{variant}.py').read_text();(ROOT/f'{mid}_{variant}.diff').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True))))
 print('Frozen offline information audit; API cap zero. Six historical development cases, two source programs.',flush=True)

def verify():
 p=read_json(ROOT/'protocol.json')
 for f,h in p['sources'].items():assert file_hash(Path(f))==h,f
 assert file_hash(ROOT/'development_replay_tasks.json')==p['task_sha256'];assert file_hash(ROOT/'mechanism_rule_comparison.json')==p['rule_sha256'];assert snapshot()==read_json(ROOT/'history_before.json')
 return p

async def replay():
 p=verify()
 if (ROOT/'development_replay.json').exists():raise FileExistsError('No result-dependent rerun')
 import random
 work=[(task,variant,rep) for task in read_json(ROOT/'development_replay_tasks.json') for variant in ['original','repaired','generic_dependency_repair' if task['mechanism']=='M1' else 'generic_streaming_repair'] for rep in range(3)];random.Random(2610151801).shuffle(work);out=[]
 for task,variant,rep in work:
  code=(ROOT/f"source_copies/{task['mechanism']}_{variant}.py").read_text();start=time.monotonic();r=await execute(code,task['problem'],3,resources=Resources(cpu_seconds=5));wall=time.monotonic()-start
  out.append(dict(task=task,variant=variant,repetition=rep,execution=r,measured_wall_seconds=wall,correct=r.get('status')=='success' and r.get('value')==task['expected'],source_sha256=file_hash(ROOT/f"source_copies/{task['mechanism']}_{variant}.py")))
 atomic_json(ROOT/'development_replay.json',out)
 print(dict(executions=len(out),API=0,history_unchanged=True),flush=True)

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('command',choices=['prepare','replay']);args=a.parse_args()
 if args.command=='prepare':prepare()
 else:asyncio.run(replay())
