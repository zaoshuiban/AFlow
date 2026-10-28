import copy
import unittest
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.failure_mechanism_induction import build_packet, validate_response, fragment_answer
from scripts.failure_mechanism_induction import prepare, run
from scripts.offline_loop_protocol import atomic_json, read_json
from scripts.offline_loop_runtime import RecordedRequestFailed


class InductionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'problem_id': p, 'problem': q, 'solution': 'correct'}
                     for p,q in [('a','a geometry wording'),('b','unrelated subject wording')]]
        self.obs = [{'problem_id': p,'run': n,'score': 0,'error': '', 'prediction': 'I replace exact data with a guess'}
                    for p in ('a','b') for n in (1,2)]
        self.packet = build_packet(self.rows,self.obs)
        self.packet['schema_version'] = 1  # Legacy quote protocol remains readable.
        self.response = {'groups':[{'mechanism_id':'exactness','explanation':'Unjustified data replacement'}],
            'analyses':[{'problem_id': o['problem_id'],'run': o['run'],'verdict':'mechanism_hypothesis',
                'mechanism_id':'exactness','violated_constraint':'Use provided exact values',
                'error_quote':'replace exact data with a guess','explanation':'Changes the input',
                'applicability':'Exact quantities given','counterexample':'Explicit approximation requested'} for o in self.obs]}

    def test_cross_wording_group_counts_problems_not_repeats(self):
        r=validate_response(self.packet,self.response,3)
        self.assertEqual(r['groups'][0]['distinct_problem_count'],2)
        self.assertFalse(r['groups'][0]['minimum_support_met'])
        self.assertFalse(r['groups'][0]['eligible_for_generation'])
        self.assertIsNone(r['population_error_rate'])

    def numbered(self):
        packet=build_packet(self.rows,self.obs)
        response=copy.deepcopy(self.response)
        for a in response['analyses']:
            a.pop('error_quote')
            a['evidence_ids']=[f'{a["problem_id"]}/r{a["run"]}/f0001']
        return packet,response

    def test_numbered_evidence_is_retrieved_from_source(self):
        packet,response=self.numbered()
        response['analyses'][0]['error_quote']='model invented quote must not be trusted'
        result=validate_response(packet,response)
        self.assertEqual(len(result['accepted_analyses']),4)
        a=result['accepted_analyses'][0]
        self.assertEqual(a['error_quote'],self.obs[0]['prediction'])
        self.assertEqual(a['semantic_review_status'],'not_covered')
        self.assertTrue(a['constraint_review']['requires_semantic_review'])

    def test_cross_observation_and_unknown_fragment_ids_rejected(self):
        for ids in (['a/r2/f0001'], ['b/r1/f0001'], ['a/r1/f9999'],
                    ['a/r1/f0001','a/r1/f0001'], [], 'a/r1/f0001'):
            packet,response=self.numbered()
            response['analyses'][0]['evidence_ids']=ids
            self.assertEqual(len(validate_response(packet,response)['excluded_analyses']),1)

    def test_modified_fragment_content_rejected(self):
        packet,response=self.numbered()
        packet['cases'][0]['failed_runs'][0]['fragments'][0]['text']='altered'
        with self.assertRaisesRegex(ValueError,'Fragment'):
            validate_response(packet,response)

    def test_fragment_offsets_cover_latex_whitespace_and_long_lines(self):
        text='  \\frac{1}{2}\n\nSame sentence.\nSame sentence.\n'+('x'*1700)+'\n最后一行'
        fragments=fragment_answer('p',2,text)
        self.assertEqual(''.join(f['text'] for f in fragments),text)
        self.assertEqual(fragments,fragment_answer('p',2,text))
        for f in fragments:
            self.assertEqual(text[f['start']:f['end']],f['text'])
            self.assertLessEqual(len(f['text']),800)

    def test_hallucinated_quotes_excluded(self):
        self.response['analyses'][0]['error_quote']='invented claim'
        r=validate_response(self.packet,self.response)
        self.assertEqual(len(r['excluded_analyses']),1)

    def test_quotes_must_belong_to_cited_run(self):
        self.packet['cases'][0]['failed_runs'][0]['prediction']='other response'
        r=validate_response(self.packet,self.response)
        self.assertEqual(len(r['excluded_analyses']),1)

    def test_missing_analysis_rejected(self):
        self.response['analyses'].pop()
        with self.assertRaisesRegex(ValueError,'Every failed'):
            validate_response(self.packet,self.response)

    def test_duplicate_and_unknown_references_rejected(self):
        for pid in ('a','hidden'):
            r=copy.deepcopy(self.response)
            r['analyses'][2]['problem_id']=pid
            with self.assertRaisesRegex(ValueError,'Unknown or duplicate'):
                validate_response(self.packet,r)

    def test_unknown_is_not_evidence_for_group(self):
        for a in self.response['analyses']: a['verdict']='unknown'
        r=validate_response(self.packet,self.response)
        self.assertEqual(r['groups'][0]['distinct_problem_count'],0)

    def test_infrastructure_errors_excluded(self):
        self.obs[0]['error']='APIConnectionError'
        self.assertEqual(len(build_packet(self.rows,self.obs)['cases']),1)

    def test_missing_boundary_excluded(self):
        self.response['analyses'][0]['counterexample']=''
        self.assertEqual(len(validate_response(self.packet,self.response)['excluded_analyses']),1)

    def test_duplicate_observations_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            build_packet(self.rows,self.obs+[self.obs[0]])

    def test_invalid_budget_fails_before_touching_source(self):
        with self.assertRaisesRegex(ValueError,'positive integer'):
            prepare(Path('missing'),Path('missing'),Path('missing'),0)

    def test_truncation_exports_partial_response_and_usage_without_retry(self):
        class FailedLLM:
            calls=0
            def __init__(self,ledger,config,key): self.ledger=ledger
            async def __call__(self,prompt):
                self.calls += 1
                atomic_json(self.ledger.path/'failure.json', {'status':'failed','error':'output_token_limit',
                    'response':'{"analyses": [','usage':{'input_tokens':12,'output_tokens':4096,'total_tokens':4108}})
                raise RecordedRequestFailed('output_token_limit')
            async def close(self): pass
        config={'opt_model':'fake','exec_model':'fake','max_output_tokens':4096}
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)
            atomic_json(output/'packet.json', self.packet)
            (output/'prompt.txt').write_text('test')
            with patch('scripts.failure_mechanism_induction.verify',return_value={'config':config}), \
                 patch('scripts.offline_loop_runtime.RecordedLLM',FailedLLM), \
                 patch('scripts.async_llm.LLMsConfig.default',return_value=SimpleNamespace(get=lambda name: None)):
                result=asyncio.run(run(output))
            self.assertEqual(result['status'],'request_failed')
            self.assertEqual(result['usage']['requests_reserved'],1)
            self.assertEqual((output/'raw_response.txt').read_text(),'{"analyses": [')
            self.assertFalse((output/'result.json').exists())
            self.assertEqual(read_json(output/'failure_summary.json')['error'],'output_token_limit')


if __name__ == '__main__':
    unittest.main()
