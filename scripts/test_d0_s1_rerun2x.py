"""Offline driver acceptance: mocked model transport, real native sandbox."""
import asyncio,json,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
import scripts.experiment_d0_s1_rerun2x as m
class FakeResponse:
 def __init__(self,name,code,index):
  a={'code':code}
  if name=='self_test':a['test_input']={'args':['3902']}
  self.choices=[NS(message=NS(content='',tool_calls=[NS(id=f't{index}',function=NS(name=name,arguments=json.dumps(a)))]))]
  self.usage=NS(prompt_tokens=100,completion_tokens=100,model_dump=lambda:{'prompt_tokens':100,'completion_tokens':100})
  self.model='MOCK_NO_API'
 def model_dump(self):return {'mock':True,'model':self.model}
class Client:
 def __init__(self,code,fail=False):self.chat=NS(completions=self);self.n=0;self.code=code;self.fail=fail
 async def create(self,**kw):
  self.n+=1
  if self.fail:raise ConnectionError('mock')
  if self.n==2:
   # Force final path in mocked test by exhausting most output budget.
   pass
  r=FakeResponse('self_test' if self.n==1 else 'submit',self.code,self.n)
  return r
async def main():
 original=m.OUT;m.OUT=Path(tempfile.mkdtemp());tid='lcb-v6-3768';public=m.load(m.OLD/'s1_public_tasks.json');backend=m.load(m.OLD/'s1_backend_tasks.json');code=Path('results/experiment_d0_s1_20261019_v1/tasks/lcb-v6-3768/final.py').read_text()
 ledger={'cap_cny':4,'committed_upper_cny':0,'calls':[]}
 row=await m.one(tid,Client(code),ledger,public,backend)
 assert row['status']=='SUBMITTED' and row['verdict']=='PASS' and row['first_verdict']=='PASS',row
 assert row['calls']==2 and ledger['committed_upper_cny']<.01
 try:
  s=m.DoubledSession({'interface':public[tid]['interface']});s.submit(code);await s.self_test(code,{})
  assert False
 except ValueError:pass
 m.OUT=Path(tempfile.mkdtemp());l={'cap_cny':4,'committed_upper_cny':0,'calls':[]};c=Client(code,True)
 r=await m.one(tid,c,l,public,backend)
 assert r['status']=='API_INFRASTRUCTURE_ERROR' and c.n==1 and l['committed_upper_cny']>0
 src=Path(m.__file__).read_text();assert "'tools':[TOOLS[1]] if final else TOOLS" in src and "role='diagnostic'" in src
 m.save(original/'offline_acceptance.json',{'passed':True,'api_calls':0,'checks':['mock request -> real native sandbox self_test -> submit -> hidden PASS -> first diagnostic PASS','submission lock','unknown delivery retains positive reservation and does not retry','submit-only schema for final phase','diagnostic role supported'],'mock_artifact_directories_not_confirmation':True})
 print('Offline acceptance PASS; real model calls: 0')
if __name__=='__main__':asyncio.run(main())
