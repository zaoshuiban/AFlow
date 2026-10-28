import json,unittest
from scripts.experiment_b0 import ROOT,relevant,verify
from scripts.report_experiment_b0 import paired
from scripts.offline_loop_protocol import read_json,digest

class B0Tests(unittest.TestCase):
 def test_frozen_split_isolation(self):
  verify();pools=[read_json(ROOT/'inputs/diagnostics.json'),read_json(ROOT/'inputs/operator_development.json'),read_json(ROOT/'sealed/hidden.json')]
  sets=[{digest(json.loads(r['problem'])) for r in rs} for rs in pools]
  self.assertEqual([len(x) for x in sets],[20,16,44])
  for i in range(3):
   for j in range(i):self.assertFalse(sets[i]&sets[j])
 def test_controlled_pairs(self):
  rs=read_json(ROOT/'inputs/diagnostics.json')
  for j in range(10):
   a,b=[json.loads(r['problem']) for r in rs if r['pair']==j]
   self.assertEqual(a['n'],b['n']);self.assertEqual(a['alphabet'],b['alphabet']);self.assertEqual([c['kind'] for c in a['constraints']],[c['kind'] for c in b['constraints']])
   self.assertTrue(relevant(a));self.assertFalse(relevant(b))
 def test_pairs_count_regressions(self):
  a={'a':{'correct':True},'b':{'correct':False}};b={'a':{'correct':False},'b':{'correct':True}}
  self.assertEqual(paired(a,b),dict(n=2,both_correct=0,both_wrong=0,wins=1,losses=1))
 def test_human_control_is_one_line_not_generated_operator(self):
  a=(ROOT/'discovery/original.py').read_text();b=(ROOT/'discovery/causal_repair.py').read_text()
  self.assertEqual(a.replace('newstate = (state + (d,))[-2:]','newstate = (state + (d,))[-3:]'),b)

if __name__=='__main__':unittest.main()
