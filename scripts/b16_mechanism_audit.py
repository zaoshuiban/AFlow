"""Offline assisted source review and minimal type-only causal interventions."""
import argparse,asyncio,difflib,json
from pathlib import Path
from scripts.experiment_b16 import ROOT
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.python_tool_interface_v3 import executor
from scripts.compositional_counting import exact_dp,brute_force

def inventory(phase):
 rows=[]
 for path in sorted((ROOT/(phase+'_events')).glob('*.json')):
  r=read_json(path);obs=r['observation'];t=r['task']
  for e in r['events']:
   i=e['step_id'];code=e.get('code');raw=obs['events'][i].get('tool_result',{});status=raw.get('status');wrong=status=='success' and raw.get('value')!=t['expected'];warning=raw.get('contract_check',{}).get('status')=='WARNING'
   runtime_violation=raw.get('error_type')=='CODE_RUNTIME_ERROR'
   if not (wrong or warning or runtime_violation):continue
   sid=t['id']+'_'+r['method']+'_s'+str(i);p=ROOT/'audit_sources'/(phase+'_'+sid+'.py');p.parent.mkdir(exist_ok=True)
   if code:p.write_text(code)
   rows.append(dict(evidence_id=phase+'_'+sid,phase=phase,task_id=t['id'],method=r['method'],step=i,code_path=str(p) if code else None,source_sha256=file_hash(p) if code else None,value=raw.get('value'),expected=t['expected'],wrong=wrong,observed_runtime_violation=runtime_violation,checker_warning=warning,checker=raw.get('contract_check'),final_correct=obs['correct'],classification='UNKNOWN',verified=False))
 atomic_json(ROOT/(phase+'_source_inventory.json'),rows)
 print(json.dumps(rows,ensure_ascii=False,indent=2));return rows

async def interventions(phase):
 inv={r['evidence_id']:r for r in read_json(ROOT/(phase+'_source_inventory.json'))};specs=read_json(ROOT/(phase+'_intervention_specs.json'))
 tasks={read_json(p)['task']['id']:read_json(p)['task'] for p in (ROOT/(phase+'_events')).glob('*.json')};rows=[]
 for spec in specs:
  r=inv[spec['evidence_id']];task=tasks[r['task_id']];code=Path(r['code_path']).read_text();fixed=code
  for old,new in spec['replacements']:assert old in fixed;fixed=fixed.replace(old,new)
  dest=ROOT/'causal_repairs'/(r['evidence_id']+'.py');dest.parent.mkdir(exist_ok=True);dest.write_text(fixed)
  p=json.loads(task['problem']);control=json.loads(task['problem']);control['constraints']=[c for c in control['constraints'] if c['kind'] not in ['pattern_count','forbidden']];small=json.loads(task['problem']);small['n']=min(small['n'],7)
  for c in small['constraints']:
   if c['kind']=='count':c['exact']=min(c['exact'],small['n'])
   if c['kind']=='pattern_count':c['exact']=min(c['exact'],max(0,small['n']-len(c['pattern'])+1))
  assert exact_dp(small)['answer']==brute_force(small);checks=[]
  for name,p in [('actual',p),('control',control),('small',small)]:
   expected=exact_dp(p)['answer'];variants={}
   for v,c in [('original',code),('type_only',fixed)]:
    out=await executor(c,json.dumps(p,sort_keys=True),6);variants[v]=dict(execution=out,correct=out.get('status')=='success' and out.get('value')==expected)
   checks.append(dict(name=name,problem=p,expected=expected,variants=variants))
  actual,ctl,smallcheck=[c['variants'] for c in checks];same_control=all(ctl['original']['execution'].get(k)==ctl['type_only']['execution'].get(k) for k in ['status','value','error'])
  verified=((r['wrong'] or r.get('observed_runtime_violation')) and not actual['original']['correct'] and actual['type_only']['correct'] and smallcheck['type_only']['correct'] and same_control)
  rows.append(dict(r,verified=verified,classification='VERIFIED_M2' if verified else spec.get('unverified_classification','UNKNOWN'),rationale=spec['rationale'],repaired_source=str(dest),repaired_sha256=file_hash(dest),diff=''.join(difflib.unified_diff(code.splitlines(True),fixed.splitlines(True))),checks=checks))
 atomic_json(ROOT/(phase+'_causal_interventions.json'),rows)
 print(json.dumps([dict(id=r['evidence_id'],verified=r['verified'],classification=r['classification']) for r in rows],ensure_ascii=False))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['inventory','intervene']);p.add_argument('phase',choices=['pilot','hidden']);a=p.parse_args()
 if a.command=='inventory':inventory(a.phase)
 else:asyncio.run(interventions(a.phase))
