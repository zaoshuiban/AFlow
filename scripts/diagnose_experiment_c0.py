"""Post-pilot counterexample reduction only. Never changes candidate sources."""
import asyncio,copy,json,time
from pathlib import Path
from scripts.experiment_c0 import ROOT,canonical,validate_problem,exact_dp,brute_force,verify
from scripts.operator_runtime import execute,Resources
from scripts.offline_loop_protocol import atomic_json,file_hash
CAP=180
async def main():
 verify()
 if (ROOT/'diagnostic_outputs.json').exists():raise FileExistsError('No rerun')
 rows=[json.loads(s) for s in (ROOT/'executions.jsonl').read_text().splitlines()];truth=json.loads((ROOT/'oracle_outputs.json').read_text());inventory={x['id']:x for x in json.loads((ROOT/'candidate_inventory.json').read_text())};chosen=[]
 for cid in sorted({x['candidate'] for x in rows if x['functional_failure']}):
  failed={x['problem_sha256']:truth[x['problem_sha256']]['problem'] for x in rows if x['candidate']==cid and x['functional_failure']}
  selected=[]
  for kind in ['forbidden','endpoints','adjacent']:
   pool=[p for p in failed.values() if kind in {c['kind'] for c in p['constraints']}]
   if pool:
    p=min(pool,key=lambda p:(len(p['constraints']),p['n'],canonical(p)))
    if p not in selected:selected.append(p)
  for p in selected:chosen.append(dict(candidate=cid,initial=p))
 atomic_json(ROOT/'diagnostic_protocol.json',dict(posthoc=True,not_counted_in_primary_budget=True,cap=CAP,reduction='Remove constraints greedily, then reduce n without altering remaining parameter values. Fixed point; not global minimum over symbols/patterns.',selection=chosen,source_edits=False,script_sha256=file_hash(Path(__file__))))
 trials=[];reduced=[]
 async def test(cid,p):
  if len(trials)>=CAP:return None
  try:validate_problem(p)
  except ValueError:return None
  t=time.monotonic();dp=exact_dp(p);b=brute_force(p);assert dp['answer']==b;refwall=time.monotonic()-t
  start=time.monotonic();out=await execute(Path(inventory[cid]['source']).read_text(),canonical(p),1,resources=Resources(cpu_seconds=3));wall=time.monotonic()-start
  bad=out.get('status')=='success' and out.get('value')!=b
  trial=dict(candidate=cid,problem=copy.deepcopy(p),expected=b,execution=out,functional_failure=bad,wall_seconds=wall,reference_seconds=refwall);trials.append(trial);return trial
 for row in chosen:
  cid=row['candidate'];p=copy.deepcopy(row['initial']);witness=await test(cid,p);assert witness['functional_failure']
  changed=True
  while changed:
   changed=False
   for i in range(len(p['constraints'])):
    q=copy.deepcopy(p);del q['constraints'][i];x=await test(cid,q)
    if x and x['functional_failure']:p=q;witness=x;changed=True;break
   if changed:continue
   for n in range(1,p['n']):
    q=copy.deepcopy(p);q['n']=n;x=await test(cid,q)
    if x and x['functional_failure']:p=q;witness=x;changed=True;break
  reduced.append(dict(candidate=cid,initial=row['initial'],reduced=p,witness=witness,minimality='1-minimal for constraint deletion and smaller valid n; not globally minimal for all transformations',cap_exhausted=len(trials)>=CAP))
 atomic_json(ROOT/'diagnostic_outputs.json',dict(reduced=reduced,trials=trials,executions=len(trials),sources_unchanged=True));print(dict(reduced=len(reduced),executions=len(trials)),flush=True)
if __name__=='__main__':asyncio.run(main())
