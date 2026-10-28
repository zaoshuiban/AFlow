"""Post-execution minimal causal audits, diagnostic inputs only; no new operator."""
import asyncio,copy,difflib,json
from pathlib import Path
from scripts.experiment_b1 import ROOT
from scripts.offline_loop_protocol import read_json,atomic_json,digest
from scripts.compositional_counting import exact_dp,brute_force
from scripts.operator_runtime import execute,Resources

async def audit():
 rows=[read_json(p) for p in (ROOT/'detected_new').glob('*.json')];audits=[];verified=[];secondary=[];related=[]
 def find(tid,arm,step):
  r=next(r for r in rows if r['base_task']['id']==tid and r['arm']==arm and r['repeat']==0);e=next(e for e in r['events'] if e['step_id']==step);assert r['stage']=='diagnostic';return r,e
 async def check(r,e,variants,mechanism,subtype,component=False):
  d=ROOT/'posthoc_causal_audit'/f"{r['base_task']['id']}_{r['arm']}_{e['step_id']}";d.mkdir(parents=True,exist_ok=True)
  for name,code in variants.items():(d/f'{name}.py').write_text(code)
  tests=[]
  for role in ('original_input','endpoint_removed') if mechanism=='M1_missing_first_symbol' else ('original_input','small_n7'):
   p=json.loads(r['base_task']['problem'])
   if role=='endpoint_removed':p['constraints']=[c for c in p['constraints'] if c['kind']!='endpoints']
   if role=='small_n7':p['n']=7
   truth=exact_dp(p)['answer']
   if p['n']<=7:assert truth==brute_force(p)
   for name,code in variants.items():
    out=await execute(code,json.dumps(p),6,resources=Resources(cpu_seconds=5));tests.append(dict(role=role,variant=name,expected=truth,result=out,correct=out['status']=='success' and out['value']==truth))
  last=list(variants)[-1];passed=all(t['correct'] for t in tests if t['variant']==last)
  (d/'change.diff').write_text(''.join(difflib.unified_diff(variants['original'].splitlines(True),variants[last].splitlines(True))))
  a=dict(mechanism=mechanism,subtype=subtype,compound_defect=component,base_task_id=r['base_task']['id'],task_id=e['task_id'],arm=r['arm'],study_stage=r['stage'],repeat=0,step_id=e['step_id'],tests=tests,repair_supported=passed,scope='diagnostic Development causal audit; never Agent feedback or confirm-set modification')
  audits.append(a);atomic_json(d/'audit.json',a)
  if passed:
   record={k:a[k] for k in ('mechanism','subtype','compound_defect','base_task_id','task_id','arm','study_stage','repeat','step_id')};record['audit_path']=str(d/'audit.json')
   if component:
    record['not_exact_prespecified_replication']=True;record['reason']='External mutable memo context is a distinct compound defect; count-only variant undercounts, not the frozen omission-overcount prediction.';related.append(record)
   else:(verified if mechanism=='M1_missing_first_symbol' else secondary).append(record)
 # M1a: first symbol absent; old endpoint condition only repeats adjacency.
 r,e=find('ba6eb2700b1bdd340c0e','baseline',2);c=e['code'];fix=c.replace('def dp(pos, last, used):','def dp(pos, last, used, first):').replace('key = (pos, last, used)','key = (pos, last, used, first)').replace('r = 1 if used == cnt_t else 0','r = 1 if used == cnt_t and (not endp or last != first) else 0').replace('            if endp and pos == n-1 and last != -1 and s == last:\n                continue\n','').replace('dp(pos+1, s, nu)','dp(pos+1, s, nu, s if pos == 0 else first)').replace('dp(0, -1, 0)','dp(0, -1, 0, -1)');assert c!=fix
 await check(r,e,dict(original=c,first_state_repair=fix),'M1_missing_first_symbol','missing first state, wrong adjacent comparison')
 # M1b: memo depends on external mutable first but key omits it; simultaneous exact-count omission.
 r,e=find('a9f0bdbe131754e7d071','baseline',2);c=e['code'];assert 'first[0] = d' in c
 context=c.replace('        first[0] = d','        first[0] = d\n        memo.clear()')
 def exact_count(x):return x.replace('        if pos == n:\n','        if pos == n:\n            if counts != tuple(cnt[s] for s in tracked):\n                return 0\n')
 await check(r,e,dict(original=c,first_context_only=context,count_only=exact_count(c),both=exact_count(context)),'M1_missing_first_symbol','external first omitted from memo context + count not exact',component=True)
 # M2 type-only conversions on two distinct diagnostic tasks, not a planned M2 Agent trial.
 r,e=find('29fd9652186ed13194b9','baseline',1);c=e['code'];fix=c.replace('tuple(pat)','tuple(int(ch) for ch in pat)');assert c!=fix
 await check(r,e,dict(original=c,type_repair=fix),'M2_pattern_type_mismatch','integer tuple vs character tuple')
 r,e=find('793693faf1ca8376aa68','baseline',2);c=e['code'];fix=c.replace('seq[i+j] == pat[j]','seq[i+j] == int(pat[j])');assert c!=fix
 await check(r,e,dict(original=c,type_repair=fix),'M2_pattern_type_mismatch','integer element vs character comparison')
 # Check all successful/failed new tool steps were scanned, not just source samples.
 all_events=[e for r in rows for e in r['events']]
 unknown=[dict(task_id=e['task_id'],step_id=e['step_id'],reason='wrong integer or execution issue not uniquely attributed; no forced mechanism') for e in all_events if e['local_failure'] and (e['task_id'],e['step_id'],e['arm']) not in {(x['task_id'],x['step_id'],x['arm']) for x in verified+secondary}]
 atomic_json(ROOT/'new_source_mechanism_audit.json',dict(verified=verified,secondary_posthoc=secondary,related_compound_defects=related,audits=audits,unknown_candidates=unknown,all_new_steps=len(all_events),all_new_wrong_integer_steps=sum(e['local_failure'] and e['error_type']=='CODE_WRONG_RESULT' for e in all_events),all_new_integration_errors=sum(e['failure_family']=='Tool Integration' and e['local_failure'] for e in all_events),confirmation_inputs_used_for_repairs=False,independently_verified_confirmation_instances=0,not_fed_to_Agent=True,assisted=True,note='Strict frozen M1 is endpoint omission/overcount from missing first state. Mutable-memo-context compound defect is related but NOT counted as same-mechanism replication. Only diagnostic Development programs repaired; confirmation kept sealed from repair tuning.'))
 print(dict(M1_verified=len(verified),M2_secondary=len(secondary),audits=[(a['subtype'],a['repair_supported']) for a in audits]),flush=True)

if __name__=='__main__':asyncio.run(audit())
