"""Parent-only evaluation and model-facing capabilities; no API client."""
import time
from .runner import execute_case,admit,public_task
async def evaluate_task(code,task,*,role='hidden',log_dir=None,task_wall=30):
 gate=admit(task)
 if not gate['admitted']:return {'verdict':'NOT_ADMITTED','admission':gate,'results':[]}
 start=time.monotonic();results=[];verdict='PASS'
 for case in task['cases']:
  remaining=task_wall-(time.monotonic()-start)
  if remaining<=0:verdict='EVALUATOR_BUDGET_EXHAUSTED';break
  result=await execute_case(code,task['interface'],case,task.get('judge'),role=role,wall=min(task.get('case_wall',2),remaining),limits=task.get('limits'),log_dir=log_dir);results.append(result)
  if result['verdict']!='PASS':verdict=result['verdict'];break
 return {'verdict':verdict,'results':results,'executed_cases':len(results),'total_cases':len(task['cases']),'all_cases_executed':len(results)==len(task['cases']),'elapsed_seconds':time.monotonic()-start}
class EngineeringSession:
 """S0.1 mock capability boundary; not an authorized S1 execution driver."""
 def __init__(self,task,log_dir=None):self.task=task;self.log_dir=log_dir;self.phase='OPEN';self.source=None;self.tool_seconds=0;self.self_test_calls=0
 def model_input(self):
  if self.phase!='OPEN':raise ValueError('No model continuation after submit')
  return public_task(self.task)
 async def self_test(self,code,test_input):
  if self.phase!='OPEN':raise ValueError('Tools closed after submit')
  if self.tool_seconds>=10:raise ValueError('Online tool budget exhausted')
  case={k:test_input[k] for k in ['stdin','args','kwargs'] if k in test_input};case['expected']=None
  r=await execute_case(code,self.task['interface'],case,role='self_test',wall=min(2,10-self.tool_seconds),log_dir=self.log_dir);self.tool_seconds+=r['elapsed_seconds'];self.self_test_calls+=1
  return {k:r[k] for k in ['status','value','stdout','stderr','error','elapsed_seconds'] if k in r}
 def submit(self,code):
  if self.phase!='OPEN':raise ValueError('Submission already frozen')
  self.source=code;self.phase='SUBMITTED'
 async def final_evaluate(self):
  if self.phase!='SUBMITTED':raise ValueError('Must submit before hidden evaluation')
  self.phase='CLOSED';return await evaluate_task(self.source,self.task,role='hidden',log_dir=self.log_dir)
