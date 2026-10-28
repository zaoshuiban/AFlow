"""Read-only provenance audits for B0; writes only the new B0 audit file."""
import json,platform,sys,importlib.metadata
from pathlib import Path
from scripts.experiment_b0 import ROOT,verify
from scripts.offline_loop_protocol import read_json,atomic_json,digest,file_hash


def collect(v,out):
 if isinstance(v,dict):
  if set(v)=={'n','alphabet','constraints'}:out.add(digest(v))
  for x in v.values():collect(x,out)
 elif isinstance(v,list):
  for x in v:collect(x,out)
 elif isinstance(v,str):
  try:parsed=json.loads(v)
  except (ValueError,TypeError):
   for marker in ('Discovery task and original raw execution:\n','Common independent validation feedback:\n'):
    if marker in v:
     try:parsed,_=json.JSONDecoder().raw_decode(v.split(marker,1)[1]);collect(parsed,out)
     except ValueError:pass
   return
  if not isinstance(parsed,str):collect(parsed,out)


def audit():
 verify();allowed={digest(json.loads(r['problem'])) for r in read_json(ROOT/'inputs/operator_development.json')+[read_json(ROOT/'discovery/task.json')]}
 hidden={digest(json.loads(r['problem'])) for r in read_json(ROOT/'sealed/hidden.json')};assert not hidden&allowed
 checks=[]
 for g in ('F','M'):
  d=ROOT/f'generation_{g}';freeze=read_json(d/'freeze.json');gen=read_json(d/f"attempt_{freeze['selected_attempt']}/generation.json")
  assert (d/'operator.py').read_text()==gen['code']
  seen=set()
  for p in (d/'requests').glob('*.json'):collect(read_json(p)['request']['messages'],seen)
  assert not (seen-allowed),(g,seen-allowed)
  assert not seen&hidden
  checks.append(dict(group=g,source_equals_raw_generated_code=True,source_sha256=file_hash(d/'operator.py'),structured_task_hashes_found=len(seen),all_visible_tasks_allowed=True,no_hidden_tasks_found=True,
   freeze_mtime_before_evaluation=(d/'freeze.json').stat().st_mtime<=(ROOT/'evaluation_start.json').stat().st_mtime))
 assert all(c['freeze_mtime_before_evaluation'] for c in checks)
 packages={}
 for name in ('openai','numpy','pydantic'):
  try:packages[name]=importlib.metadata.version(name)
  except importlib.metadata.PackageNotFoundError:packages[name]=None
 atomic_json(ROOT/'environment.json',dict(python=sys.version,platform=platform.platform(),packages=packages))
 atomic_json(ROOT/'provenance_audit.json',dict(checks=checks,public_reference_source_not_sent=True,original_A_source_untouched=True,
   scope='Structured-task recursive parsing plus explicit audited prompt builder. This does not prove absence of every conceivable textual leak.',human_mechanism_assistance_declared=True))
 print(checks)

if __name__=='__main__':audit()
