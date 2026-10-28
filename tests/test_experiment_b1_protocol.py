import unittest,json

class B1ProtocolTests(unittest.TestCase):
 def test_fixed_disjoint_input_roles_and_pairs(self):
  from pathlib import Path
  from scripts.offline_loop_protocol import read_json,digest
  root=Path('results/experiment_b1_20261015_v1');ds=read_json(root/'inputs/diagnostics.json');hidden=read_json(root/'sealed/confirmation.json');natural=read_json(root/'inputs/natural_discovery.json')
  sets=[{digest(json.loads(r['problem'])) for r in rs} for rs in (ds,hidden,natural)]
  self.assertEqual([len(s) for s in sets],[20,8,60])
  for i in range(3):
   for j in range(i):self.assertFalse(sets[i]&sets[j])
  for i in range(10):
   a=next(json.loads(r['problem']) for r in ds if r['pair']==i and r['variant']=='trigger');b=next(json.loads(r['problem']) for r in ds if r['pair']==i and r['variant']=='control');a['constraints']=[c for c in a['constraints'] if c['kind']!='endpoints'];self.assertEqual(a,b)
 def test_complete_schedule_and_stable_limits(self):
  from pathlib import Path
  from scripts.offline_loop_protocol import read_json
  root=Path('results/experiment_b1_20261015_v1');self.assertEqual(read_json(root/'agent_schedule.json')['n_trials'],72)
  for arm in ['baseline','reflection']:
   m=read_json(root/f'agent_{arm}/manifest.json');self.assertEqual(m['runtime_version'],'operator-runtime-v1');self.assertEqual(m['api_budget_cny'],4)
   self.assertEqual(len(list((root/f'agent_{arm}/observations').glob('*.json'))),36)
 def test_confirmation_not_used_in_source_repairs(self):
  from pathlib import Path
  from scripts.offline_loop_protocol import read_json
  a=read_json(Path('results/experiment_b1_20261015_v1/new_source_mechanism_audit.json'));self.assertFalse(a['confirmation_inputs_used_for_repairs']);self.assertTrue(all(x['study_stage']=='diagnostic' for x in a['verified']+a['secondary_posthoc']))
 def test_interventions_distinguish_compound_causes(self):
  from pathlib import Path
  from scripts.offline_loop_protocol import read_json
  a=read_json(Path('results/experiment_b1_20261015_v1/new_source_mechanism_audit.json'));c=next(r for r in a['audits'] if r['compound_defect']);t={r['variant']:r['correct'] for r in c['tests'] if r['role']=='original_input'}
  self.assertEqual(t,{'original':False,'first_context_only':False,'count_only':False,'both':True})
  self.assertFalse(any(x['compound_defect'] for x in a['verified']))
  self.assertEqual(len(a['related_compound_defects']),1)
