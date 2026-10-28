"""Read-only introspection of the trusted old static checker, not generated-code execution."""
import ast,sys
from collections import Counter
from pathlib import Path
from scripts.pattern_type_contract_checker import check as old_check
from scripts.experiment_b17 import ROOT
from scripts.offline_loop_protocol import read_json,atomic_json

def run():
 audit=read_json(ROOT/'unknown_53_audit.json');out=[]
 for row in audit['calls']:
  captured={}
  def trace(frame,event,arg):
   if frame.f_code is old_check.__code__ and event=='return':
    for k in ['pattern_names','types','conflicts','compares','known']:
     v=frame.f_locals.get(k);captured[k]=sorted(v) if isinstance(v,set) else v
   return trace
  previous=sys.gettrace()
  try:sys.settrace(trace);res=old_check(row['code'])
  finally:sys.settrace(previous)
  assert res['status']=='UNKNOWN'
  names=captured.get('pattern_names') or [];compares=captured.get('compares') or 0
  if not names:cause='NO_DIRECT_PATTERN_PROVENANCE'
  elif not compares:cause='ALIAS_OR_ITERATION_PROVENANCE_LOST'
  else:cause='OPERAND_REPRESENTATION_UNRESOLVED'
  features=[];tree=ast.parse(row['code'])
  if any(isinstance(n,ast.For) and isinstance(n.target,ast.Tuple) for n in ast.walk(tree)):features.append('DP_OR_TUPLE_DESTRUCTURING_UNSUPPORTED')
  if any(isinstance(n,(ast.GeneratorExp,ast.ListComp,ast.DictComp)) for n in ast.walk(tree)):features.append('COMPREHENSION_FLOW_UNSUPPORTED')
  if captured.get('conflicts'):features.append('SCOPE_INSENSITIVE_NAME_CONFLICT_PRESENT')
  if row['confirmed_prior_label']=='UNREVIEWED':features.append('NO_CONFIRMED_DEFECT_LABEL')
  out.append(dict(row,root_cause=cause,old_checker_internal_state=captured,code_shapes=features,parse_failure=False,runtime_evidence_missing=True))
 atomic_json(ROOT/'unknown_53_audit.json',dict(audit,calls=out,counts=dict(Counter(r['root_cause'] for r in out)),parse_failures=0,warning='These are causes of static abstention, not automatic defect labels. Only pre-reviewed verified M2 is a known miss.'))
 print(Counter(r['root_cause'] for r in out))
if __name__=='__main__':run()
