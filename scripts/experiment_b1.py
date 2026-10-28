"""Exploratory local-failure discovery, no operator synthesis or hidden feedback."""
from __future__ import annotations
import argparse,ast,asyncio,copy,difflib,functools,json,random,shutil,time
from pathlib import Path
from collections import Counter
from scripts.offline_loop_protocol import atomic_json,read_json,digest,file_hash,run_lock
from scripts.compositional_counting import SPEC,exact_dp,brute_force
from scripts.operator_runtime import Transport,solve,execute,Resources
from scripts.operator_runtime.history import TaskRoles

ROOT=Path('results/experiment_b1_20261015_v1')
A=Path('results/generated_operator_experiment_a_v1');B0=Path('results/experiment_b0_20261014_v1')
SEMANTICS=SPEC+'\nExecution environment: imports and function definitions only; no decorators or type annotations in submitted code.'
SEED=2610154101
FILES=['scripts/experiment_b1.py','scripts/compositional_counting.py','scripts/experiment_a_calibration.py','scripts/experiment_a_pilot.py']+[str(p) for p in sorted(Path('scripts/operator_runtime').glob('*.py'))]


def detect_local(task,observation,*,source='historical',codes=None):
 """Only executed full-task solve outputs/errors. Never infer hidden reasoning."""
 expected=task['expected'];results=[]
 for index,e in enumerate(observation.get('events',[])):
  name=e.get('function') or e.get('tool');t=e.get('tool_result') or e.get('result')
  if name not in ('python','operator') or not t:continue
  trace=t.get('trace',{});code=trace.get('code') or e.get('arguments',{}).get('code') or (codes or {}).get(index)
  status=t.get('status');value=t.get('value');error=t.get('error','');r=dict(task_id=task['id'],run_id=source,step_id=index,operation_type='executed_full_task_solver',tool_name=name,input=dict(problem=task['problem']),output=dict(value=value,error=error),execution_status=status,validation_status='UNKNOWN',final_task_success=observation['correct'],error_type=None,local_failure=False,recovery_status=None,code=code,code_sha256=digest(code) if code else None,evidence=[],mechanism='UNKNOWN')
  actual_hash=trace.get('problem_sha256');valid_hash=actual_hash is None or actual_hash in (digest(task['problem']),__import__('scripts.operator_runtime.events',fromlist=['hash_value']).hash_value(task['problem']))
  if not valid_hash:r['evidence']=['Input hash mismatch; cannot compare to full task'];results.append(r);continue
  if status=='success' and type(value) is int:
   # Tool protocol explicitly calls solve(main JSON); top-level prints are not returned.
   r.update(validation_status='VERIFIED' if value==expected else 'FAILED',error_type=None if value==expected else 'CODE_WRONG_RESULT',local_failure=value!=expected)
   r['evidence']=['Executed solve(problem) full-task return, independently recomputed exact reference; not print/debug output']
  elif status=='error':
   if any(s in error for s in ['worker_exit_','worker_timeout','memory_threshold','rss_limit']):
    r.update(error_type=t.get('error_type') or 'UNKNOWN',validation_status='UNKNOWN');r['evidence']=['Observed resource/process termination; root program defect not established']
   else:
    typ=t.get('error_type') or ('CODE_CONTRACT_ERROR' if any(s in error for s in ['Return','Define solve','Prohibited','Unsupported','Attribute not allowed']) else 'CODE_IMPORT_ERROR' if 'Import' in error else 'CODE_SYNTAX_ERROR' if 'SyntaxError' in error else 'CODE_RUNTIME_ERROR')
    r.update(error_type=typ,validation_status='FAILED',local_failure=typ!='TOOL_INTEGRATION_ERROR');r['evidence']=['Observable tool contract/runtime failure; no algorithm-capability inference']
  elif status=='declined':r.update(validation_status='NOT_CHECKED');r['evidence']=['Explicit refusal is not a verified incorrect intermediate integer']
  if r['local_failure']:
   r['local_status']='VERIFIED_LOCAL_FAILURE';r['recovery_status']='RECOVERED_LOCAL_FAILURE' if observation['correct'] else 'UNRECOVERED_LOCAL_FAILURE'
  else:r['local_status']='UNKNOWN' if r['validation_status']=='UNKNOWN' else 'EXPECTED_EXPLORATION' if status=='declined' else 'NO_VERIFIED_FAILURE'
  results.append(r)
 return results


