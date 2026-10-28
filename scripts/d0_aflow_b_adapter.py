"""Audited official MBPP round14 control flow, controlled transport and sandbox bridge.
Only offline replay is exposed here. No model client, SDK retry, or credential access.
"""
import ast,asyncio,hashlib,json,re,traceback
from pathlib import Path
from types import SimpleNamespace as NS
from concurrent.futures import ThreadPoolExecutor
from typing import Dict,List,Tuple
from pydantic import BaseModel,Field
from scripts.d0_s01_runtime.runner import execute_case
from scripts.d0_s01_runtime.evaluation import evaluate_task
P=Path('results/experiment_d0_aflow_b_stage0_20261019_v1')
F=P/'frozen_official/results'
class WorkflowFault(Exception):pass
class InfrastructureFault(WorkflowFault):pass
class FormatFault(WorkflowFault):pass
class BudgetFault(WorkflowFault):pass
class EmptyOutput(WorkflowFault):pass
def code_extract(raw):
 if not isinstance(raw,str) or not raw.strip():raise EmptyOutput('empty program')
 blocks=re.findall(r'```(?:python)?\s*\n(.*?)```',raw,re.S)
 code='\n'.join(blocks) if blocks else raw
 try:ast.parse(code)
 except SyntaxError as e:raise FormatFault('unparseable final/program response') from e
 if not code.strip():raise EmptyOutput('empty program')
 return code

def definitions(path,namespace,allowed):
 """Load only audited class definitions, never candidate code or module top-level imports."""
 tree=ast.parse(path.read_text());nodes=[n for n in tree.body if isinstance(n,ast.ClassDef)]
 if set(n.name for n in nodes)!=set(allowed):raise WorkflowFault('Unexpected official class set')
 exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)
 return namespace

def constants(path):
 result={}
 for n in ast.parse(path.read_text()).body:
  if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name):result[n.targets[0].id]=ast.literal_eval(n.value)
 return NS(**result)
class ReplayTransport:
 def __init__(self,responses,max_calls=8):self.responses=list(responses);self.requests=[];self.max_calls=max_calls;self.cost_manager=NS(total_cost=0.0)
 async def request(self,model,context,mode,function_name=None):
  if len(self.requests)>=self.max_calls:raise BudgetFault('logical request cap reached; no fallback draft')
  record={'node':model.__name__,'context':context,'mode':mode,'function_name':function_name,'physical_model_request':False};self.requests.append(record)
  if not self.responses:raise EmptyOutput('no mock response')
  raw=self.responses.pop(0)
  if isinstance(raw,Exception):raise raw
  record['raw_response']=raw
  if not isinstance(raw,dict):raise FormatFault('expected structured response')
  key='solution_letter' if model.__name__=='ScEnsembleOp' else 'reflection_and_solution' if model.__name__=='ReflectionTestOp' else 'response'
  if not isinstance(raw.get(key),str) or not raw[key].strip():raise EmptyOutput('missing/empty '+key)
  # Parse code without changing its algorithm, identifiers, or required interface.
  if mode=='code_fill':raw={**raw,key:code_extract(raw[key])}
  return raw
class Adaptation:
 def __init__(self,public,transport,log_dir):
  self.public=json.loads(json.dumps(public));self.transport=transport;self.log_dir=Path(log_dir);self.events=[];self.locked=False;self.final_source=None
  self.pool=ThreadPoolExecutor(max_workers=1);self.test_seconds=0
 def node_type(self):
  owner=self
  class Node:
   def __init__(self,model):self.model=model
   @classmethod
   def from_pydantic(cls,model):return cls(model)
   async def fill(self,context,llm,mode,**kwargs):
    output=await llm.request(self.model,context,mode,kwargs.get('function_name'))
    owner.events.append({'kind':'model_node','node':self.model.__name__,'mode':mode,'input':context,'output':output})
    self.instruct_content=NS(model_dump=lambda:output);return self
  return Node
 async def check_public(self,code):
  tests=self.public.get('cases',[])
  if not tests:raise WorkflowFault('NO_PUBLIC_TESTS: never vacuously PASS')
  failures=[]
  for case in tests:
   if self.test_seconds>=120:raise BudgetFault('public test wall cap')
   r=await execute_case(code,self.public['interface'],case,self.public.get('judge'),role='self_test',wall=min(6,120-self.test_seconds),log_dir=self.log_dir/'execution');self.test_seconds+=r['elapsed_seconds']
   self.events.append({'kind':'public_test','source':self.public.get('source'),'source_code':code,'case':case,'result':r})
   if r['verdict'] in ['INFRASTRUCTURE_ERROR','JUDGE_ERROR','PROCESS_ERROR']:raise InfrastructureFault(r['verdict'])
   if r['verdict']!='PASS':
    if r['verdict']!='WRONG_ANSWER':return {'exec_fail_case':r.get('error',r['verdict'])}
    failures.append({'test_fail_case':{'test_case':case,'error_type':'AssertionError','error_message':'public output differs','traceback':'isolated execution; parent comparison'}})
  return failures if failures else 'no error'
 def load(self):
  # The official MBPP operator really imports HumanEval schemas/prompts: preserve this dependency.
  schemas=definitions(F/'HumanEval/graphs/template/operator_an.py',{'BaseModel':BaseModel,'Field':Field},['GenerateOp','ScEnsembleOp','ReflectionTestOp'])
  prompts=constants(F/'HumanEval/graphs/template/op_prompt.py')
  ns={'LLM':object,'List':List,'Dict':Dict,'Tuple':Tuple,'ActionNode':self.node_type(),**{k:schemas[k] for k in ['GenerateOp','ScEnsembleOp','ReflectionTestOp']},**vars(prompts)}
  definitions(F/'MBPP/graphs/template/operator.py',ns,['Operator','Custom','CustomCodeGenerate','ScEnsemble','Test'])
  # Preserve the original Test.__call__ exactly; replace only unsafe exec_code boundary.
  def safe_exec(_test,solution,entry_point):
   return self.pool.submit(asyncio.run,self.check_public(solution)).result()
  ns['Test'].exec_code=safe_exec
  ops=NS(**{k:ns[k] for k in ['Custom','CustomCodeGenerate','ScEnsemble','Test']})
  graph={'operator':ops,'prompt_custom':constants(F/'MBPP/graphs_test/round_14/prompt.py'),'create_llm_instance':lambda config:self.transport,'CostManager':lambda:NS(total_cost=0.0),'DatasetType':str}
  definitions(F/'MBPP/graphs_test/round_14/graph.py',graph,['Workflow'])
  return graph['Workflow']('D0-AFlow-B',None,'MBPP')
 async def run(self,problem,entry_point):
  if self.locked:raise WorkflowFault('final source already locked')
  status='WORKFLOW_ERROR';result={}
  try:
   workflow=self.load()
   returned=await asyncio.wait_for(workflow(problem,entry_point),600)
   if not isinstance(returned,tuple) or len(returned)!=2:raise FormatFault('workflow return contract')
   source=code_extract(returned[0]);self.final_source=source;self.locked=True
   result={'status':'RETURNED_LOCKED','source':source,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'workflow_cost':returned[1]};status=result['status']
  except Exception as e:
   result={'status':type(e).__name__,'error':str(e),'no_intermediate_promotion':True};status=result['status']
  finally:
   self.pool.shutdown(wait=True);self.log_dir.mkdir(parents=True,exist_ok=True)
   result.update(public_test_seconds=self.test_seconds,events=self.events,requests=self.transport.requests,real_API_calls=sum(bool(q.get("physical_model_request",False)) for q in self.transport.requests))
   (self.log_dir/'replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
  return result
