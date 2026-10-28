import unittest

from scripts.mechanism_reliability import evaluate, matches, validate_scope
from scripts.offline_loop_protocol import digest
from scripts.operator_library import stable_problem_id


class ReliabilityTests(unittest.TestCase):
    def payload(self):
        scope = {'mechanism_id': 'm', 'required_terms': ['circle'], 'forbidden_terms': []}
        pid = stable_problem_id('circle A')
        return {'scope': scope, 'frozen_scope_sha256': digest(scope), 'discovery_ids': [],
                'rows': [{'problem_id': pid, 'problem': 'circle A', 'solution': '1'}],
                'observations': [{'problem_id': pid, 'run': i, 'score': 1, 'prediction': '1', 'error': None} for i in (1,2,3)],
                'packet': {'schema_version': 2, 'cases': []},
                'response': {'analyses': [], 'groups': []}}

    def test_no_failure_not_supported(self):
        result = evaluate(self.payload())
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertEqual(result['observed_error_rate'], 0)
        self.assertFalse(result['promotion_authorized'])

    def test_overlap_rejected(self):
        p = self.payload()
        p['discovery_ids'] = [p['rows'][0]['problem_id']]
        with self.assertRaises(ValueError): evaluate(p)

    def test_changed_scope_rejected(self):
        p = self.payload()
        p['scope']['required_terms'] = ['A']
        with self.assertRaises(ValueError): evaluate(p)

    def test_duplicate_observation_rejected(self):
        p = self.payload()
        p['observations'].append(p['observations'][0])
        with self.assertRaises(ValueError): evaluate(p)

    def test_errors_not_wrong(self):
        p = self.payload()
        p['observations'][0].update(error='timeout', score=0)
        r = evaluate(p)
        self.assertEqual(r['valid_observations'], 2)
        self.assertEqual(r['wrong_observations'], 0)
        self.assertEqual(r['execution_errors'], 1)

    def test_case_sensitive_scope(self):
        s = {'mechanism_id': 'm', 'required_terms': ['A'], 'forbidden_terms': []}
        self.assertFalse(matches(s, 'a'))

    def test_contradictory_scope(self):
        with self.assertRaises(ValueError):
            validate_scope({'mechanism_id': 'm', 'required_terms': ['x'], 'forbidden_terms': ['x']})


if __name__ == '__main__': unittest.main()