def counts(tasks,observations,events):
 by={r['id']:r for r in observations};group={t['id']:[e for e in events if e['task_id']==t['id']] for t in tasks}
 observable={i for i,z in group.items() if z};verifiable={i for i,z in group.items() if any(e['validation_status'] in ('VERIFIED','FAILED') for e in z)};local={i for i,z in group.items() if any(e['local_failure'] for e in z)};success={i for i,r in by.items() if r['correct']};recover=local&success
 return dict(tasks=len(tasks),final_success=len(success),final_failure=len(tasks)-len(success),observable_tool_tasks=len(observable),verifiable_intermediate_tasks=len(verifiable),observable_tool_steps=len(events),verifiable_intermediate_steps=sum(e['validation_status'] in ('VERIFIED','FAILED') for e in events),verified_local_failure_tasks=len(local),verified_local_failure_steps=sum(e['local_failure'] for e in events),recovered_local_tasks=len(recover),unrecovered_local_tasks=len(local-success),recovered_local_steps=sum(e['local_failure'] and e['final_task_success'] for e in events),unknown_tasks=sum(any(e['validation_status']=='UNKNOWN' for e in z) for z in group.values()),unknown_steps=sum(e['validation_status']=='UNKNOWN' for e in events),local_incidence=dict(numerator=len(local),denominator=len(verifiable),rate=len(local)/len(verifiable) if verifiable else None),recovered_rate_verifiable=dict(numerator=len(recover),denominator=len(success&verifiable),rate=len(recover)/len(success&verifiable) if success&verifiable else None),recovered_rate_all_success=dict(numerator=len(recover),denominator=len(success),rate=len(recover)/len(success) if success else None))


