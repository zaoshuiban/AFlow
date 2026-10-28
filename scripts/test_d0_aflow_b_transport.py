"""Exercise real adapter transport with mock HTTP client, API cost zero."""
import asyncio,json,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from scripts.d0_aflow_b_transport import BoundedTransport
from scripts.d0_aflow_b_adapter import P,BudgetFault,FormatFault
from pydantic import BaseModel
class GenerateOp(BaseModel):response:str=''
class ReflectionTestOp(BaseModel):reflection_and_solution:str=''
class ScEnsembleOp(BaseModel):thought:str='';solution_letter:str=''
class Client:
 def __init__(self,seq):self.seq=list(seq);self.chat=NS(completions=self);self.count=0
 async def create(self,**kw):
  self.count+=1;item=self.seq.pop(0)
  if isinstance(item,Exception):raise item
  usage=NS(prompt_tokens=100,completion_tokens=20,model_dump=lambda:{'prompt_tokens':100,'completion_tokens':20})
  return NS(model='MOCK',usage=usage,choices=[NS(message=NS(content=item))],model_dump=lambda:{'mock':True,'content':item})
async def main():
 checks=[]
 c=Client(['analysis','```python\ndef f(x):\n return x+1\n```','<thought>x</thought><solution_letter>A</solution_letter>','def f(x):\n return x+1'])
 t=BoundedTransport(c,Path(tempfile.mkdtemp()),1)
 assert (await t.request(GenerateOp,'spec','single_fill'))['response']=='analysis'
 assert (await t.request(GenerateOp,'spec','code_fill','f'))['response'].startswith('def f(')
 assert (await t.request(ScEnsembleOp,'spec','context_fill'))['solution_letter']=='A'
 assert 'reflection_and_solution' in await t.request(ReflectionTestOp,'spec','code_fill')
 checks.append('four node schema/mode contracts including corrective output')
 c=Client([ConnectionError('mock')]);t=BoundedTransport(c,Path(tempfile.mkdtemp()),1)
 try:await t.request(GenerateOp,'spec','single_fill');assert False
 except ConnectionError:assert t.committed>0 and c.count==1 and t.requests[-1]['status']=='UNKNOWN_DELIVERY'
 checks.append('failed request retained positive cost reservation and not retried')
 c=Client(['unused']);t=BoundedTransport(c,Path(tempfile.mkdtemp()),0)
 try:await t.request(GenerateOp,'spec','single_fill');assert False
 except BudgetFault:assert c.count==0
 checks.append('cost cap checked before physical request')
 c=Client(['bad format']);t=BoundedTransport(c,Path(tempfile.mkdtemp()),1)
 try:await t.request(ScEnsembleOp,'spec','context_fill');assert False
 except FormatFault:assert t.committed>0
 checks.append('malformed paid response remains charged and errors explicitly')
 (P/'transport_offline_acceptance.json').write_text(json.dumps({'passed':True,'checks':checks,'real_API_calls':0,'ledger_temporaries':'mock-only fixtures; no provider invoice'},ensure_ascii=False,indent=2));print('transport checks',len(checks),'API 0')
if __name__=='__main__':asyncio.run(main())
