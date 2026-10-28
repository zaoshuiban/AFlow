"""B1.7 offline detection gate; immutable historical material, no API before acceptance."""
import argparse,asyncio,ast,copy,hashlib,json,time
from pathlib import Path
from collections import Counter
from scripts.offline_loop_protocol import atomic_json,read_json,file_hash
from scripts.experiment_b16 import PROTECTED,DEPENDENCIES
from scripts.b17_checkers import check,analyze
ROOT=Path('results/experiment_b17_20261015_v1')
HIST=PROTECTED+['results/experiment_b16_20261015_v1','scripts/python_tool_interface_v3.py','scripts/pattern_type_contract_checker.py']
def snapshot():
 return {str(p):file_hash(p) for d in HIST for p in ([Path(d)] if Path(d).is_file() else Path(d).rglob('*')) if p.is_file() and '__pycache__' not in str(p)}

def prepare():
 if (ROOT/'protocol.json').exists():raise FileExistsError('Already prepared')
 atomic_json(ROOT/'history_before.json',snapshot())
 # New confirmation programs generated and labeled without consulting the checker.
 fixtures=[]
 for i in range(12):
  pat=['01','10','010'][i%3];n=4+i%2
  p=dict(n=n,alphabet=[0,1,2,3],constraints=[dict(kind='pattern_count',pattern=pat,exact=1)])
  style=i%4
  cmp=['tuple(seq[i:i+len(pat)]) == tuple(pat)','list(seq[i:i+len(pat)]) == list(pat)','seq[i:i+len(pat)] == tuple(pat)','tuple(seq[i:i+len(pat)]) == tuple(c["pattern"])'][style]
  head='import json\nfrom itertools import product\ndef solve(problem):\n p=json.loads(problem); n=p["n"]; c=p["constraints"][0]; pat=c["pattern"]; total=0\n for seq in product(p["alphabet"], repeat=n):\n  occ=sum(1 for i in range(n-len(pat)+1) if '+cmp+')\n  if occ==c["exact"]: total+=1\n return total\n'
  fixed=head.replace('tuple(pat)','tuple(map(int,pat))').replace('list(pat)','list(map(int,pat))').replace('tuple(c["pattern"])','tuple(map(int,c["pattern"]))')
  fixtures+=[dict(id=f'confirmation_original_{i}',code=head,problem=json.dumps(p),label='M2',pair=i),dict(id=f'confirmation_repair_{i}',code=fixed,problem=json.dumps(p),label='NON_M2',pair=i)]
 for i in range(4):
  p=dict(n=4+i%2,alphabet=[0,1,2,3],constraints=[dict(kind='pattern_count',pattern='01',exact=1)])
  fixtures.append(dict(id=f'confirmation_other_{i}',code='import json\ndef solve(problem):\n p=json.loads(problem); pat=p["constraints"][0]["pattern"]; x=tuple(map(int,pat))\n return int(x == tuple(map(int,pat)))\n',problem=json.dumps(p),label='NON_M2',note='Wrong complete count, type-consistent comparison; M2 negative, not functional correctness.'))
  fixtures.append(dict(id=f'confirmation_unknown_{i}',code='import json\ndef solve(problem):\n p=json.loads(problem); pat=p["constraints"][0]["pattern"]\n if p["n"]<0:\n  return int((0,1)==tuple(pat))\n return 0\n',problem=json.dumps(p),label='UNKNOWN',note='Pattern comparison unreachable on supplied input.'))
 atomic_json(ROOT/'sealed/confirmation_programs.json',fixtures)
 atomic_json(ROOT/'protocol.json',dict(research='B1.7',stage_A=dict(confirmation_programs=len(fixtures),positives=12,known_negatives=16,unknown=4,gate=dict(min_positive_violation_rate=.8,max_false_violations=0,min_effective_positive_warnings=8,engineering_required=True),one_confirmation_attempt=True,no_post_confirmation_changes=True,confirmation_source='Researcher-authored new small semantic fixtures, independent of historical development examples; not a fresh natural Agent program distribution',wall_per_checker_seconds=3),stages_B_C=dict(max_API_cny=10,stage_B_required_before_C=True,stage_B_checkpoints=6,stage_C_inputs=12,authorization='User explicitly requested design and execution, with conditional online gates. No API before Stage A acceptance.',budget_proposal='Common checkpoint generation <=1 CNY, branches <=1 CNY each, C reserve <=2 CNY each; sum10'),generic='Strong public-schema representation contract, independently specified from schema rather than failure IDs. Shared instrumentation deliberately avoids weakening generic control; it may be extensionally identical to the mechanism checker.',history_roles='All B0/B1/B1.5/B1.6 sources used here reclassified DEVELOPMENT, never independent confirmation',confirmation_sha256=file_hash(ROOT/'sealed/confirmation_programs.json')))
 print('Prepared offline gate; no API. Independent fixture confirmation sealed.',flush=True)

