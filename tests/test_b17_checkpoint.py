import json,tempfile,time,unittest
from pathlib import Path
from tests.test_experiment_b16_interface import Mock,GOOD
from scripts.b17_checkpoint_study import ResumeTransport,ResumeExecutor
from scripts.operator_runtime import solve
class Tests(unittest.IsolatedAsyncioTestCase):
 def checkpoint(self,elapsed=1):
  code=GOOD.replace('*2','+2');return dict(code=code,task=dict(id='cp',problem='{"n":3}',expected=6),elapsed_seconds=elapsed,response=dict(message=dict(content=None,tool_calls=[dict(id='shared',type='function',function=dict(name='python',arguments=json.dumps({'code':code})))]),usage=dict(input_tokens=10,output_tokens=20)),tool_result=dict(status='success',value=5,result=5,execution_status='SUCCESS',elapsed_seconds=.01,runtime_ms=10,validation_status='NOT_CHECKED'))
 async def test_shared_first_call_not_resent_and_final_submit(self):
  cp=self.checkpoint();base=Mock([('python',{'code':GOOD}),('submit',{'value':6})]);start=time.monotonic()
  with tempfile.TemporaryDirectory() as d:
   out=await solve(Path(d),ResumeTransport(base,cp,start),cp['task'],'reflection','public',executor=ResumeExecutor(cp,'R',start))
   self.assertTrue(out['correct']);self.assertEqual(len(base.requests),2);self.assertEqual(out['calls'],3);self.assertEqual(out['output_tokens'],60)
 async def test_common_wall_already_spent_stops_continuation(self):
  cp=self.checkpoint(181);base=Mock([]);start=time.monotonic()
  with tempfile.TemporaryDirectory() as d:
   out=await solve(Path(d),ResumeTransport(base,cp,start),cp['task'],'reflection','public',executor=ResumeExecutor(cp,'R',start));self.assertEqual(out['error'],'infrastructure_interrupted');self.assertEqual(len(base.requests),0)
