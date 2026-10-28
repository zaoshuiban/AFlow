import unittest
from scripts.experiment_e3_s0 import classify, replay, timestamp


class HistoricalReplayTests(unittest.TestCase):
    def test_mismatch_and_ambiguous_probes_do_not_supply_failure_evidence(self):
        self.assertEqual(classify({'status':'success','value':3},4,['HASH'],None),('UNTRUSTED',False))
        self.assertEqual(classify({'status':'error','error':'AssertionError: probe'},4,[],None),('ASSERTION_PROBE_UNKNOWN',False))
        self.assertEqual(classify({'status':'success','value':3},4,[],'exploration'),('EXPLORATION',False))

    def test_prefix_and_timestamp_ties(self):
        def event(i,t):
            return dict(event_id=str(i),task_id=str(i),timestamp=t,
                        candidate_key='runtime',observed_failure=True)
        es=[event(1,'2026-01-01T00:00:00+00:00'),event(2,'2026-01-01T00:00:00+00:00'),event(3,'2026-01-02T00:00:00+00:00'),event(4,None)]
        timed,curve,candidates=replay(es)
        self.assertEqual([e['prior_distinct_failure_tasks'] for e in timed],[0,0,2])
        self.assertEqual([r['cumulative_failure_events'] for r in curve],[2,3])
        self.assertEqual(candidates[0]['support_task_ids'],['1','2','3'])
        self.assertFalse(candidates[0]['eligible_for_generation'])

    def test_missing_or_timezone_ambiguous_timestamp_is_not_invented(self):
        for x in [None,'invalid','2026-01-01T00:00:00']:
            self.assertIsNone(timestamp(x))


if __name__=='__main__':unittest.main()