def catalog():
 rows=[]
 for filename in ['results/experiment_b15_20261015_v1/causal_interventions.json','results/experiment_b16_20261015_v1/pilot_causal_interventions.json','results/experiment_b16_20261015_v1/hidden_causal_interventions.json']:
  for r in read_json(Path(filename)):
   if not r.get('verified') or (r.get('mechanism') and r['mechanism']!='M2'):continue
   cases=r.get('checks',r.get('cases'));p=cases[0]['problem'];code=Path(r['code_path']).read_text();fixed=Path(r.get('repaired_source',r.get('repaired_path'))).read_text()
   for variant,src,label in [('original',code,'M2'),('minimal_repair',fixed,'NON_M2')]:rows.append(dict(id=r['evidence_id']+'_'+variant,code=src,problem=json.dumps(p),label=label,source=filename,role='DEVELOPMENT',historical_hidden_reclassified=True))
 # Other defects / uncertainty from reviewed B1.6, without treating unknowns as negatives.
 b16=read_json(Path('results/experiment_b16_20261015_v1/mechanism_audit.json'))
 for r in b16['hidden']+b16['pilot']['rows']:
  if r.get('verified') or not r.get('code_path'):continue
  record=next((read_json(p) for phase in ['hidden','pilot'] for p in Path('results/experiment_b16_20261015_v1',phase+'_events').glob('*_'+r['method']+'.json') if read_json(p)['task']['id']==r['task_id']),None)
  if record:rows.append(dict(id=r['evidence_id'],code=Path(r['code_path']).read_text(),problem=record['task']['problem'],label='NON_M2' if r['classification'] in ('OTHER_SOURCE_DEFECT','EXPECTED_EXPLORATION') else 'UNKNOWN',source='B1.6 source review',role='DEVELOPMENT'))
 # Explicitly retain B0 compound evidence as uncertain, not pretend strict M2-only proof.
 b0=read_json(Path('results/experiment_b0_20261014_v1/diagnostic_trace_audit.json'))
 atomic_json(ROOT/'historical_evidence_scope.json',dict(B0=dict(path='results/experiment_b0_20261014_v1/diagnostic_trace_audit.json',scope='Compound history truncation plus type mismatch; not strict M2-only positive',sha256=file_hash(Path('results/experiment_b0_20261014_v1/diagnostic_trace_audit.json'))),B1=dict(path='results/experiment_b1_20261015_v1/new_source_mechanism_audit.json',scope='Two secondary posthoc M2 records, related source lineage overlaps newer material; kept as Development provenance, not inflated as independent programs',sha256=file_hash(Path('results/experiment_b1_20261015_v1/new_source_mechanism_audit.json')))))
 # Normal full-task programs from real successful C calls are UNKNOWN until independently reviewed.
 atomic_json(ROOT/'development_programs.json',rows)
 return rows

async def audit_unknowns():
 root=Path('results/experiment_b16_20261015_v1/hidden_events');out=[]
 for f in sorted(root.glob('*_C.json')):
  r=read_json(f)
  for e in r['events']:
   raw=r['observation']['events'][e['step_id']].get('tool_result',{})
   if raw.get('contract_check',{}).get('status')!='UNKNOWN':continue
   code=e.get('code');reason='MISSING_SOURCE';sites=[]
   if code:
    try:
     _,sites=analyze(code)
     if not sites:reason='NO_RECOGNIZED_PATTERN_ORIGIN_COMPARISON'
     else:reason='UNRESOLVED_OLD_STATIC_DATAFLOW_OR_SCOPE'
    except (ValueError,SyntaxError,RecursionError) as exc:reason='UNSUPPORTED_OR_PARSE: '+str(exc)
   # Source-reviewed labels only, never infer a miss just from UNKNOWN or wrong output.
   reviewed=read_json(Path('results/experiment_b16_20261015_v1/mechanism_audit.json'))['hidden'];sid='hidden_'+r['task']['id']+'_C_s'+str(e['step_id']);label=next((a['classification'] for a in reviewed if a['evidence_id']==sid),'UNREVIEWED')
   out.append(dict(task_id=r['task']['id'],step=e['step_id'],code=code,source_sha256=hashlib.sha256((code or '').encode()).hexdigest(),old_status='UNKNOWN',root_cause=reason,old_static_limits=['No interprocedural/alias provenance propagation','Cannot infer product/generator/DP tuple destructuring element types','No runtime comparison reachability/value evidence','No knowledge of semantic impact from an answer alone'],confirmed_prior_label=label,known_miss=label=='VERIFIED_M2',not_a_miss_by_default=True))
 assert len(out)==53
 atomic_json(ROOT/'unknown_53_audit.json',dict(calls=out,counts=dict(Counter(r['root_cause'] for r in out)),reviewed_verified_M2_calls=sum(r['known_miss'] for r in out),unreviewed_calls=sum(r['confirmed_prior_label']=='UNREVIEWED' for r in out),old_checker_audit_sha256=file_hash(Path('results/experiment_b16_20261015_v1/checker_audit.json'))))

