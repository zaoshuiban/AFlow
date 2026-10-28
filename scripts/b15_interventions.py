"""Assisted offline causal trials, isolated just like Agent code; no repair feedback."""
import asyncio,json,difflib
from pathlib import Path
from scripts.experiment_b15 import ROOT
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.python_tool_interface_v2 import executor
from scripts.compositional_counting import exact_dp,brute_force

async def run():
 specs=read_json(ROOT/'intervention_specs.json') if (ROOT/'intervention_specs.json').exists() else []
 inv={r['evidence_id']:r for r in read_json(ROOT/'source_inventory.json')};tasks=read_json(ROOT/'mechanism_inputs/diagnostics.json')
 if (ROOT/'source_audit_freeze.json').exists():tasks+=read_json(ROOT/'sealed/confirmation.json')
 results=[]
 for spec in specs:
  r=inv[spec['evidence_id']];task=next(t for t in tasks if t['id']==r['task_id']);code=Path(r['code_path']).read_text();repaired=code
  for before,after in spec['replacements']:
   assert before in repaired,(r['evidence_id'],before);repaired=repaired.replace(before,after)
  dest=ROOT/'interventions'/(r['evidence_id']+'_repaired.py');dest.parent.mkdir(exist_ok=True);dest.write_text(repaired)
  variants={'original':code,'repaired':repaired}
  for name,replacements in spec.get('factorial_variants',{}).items():
   text=code
   for before,after in replacements:assert before in text;text=text.replace(before,after)
   variants[name]=text
  cases=[dict(label='actual',problem=json.loads(task['problem']))]
  # Counterfactual removes only the repaired mechanism's relevant condition, not all constraints.
  q=json.loads(task['problem']);q['constraints']=[c for c in q['constraints'] if c['kind'] not in (['endpoints'] if spec['mechanism']=='M1' else ['pattern_count','forbidden'])];cases.append(dict(label='mechanism_control',problem=q))
  small=json.loads(task['problem']);small['n']=7
  for c in small['constraints']:
   if c['kind']=='count':c['exact']=min(c['exact'],7)
   if c['kind']=='pattern_count':c['exact']=min(c['exact'],max(0,8-len(c['pattern'])))
  assert exact_dp(small)['answer']==brute_force(small);cases.append(dict(label='small_crosscheck',problem=small))
  checks=[]
  for case in cases:
   expected=exact_dp(case['problem'])['answer'];outputs={}
   for name,text in variants.items():
    out=await executor(text,json.dumps(case['problem'],sort_keys=True),6);outputs[name]=dict(execution=out,correct=out.get('value')==expected and out['execution_status']=='SUCCESS')
   checks.append(dict(**case,expected=expected,variants=outputs))
  a=checks[0]['variants'];c=checks[1]['variants'];smallcheck=checks[2]['variants']
  control_same=(c['original']['execution'].get('value')==c['repaired']['execution'].get('value') and c['original']['execution'].get('execution_status')==c['repaired']['execution'].get('execution_status') and c['original']['execution'].get('error')==c['repaired']['execution'].get('error'))
  verified=(not a['original']['correct'] and a['repaired']['correct'] and smallcheck['repaired']['correct'] and control_same)
  if spec['mechanism']=='M1':verified=verified and c['original']['correct'] and c['repaired']['correct']
  causal_verified=verified
  if spec.get('strict_scope') is False:verified=False
  results.append(dict(r,causal_verified=causal_verified,mechanism=spec['mechanism'],classification='VERIFIED_MECHANISM_OCCURRENCE' if verified else spec.get('failure_classification','UNKNOWN'),verified=verified,review_rationale=spec['rationale'],repaired_path=str(dest),repaired_sha256=file_hash(dest),diff=''.join(difflib.unified_diff(code.splitlines(True),repaired.splitlines(True))),cases=checks,assisted=True))
 atomic_json(ROOT/'causal_interventions.json',results)
 print(json.dumps([dict(id=r['evidence_id'],mechanism=r['mechanism'],verified=r['verified'],classification=r['classification'],cases=[dict(label=c['label'],expected=c['expected'],outputs={k:v['execution'].get('value') for k,v in c['variants'].items()}) for c in r['cases']]) for r in results],indent=2))
if __name__=='__main__':asyncio.run(run())
