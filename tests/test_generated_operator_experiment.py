import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import generated_operator_experiment as e
from scripts.offline_loop_protocol import atomic_json,read_json,file_hash
from scripts.report_generated_operator import paired,metrics,failure_label


class FakeGenerator:
    def __init__(self):self.calls=[]
    async def call(self,key,messages,*args,**kwargs):
        self.calls.append(dict(key=key,messages=list(messages)))
        code='def solve(problem): return '+('0' if len(self.calls)==1 else '4')
        return dict(response=json.dumps(dict(code=code,rationale='Synthetic unit fixture')))
    def usage(self):return dict(fixture=True,requests=len(self.calls))


class ExperimentTests(unittest.TestCase):
    def test_pairing_exact_test_and_bootstrap(self):
        r=paired([1]*6,[0]*6)
        self.assertEqual(6,r['operator_wins']);self.assertEqual(.03125,r['p_value'])
        self.assertEqual([1.,1.],r['ci95']);self.assertEqual(10000,r['bootstrap_replicates'])
        self.assertEqual(1,paired([1,0],[1,0])['p_value'])

    def test_prepare_never_parses_formal_or_old_failures(self):
        with tempfile.TemporaryDirectory() as d:
            original=e.read_json;seen=[]
            def traced(path):
                seen.append(str(path));self.assertNotIn('sealed',str(path));self.assertNotIn('observations',str(path));return original(path)
            with patch.object(e,'read_json',side_effect=traced):e.prepare(Path(d))
            m=read_json(Path(d)/'manifest.json')
            self.assertEqual(240,m['formal']['total']);self.assertEqual(3,m['generation']['candidates'])
            self.assertEqual(30,len(read_json(Path(d)/'inputs/training.json')))
            p=e.generation_prompt(Path(d))
            self.assertNotIn('5f67880591f2dee7ff1b',p);self.assertNotIn('truncat',p)

    def test_automatic_repair_first_passing_candidate_no_manual_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);row=dict(id='fixture',problem=json.dumps(dict(n=1,alphabet=[0,1,2,3],constraints=[])),expected=4)
            for pool in ('training','development','boundary'):atomic_json(root/'inputs'/f'{pool}.json',[row])
            t=FakeGenerator();self.assertTrue(asyncio.run(e.generate(root,t)))
            self.assertEqual(2,len(t.calls));f=read_json(root/'operator/freeze.json')
            self.assertEqual(1,f['candidate']);self.assertEqual(1,f['repair'])
            self.assertEqual(file_hash(root/'operator/solve.py'),f['sha256'])
            self.assertFalse((root/'formal_start.json').exists())
            self.assertIn('expected',t.calls[1]['messages'][-1]['content'])

    def test_failed_admission_does_not_open_formal(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with self.assertRaises(FileNotFoundError):asyncio.run(e.formal(root,None))
            self.assertFalse((root/'formal_start.json').exists())

    def test_missing_resources_not_asserted_free_and_unknown_failure_not_invented(self):
        row=dict(input_tokens=10,output_tokens=20,correct=False,submitted=False,unknown_usage=1,calls=1,tools=None,tool_seconds=None,seconds=1,error='no_final_submission',events=[])
        r=metrics([row]);self.assertIsNone(r['avg_tool_runtime_seconds']);self.assertEqual(1,r['unknown_tool_time_rows'])
        self.assertEqual('unknown',failure_label(row)['label'])


if __name__=='__main__':unittest.main()
