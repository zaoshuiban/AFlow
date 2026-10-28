import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from scripts.compositional_counting import oracle_selfcheck,exact_dp,brute_force,KINDS
from scripts.counting_benchmark import pools,HELD_OUT
from scripts.experiment_a_calibration import solve,execute
from scripts.offline_loop_protocol import atomic_json,digest


class FakeTransport:
    def __init__(self,root,actions,output_usage=10):self.root=root;self.actions=iter(actions);self.calls=[];self.output_usage=iter(output_usage) if isinstance(output_usage,list) else output_usage
    async def call(self,key,messages,output_cap,timeout=175,tools=None):
        self.calls.append(dict(tools=tools,output_cap=output_cap,messages=list(messages)))
        name,args=next(self.actions)
        r=dict(key=key,usage=dict(input_tokens=20,output_tokens=next(self.output_usage) if not isinstance(self.output_usage,int) else self.output_usage),elapsed_seconds=.001,
            message=dict(tool_calls=[dict(id='test'+str(len(self.calls)),type='function',function=dict(name=name,arguments=json.dumps(args)))]))
        atomic_json(self.root/'requests'/(digest(key)+'.json'),r);return r


class CountingTests(unittest.TestCase):
    def test_independent_oracles_all_kinds_overlap_and_empty_solution(self):
        checks=oracle_selfcheck()
        self.assertEqual(84,len(checks));self.assertTrue(all(c['passed'] for c in checks))
        self.assertEqual(1,checks[-4]['dp']);self.assertEqual(0,checks[-3]['dp']);self.assertEqual(0,checks[-2]['dp'])

    def test_parity_is_one_based_and_leading_zero_allowed(self):
        p=dict(n=2,alphabet=[0,1,2,3],constraints=[dict(kind='position',parity='even',forbidden_symbol=3)])
        self.assertEqual(12,exact_dp(p)['answer']);self.assertEqual(12,brute_force(p))
        self.assertEqual(4**40,exact_dp(dict(n=40,alphabet=[0,1,2,3],constraints=[]))['answer'])

    def test_ood_combinations_never_in_development_pool(self):
        held={frozenset(s) for s in HELD_OUT}
        for tier,ms in pools().items():
            self.assertEqual(10,len(ms));self.assertFalse(held&{frozenset(s) for s in ms})
            self.assertEqual({'Easy':{1,2},'Medium':{3,4},'Hard':{5,6,7}}[tier],{len(s) for s in ms})

    def test_late_reflection_draft_has_reserved_actual_final_call(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);t=FakeTransport(root,[('python',dict(code='def solve(problem): return None'))]*2+[('submit',dict(value=7)),('submit',dict(value=8))])
            r=asyncio.run(solve(root,t,dict(id='x',expected=8,problem='{}',tier=2),'reflection','test specification'))
            self.assertTrue(r['submitted']);self.assertTrue(r['correct']);self.assertEqual(7,r['draft'])
            self.assertEqual('2',r['tier'])
            self.assertEqual(['submit'],[x['function']['name'] for x in t.calls[-1]['tools']])
            self.assertEqual(7680,t.calls[0]['output_cap']);self.assertEqual(4,r['calls'])

    def test_tool_success_starts_review_without_extra_draft_submission(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);t=FakeTransport(root,[('python',dict(code='def solve(problem): return 7')),('submit',dict(value=7))])
            r=asyncio.run(solve(root,t,dict(id='x',expected=7,problem='{}'),'reflection','test'))
            self.assertTrue(r['correct']);self.assertTrue(r['review_performed']);self.assertEqual(2,r['calls'])

    def test_correct_draft_not_auto_scored_and_explicit_abstention_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);t=FakeTransport(root,[('submit',dict(value=7)),('submit',dict(value=None))])
            r=asyncio.run(solve(root,t,dict(id='x',expected=7,problem='{}'),'reflection','test'))
            self.assertTrue(r['submitted']);self.assertFalse(r['correct']);self.assertEqual('explicit_abstention',r['error'])

    def test_worker_large_integer_and_io_blocked(self):
        r=asyncio.run(execute('def solve(problem): return 2**200','{}',2))
        self.assertEqual(2**200,r['value'])
        r=asyncio.run(execute('import os\ndef solve(problem): return 1','{}',2))
        self.assertEqual('error',r['status'])

    def test_early_output_exhaustion_forces_real_submission(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);t=FakeTransport(root,[('python',dict(code='def solve(problem): return 7')),('submit',dict(value=7))],output_usage=[7680,10])
            r=asyncio.run(solve(root,t,dict(id='x',expected=7,problem='{}'),'reflection','test'))
            self.assertTrue(r['correct']);self.assertEqual(['submit'],[x['function']['name'] for x in t.calls[1]['tools']]);self.assertEqual(512,t.calls[1]['output_cap'])

    def test_standard_debug_and_queue_methods_are_available(self):
        code='from collections import deque\ndef solve(problem):\n q=deque([1,2]); print("debug"); return q.popleft()+next(iter([3]))'
        r=asyncio.run(execute(code,'{}',2));self.assertEqual('success',r['status']);self.assertEqual(4,r['value'])


if __name__=='__main__':unittest.main()