async def development():
 rows=catalog();await audit_unknowns();results=[]
 for r in rows:
  t=time.monotonic();c=await check(r['code'],r['problem'],3);results.append(dict(id=r['id'],label=r['label'],result=c,wall=time.monotonic()-t))
 atomic_json(ROOT/'development_detection.json',results)
 print(dict(development_programs=len(results),statuses=dict(Counter(r['result']['status'] for r in results))),flush=True)

def freeze():
 if (ROOT/'checker_freeze.json').exists():raise FileExistsError('One confirmation only')
 assert (ROOT/'development_detection.json').exists()
 atomic_json(ROOT/'checker_freeze.json',dict(before_confirmation=True,sources={f:file_hash(Path(f)) for f in ['scripts/b17_checkers.py','scripts/experiment_b17.py']+[str(p) for p in Path('scripts/operator_runtime').glob('*.py')]},protocol_sha256=file_hash(ROOT/'protocol.json'),confirmation_sha256=file_hash(ROOT/'sealed/confirmation_programs.json'),generic_origin='Public task schema only; same observable representation predicate, no historical failure IDs or example-derived constants',modifications=[dict(version=1,reason='Replace static-only lattice with actually executed, pattern-origin comparison evidence and type-only counterfactual return-effect check',diff_from='New checker, frozen B1.6 implementation unchanged')]))

async def confirm():
 freeze=read_json(ROOT/'checker_freeze.json')
 for f,h in freeze['sources'].items():assert file_hash(Path(f))==h,f
 assert file_hash(ROOT/'sealed/confirmation_programs.json')==freeze['confirmation_sha256']
 if (ROOT/'confirmation_detection.json').exists():raise FileExistsError('Confirmation already consumed')
 rows=read_json(ROOT/'sealed/confirmation_programs.json');out=[]
 for r in rows:
  results={}
  for kind in ['generic','mechanism']:results[kind]=await check(r['code'],r['problem'],3,kind)
  out.append(dict(id=r['id'],label=r['label'],results=results))
 atomic_json(ROOT/'confirmation_detection.json',out)
 metrics={}
 for kind in ['generic','mechanism']:
  z=Counter()
  for r in out:
   s=r['results'][kind]['status'];z['n']+=1;z[s]+=1
   if r['label']=='M2':z['positive']+=1;z['TP' if s=='VIOLATION' else 'positive_no_warning']+=1
   elif r['label']=='NON_M2':z['negative']+=1;z['FP' if s=='VIOLATION' else 'negative_no_warning']+=1
   else:z['unknown_label']+=1
  z['positive_coverage']=z['TP']/z['positive'];z['decisive_coverage']=(z['VIOLATION']+z['PASS'])/z['n'];z['precision']=z['TP']/(z['TP']+z['FP']) if z['TP']+z['FP'] else None;metrics[kind]=dict(z)
 m=metrics['mechanism'];gate=read_json(ROOT/'engineering_gate.json')['passed'] and m['TP']>=8 and m['positive_coverage']>=.8 and m.get('FP',0)==0
 atomic_json(ROOT/'stage_A_gate.json',dict(passed=gate,metrics=metrics,status='DETECTION_ESTABLISHED' if gate else 'DETECTION_NOT_ESTABLISHED',confirmation_once=True,API_calls=0))
 print(json.dumps(dict(gate=gate,metrics=metrics)))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','development','freeze','confirm']);a=p.parse_args()
 if a.command=='prepare':prepare()
 elif a.command=='freeze':freeze()
 elif a.command=='development':asyncio.run(development())
 else:asyncio.run(confirm())
