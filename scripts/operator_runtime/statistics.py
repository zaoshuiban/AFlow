"""Separate execution, functional correctness and final task/cost metrics."""
from collections import Counter

def summarize(rows,cost_records=()):
 n=len(rows);correct=sum(r.get('correct') is True for r in rows);tool=[]
 for r in rows:
  tool.extend(e for e in r.get('events',[]) if e.get('tool')=='operator')
 known=sum(c.get('known_cost_cny',0) for c in cost_records);phases={}
 for c in cost_records:
  phase='infrastructure_retry' if c.get('retry_index',0)>0 else c.get('phase','unknown')
  group=phases.setdefault(phase,dict(requests=0,known_estimate_cny=0,unknown_usage_requests=0));group['requests']+=1;group['known_estimate_cny']+=c.get('known_cost_cny',0);group['unknown_usage_requests']+=c.get('usage') is None
 return dict(agent_final_tasks=n,agent_final_correct=correct,accuracy=correct/n if n else None,
  operator_execution_success=sum(e['result']['execution_status']=='SUCCESS' for e in tool),operator_invocations=len(tool),
  operator_functional_correct=sum(e.get('functional_correctness') is True for e in tool),operator_functional_unknown=sum('functional_correctness' not in e for e in tool),
  generation_validation_note='Generation response and admission success are separate lifecycle/attempt metrics; execution is never proof of correctness.',
  known_api_estimate_cny=known,actual_invoice_cny=None,unknown_usage_requests=sum(c.get('usage') is None for c in cost_records),cost_per_correct_known_estimate_cny=known/correct if correct else None,
  mean_known_cost_cny=known/n if n else None,cost_phases=phases,
  efficiency_claim_allowed=False,note='Report accuracy and cost per correct jointly; no claim that fast wrong answers are useful efficiency gains.')
