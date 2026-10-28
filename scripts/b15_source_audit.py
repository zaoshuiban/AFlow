"""Offline source evidence inventory; candidate screens never establish mechanisms."""
import ast,json
from pathlib import Path
from scripts.experiment_b15 import ROOT
from scripts.offline_loop_protocol import atomic_json,read_json,file_hash

def inventory():
 rows=[];unknown=[]
 for p in sorted((ROOT/'mechanism_events').glob('*.json')):
  r=read_json(p);task=r['task']
  for e in r['events']:
   code=e.get('code');value=e['output']['value'];actualwrong=e['execution_status']=='success' and type(value) is int and value!=task['expected']
   # Include every wrong executed full-task return, irrespective of whether screen fires.
   if not actualwrong:continue
   stem=p.stem+f"_s{e['step_id']}";dest=ROOT/'audit_sources'/(stem+'.py');dest.parent.mkdir(exist_ok=True)
   if code:dest.write_text(code)
   hints=[]
   if code:
    tree=ast.parse(code)
    for n in ast.walk(tree):
     if isinstance(n,ast.Compare):
      s=ast.get_source_segment(code,n) or ''
      if any(x in s.lower() for x in ['pat','first','tail','hist','seq']):hints.append(s)
   rows.append(dict(evidence_id=stem,task_id=task['id'],arm=r['arm'],repeat=r['repeat'],stage=r['stage'],designed_mechanism=task['mechanism'],pair=task['pair'],variant=task['variant'],value=value,expected=task['expected'],final_correct=r['observation']['correct'],code_path=str(dest) if code else None,code_sha256=file_hash(dest) if code else None,candidate_comparisons=sorted(set(hints)),classification='UNKNOWN',verified=False))
 atomic_json(ROOT/'source_inventory.json',rows)
 print(json.dumps([dict(id=r['evidence_id'],m=r['designed_mechanism'],variant=r['variant'],value=r['value'],expected=r['expected'],comparisons=r['candidate_comparisons']) for r in rows],ensure_ascii=False,indent=2))
 return rows
if __name__=='__main__':inventory()
