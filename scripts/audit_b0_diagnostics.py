"""Post-diagnosis causal checks; never candidate source or generation feedback."""
import asyncio,copy
from scripts.experiment_b0 import ROOT
from scripts.offline_loop_protocol import read_json,atomic_json
from scripts.experiment_a_calibration import execute
from scripts.compositional_counting import exact_dp,brute_force
import json

async def audit():
 checks=[]
 task=next(r for r in read_json(ROOT/'inputs/diagnostics.json') if r['id']=='92088c8c1b8a6f763b81')
 obs=read_json(ROOT/'diagnostic_agent/observations'/f"{task['id']}_reflection.json")
 code=obs['events'][0]['arguments']['code']
 def change(c,history,types):
  if history:
   c=c.replace('                if len(ns) > maxlen-1:\n                    ns = ns[-(maxlen-1):]\n','')
   c=c.replace('ndp[ns] += cnt','ndp[ns[-(maxlen-1):]] += cnt')
  if types:c=c.replace('tuple(pat)','tuple(int(x) for x in pat)')
  return c
 for n in (6,10):
  p=json.loads(task['problem']);p['n']=n;truth=exact_dp(p)['answer']
  if n==6:assert truth==brute_force(p)
  for h,t in [(False,False),(True,False),(False,True),(True,True)]:
   c=change(code,h,t);r=await execute(c,json.dumps(p),6);checks.append(dict(task_id=task['id'],n=n,history_intervention=h,type_intervention=t,expected=truth,result=r,correct=r.get('value')==truth and r['status']=='success'))
 task=next(r for r in read_json(ROOT/'inputs/diagnostics.json') if r['id']=='c75c08437f7aee11656a');obs=read_json(ROOT/'diagnostic_agent/observations'/f"{task['id']}_reflection.json")
 code=obs['events'][1]['arguments']['code'];fixed=code.replace('prev is not None and str(prev)+str(a)','last is not None and str(last)+str(a)');assert code!=fixed
 r=await execute(fixed,task['problem'],6)
 atomic_json(ROOT/'diagnostic_trace_audit.json',dict(post_generation=True,not_fed_to_synthesis=True,assisted=True,
  recovered_case=dict(id='92088c8c1b8a6f763b81',description='First tool truncates before matching AND compares tuple of integers with tuple of characters. Neither single intervention repairs; both together do. Agent eventually recovers via enumeration.',factorial_checks=checks),
  control_failure=dict(id=task['id'],description='Length2 control failed: first code explicitly skipped multi-symbol patterns; second uses prev rather than last, comparing distance2 symbols. One-line transition fix repairs final code.',original_final=obs['answer'],expected=task['expected'],intervention_result=r),
  conclusion='Specific interacting local history defect reproduced in one successful Agent trajectory. No final trigger failure; a control final failure and wrong-distance transition refute overly narrow long-pattern-only generality.'))
 print([(r['n'],r['history_intervention'],r['type_intervention'],r['correct']) for r in checks]);print('control_fixed',r.get('value'),task['expected'])

if __name__=='__main__':asyncio.run(audit())
