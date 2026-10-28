import asyncio,json,tempfile,unittest
from pathlib import Path
from scripts.operator_runtime import solve
from scripts.python_tool_interface_v3 import InterfaceTransport,executor,source_adapter,CONTRACT
from scripts.operator_runtime.executor import static_check
GOOD='import json\ndef solve(problem):\n return json.loads(problem)["n"]*2'
class Mock:
 controller=None
 def __init__(self,items):self.items=items;self.requests=[]
 async def call(self,key,messages,output_cap,**kwargs):
  self.requests.append(dict(key=key,messages=messages,cap=output_cap,tools=kwargs.get('tools')))
  name,args=self.items.pop(0)
  return dict(message=dict(content=None,tool_calls=[dict(id=str(len(self.requests)),type='function',function=dict(name=name,arguments=json.dumps(args)))]),usage=dict(input_tokens=10,output_tokens=20))
class Tests(unittest.IsolatedAsyncioTestCase):
 async def runmock(self,items):
  with tempfile.TemporaryDirectory() as d:
   base=Mock(items);r=await solve(Path(d),InterfaceTransport(base),dict(id='mock',problem='{"n":3}',expected=6),'reflection','Mock public protocol',executor=executor)
   self.assertTrue(all(CONTRACT in q['messages'][0]['content'] for q in base.requests));self.assertLessEqual(r['calls'],4)
   return r,base
 async def test_generation_review_rewrite_reexecute_submit(self):
  r,b=await self.runmock([('python',{'code':GOOD}),('python',{'code':GOOD.replace('*2','+3')}),('python',{'code':GOOD}),('submit',{'value':6})]);self.assertTrue(r['correct']);self.assertEqual(r['tools'],3);self.assertEqual([t['function']['name'] for t in b.requests[-1]['tools']],['submit'])
 async def test_runtime_error_then_recovery(self):
  r,_=await self.runmock([('python',{'code':'def solve(problem):\n return 1//0'}),('python',{'code':GOOD}),('submit',{'value':6})]);self.assertTrue(r['correct']);self.assertEqual(r['raw_tool_events'][0]['tool_result']['error_type'],'CODE_RUNTIME_ERROR')
 async def test_draft_submission_review_final(self):
  r,_=await self.runmock([('submit',{'value':6}),('python',{'code':GOOD}),('submit',{'value':6})]);self.assertTrue(r['correct']);self.assertEqual(r['calls'],3)
 async def test_wrong_math_remains_wrong(self):
  code=GOOD.replace('*2','+2');self.assertEqual(source_adapter(code),code);r=await executor(code,'{"n":3}',3);self.assertEqual(r['value'],5)
 async def test_return_contract(self):
  for text in ['"6"','True','{}']:
   with self.subTest(text=text):
    r=await executor('def solve(problem):\n return '+text,'{}',3);self.assertEqual(r['error_type'],'CODE_CONTRACT_ERROR')
 def test_prohibited_review_shapes(self):
  for code in ['import os\n'+GOOD,GOOD+'\nprint(solve("{}"))',GOOD+'\nx=3','from functools import lru_cache\n@lru_cache(None)\n'+GOOD.replace('import json\n',''),'def solve(problem: str) -> int:\n return 0',GOOD+'\nassert 1==1']:
   with self.subTest(code=code):
    with self.assertRaises(ValueError):static_check(source_adapter(code))
