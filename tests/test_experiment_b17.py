import unittest
from scripts.b17_checkers import analyze,instrument,check
from scripts.operator_runtime import execute,Resources
CODE='import json\ndef solve(problem):\n p=json.loads(problem); pat=p["constraints"][0]["pattern"]; total=0\n for x in p["alphabet"]:\n  if x == pat[0]: total+=1\n return total'
P='{"alphabet":[0,1,2,3],"constraints":[{"kind":"pattern_count","pattern":"0","exact":1}],"n":1}'
class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_probe_preserves_original_semantics(self):
  tree,sites=analyze(CODE);out=await execute(instrument(CODE,tree,sites,0),P,3,resources=Resources(cpu_seconds=5));self.assertEqual(out['value'],0)
 async def test_active_representation_violation(self):
  out=await check(CODE,P);self.assertEqual(out['status'],'VIOLATION');self.assertNotIn('answer',out)
 async def test_correct_normalization_pass(self):
  out=await check(CODE.replace('x == pat[0]','x == int(pat[0])'),P);self.assertEqual(out['status'],'PASS')
 async def test_dead_comparison_abstains(self):
  c='import json\ndef solve(problem):\n p=json.loads(problem);pat=p["constraints"][0]["pattern"]\n if p["n"]<0: return int((0,)==tuple(pat))\n return 0'
  self.assertEqual((await check(c,P))['status'],'ABSTAIN')
