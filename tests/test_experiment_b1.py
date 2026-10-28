import unittest,json
from scripts.b1_detection import detect
from scripts.offline_loop_protocol import digest

class LocalDetectionTests(unittest.TestCase):
 def task(self):return {'id':'x','problem':'{}','expected':4}
 def observation(self,result,correct=True,**extra):return {'correct':correct,'events':[{'function':'python','tool_result':result,**extra}]}
 def test_wrong_full_return_verified_recovery(self):
  e=detect(self.task(),self.observation({'status':'success','value':0}))[0];self.assertTrue(e['local_failure']);self.assertEqual(e['recovery_status'],'RECOVERED_LOCAL_FAILURE')
 def test_unexecuted_or_refusal_not_verified_error(self):
  self.assertFalse(detect(self.task(),{'correct':True,'events':[{'function':'python','arguments':{'code':'bad'}}]}))
  self.assertFalse(detect(self.task(),self.observation({'status':'declined','value':None}))[0]['local_failure'])
 def test_assertion_in_probe_only_suspected(self):
  e=detect(self.task(),self.observation({'status':'error','error':'AssertionError: hypothesis counterexample'}))[0];self.assertFalse(e['local_failure']);self.assertEqual(e['local_status'],'SUSPECTED_LOCAL_FAILURE')
 def test_explicit_subproblem_not_compared_to_full_truth(self):
  e=detect(self.task(),self.observation({'status':'success','value':1},declared_scope='subproblem'))[0];self.assertFalse(e['local_failure']);self.assertEqual(e['local_status'],'EXPECTED_EXPLORATION')
 def test_input_mismatch_remains_unknown(self):
  e=detect(self.task(),self.observation({'status':'success','value':1,'trace':{'problem_sha256':'different'}}))[0];self.assertFalse(e['local_failure']);self.assertEqual(e['validation_status'],'UNKNOWN')
 def test_resource_termination_not_math(self):
  e=detect(self.task(),self.observation({'status':'error','error':'worker_exit_-14'}))[0];self.assertFalse(e['local_failure']);self.assertEqual(e['error_type'],'UNKNOWN')
 def test_bad_interface_distinct_from_algorithm(self):
  e=detect(self.task(),self.observation({'status':'error','error':'Define solve(problem)'}))[0];self.assertTrue(e['local_failure']);self.assertEqual(e['failure_family'],'Tool Integration')