def prepare_history():
 if (ROOT/'manifest.json').exists():raise FileExistsError('Existing B1 run; use later phases')
 ROOT.mkdir(parents=True,exist_ok=True)
 tasks=read_json(A/'inputs/formal_unsealed.json');rng=random.Random(SEED);selected=[]
 for tier in ('Easy','Medium','Hard'):
  selected.extend(rng.sample(sorted([t for t in tasks if t['tier']==tier],key=lambda x:x['id']),20))
 atomic_json(ROOT/'natural_discovery_manifest.json',dict(source='A historical ID Reflection, balanced tier sample',n=60,selection_seed=SEED,selection='20 each tier, sorted IDs then random.sample, outcome-blind',population=180,inclusion='No correct/failure filtering',scope='Historical benchmark-stratified distribution, NOT real-world natural occurrence',task_ids=[t['id'] for t in selected],fresh_natural_tasks_required=False,natural_new_api_calls=0))
 atomic_json(ROOT/'inputs/natural_discovery.json',selected)
 sources={p:file_hash(Path(p)) for p in FILES}
 m=dict(experiment='B1 Local Failure-Based Capability Gap Discovery',runtime_version='operator-runtime-v1',model_alias='deepseek-v4-flash',temperature=0,thinking='disabled',status='history_prepared',sources=sources,source_history=dict(A_manifest=file_hash(A/'manifest.json'),B0_manifest=file_hash(B0/'manifest.json')),history_snapshot=read_json(Path('engineering_stabilization/runs/20261015_v1/history_before.json')),api_budget_cny=8,max_requests=864,arms=['baseline','reflection'],budget=dict(calls=4,output_tokens=8192,tool_wall=10,trial_wall=180,cpu_seconds=5,worker_wall=6,memory_bytes=256*1024**2,concurrency=3),plan=dict(mechanisms_deep=1,max_candidates=3,diagnostic_pairs=10,repeat_pairs=[0,4],extra_repetitions=2,confirmation=8),note='No generated operators. Historical Discovery and new Targeted Diagnosis never pooled. All new calls use stable runtime.')
 atomic_json(ROOT/'manifest.json',m)
 shutil.copyfile('/Users/majiatong/.codex/attachments/e71a679a-72cc-495e-b2d7-a0f6548fed99/已粘贴的文本.txt',ROOT/'user_protocol.txt')
 for p in FILES:
  dst=ROOT/'frozen_code'/p;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dst)
 inventory=dict(available=dict(A_baseline=240,A_reflection=240,B0_natural_reflection=20,B0_targeted_diagnostic_reflection=20),selected_natural=dict(n=60,arm='reflection',source='A ID'),historical_discovery_role=True,no_new_natural_api=True,observation_completeness='all selected have native full-task solve events, source, final answer, reference, usage and timing')
 atomic_json(ROOT/'trace_inventory.json',inventory)
 observations=[];events=[];cross=[]
 roles=TaskRoles(ROOT/'task_roles.json')
 # Register all known historical inputs as already used, without changing original records.
 for p in [A/'inputs/training.json',A/'inputs/development.json',A/'inputs/boundary.json',A/'inputs/formal_unsealed.json',B0/'inputs/diagnostics.json',B0/'inputs/operator_development.json',B0/'sealed/hidden.json']:
  for r in read_json(p):roles.add(r['problem'],'HISTORICAL_TEST','historical',str(p))
 for task in selected:
  truth=exact_dp(json.loads(task['problem']))['answer'];assert truth==task['expected'];cross.append(dict(id=task['id'],historical=task['expected'],independent_recomputed=truth))
  obs=read_json(A/'observations'/f"{task['id']}_reflection.json");observations.append(obs);ev=detect_local(task,obs,source='A_historical_reflection');events.extend(ev)
  atomic_json(ROOT/'natural_trajectories'/f"{task['id']}.json",dict(task=task,observation=obs,source_path=str(A/'observations'/f"{task['id']}_reflection.json"),source_sha256=file_hash(A/'observations'/f"{task['id']}_reflection.json")))
  roles.add(task['problem'],'DISCOVERY','B1','outcome-blind selected historical Reflection')
  for e in ev:
   if e['local_failure'] and e['code']:
    p=ROOT/'discovery_code'/f"{e['task_id']}_{e['step_id']}.py";p.parent.mkdir(exist_ok=True);p.write_text(e['code']);e['code_path']=str(p)
 atomic_json(ROOT/'reference_crosschecks.json',cross);atomic_json(ROOT/'normalized_historical_events.json',events)
 atomic_json(ROOT/'local_failure_candidates.json',[e for e in events if e['local_failure'] or e['validation_status']=='UNKNOWN'])
 atomic_json(ROOT/'verified_local_failures.json',[e for e in events if e['local_failure']])
 stat=counts(selected,observations,events);atomic_json(ROOT/'historical_statistics.json',stat)
 atomic_json(ROOT/'human_interventions.json',dict(assisted=True,automatic_components=['outcome-blind sampling','executed-result/reference comparator','runtime/contract event detector','denominator accounting'],assisted_components=['mechanism code review and minimal causal interventions','diagnostic design and interpretation'],not_autonomous_mechanism_discovery=True,known_cost_for_assistant_reasoning=None))
 (ROOT/'historical_trace_analysis.md').write_text('# Historical trace analysis\n\n'+json.dumps(stat,ensure_ascii=False,indent=2)+'\n\n60 historical benchmark-stratified Reflection trajectories; not targeted diagnosis or natural real-world error frequency. Only actual executed full-task outputs compared; debug prints, unexecuted programs, search revisions and explicit refusals are not automatically failures. Process/resource errors remain Unknown unless separately verified. All references recomputed offline, never passed to new Agent calls.\n')
 atomic_json(ROOT/'state.json',dict(status='history_analyzed',new_agent_calls=0))
 print(json.dumps(stat,ensure_ascii=False),flush=True)


def verify():
 m=read_json(ROOT/'manifest.json')
 for p,h in m['sources'].items():assert file_hash(Path(p))==h,p
 for p,h in m['history_snapshot'].items():assert file_hash(Path(p))==h,'Historical file changed: '+p
 return m


def callable_only(code):
 tree=ast.parse(code)
 return '\n\n'.join(ast.get_source_segment(code,n) for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom,ast.FunctionDef)))+'\n'


def load_codes(phase_root,observation):
 codes={}
 for f in (phase_root/'api_attempts').glob('*.json'):
  r=read_json(f)
  if not r['logical_key'].startswith(f"pilot/{observation['id']}/{observation['arm']}/"):continue
  for tc in r.get('raw_response',{}).get('message',{}).get('tool_calls',[]):
   fn=tc['function']
   if fn['name']=='python':
    try:code=json.loads(fn['arguments'])['code']
    except (ValueError,KeyError,TypeError):continue
    for i,e in enumerate(observation['events']):
     t=e.get('tool_result',{});sha=t.get('trace',{}).get('code_sha256')
     if sha==__import__('scripts.operator_runtime.events',fromlist=['hash_value']).hash_value(code):codes[i]=code
 return codes


def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['history']);a=p.parse_args()
 if a.command=='history':prepare_history()

if __name__=='__main__':main()
