import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from scripts.experiment_a_pilot import (reference, second_reference, make_rows, trial,
    checked_execution, system_prompt, parse_json, ARMS)
from scripts.offline_loop_protocol import atomic_json, digest


class FakeTransport:
    def __init__(self, root, actions):
        self.root=root;self.actions=iter(actions);self.messages=[]

    async def call(self, key, messages, output_cap, timeout=175, tools=None):
        self.messages.append(list(messages))
        value=next(self.actions)
        r=dict(key=key,response=json.dumps(value),usage=dict(input_tokens=20,output_tokens=10),elapsed_seconds=.01)
        action=value['action'];args={k:v for k,v in value.items() if k!='action'}
        r['message']=dict(tool_calls=[dict(id='test_call',type='function',function=dict(name='submit' if action=='answer' else action,arguments=json.dumps(args)))])
        atomic_json(self.root/'requests'/(digest(key)+'.json'),r)
        return r


class PilotTests(unittest.TestCase):
    def test_oracles_and_family_isolation(self):
        seen=set()
        a=make_rows(3,12,'validation',seen)
        b=make_rows(4,12,'validation',seen)
        self.assertFalse({r['family_id'] for r in a}&{r['family_id'] for r in b})
        for row in a+b:
            self.assertEqual(row['expected'],second_reference(json.loads(row['problem'])))

    def test_worker_real_execution_and_denied_import(self):
        r=asyncio.run(checked_execution('def solve(problem):\n return 13','{}'))
        self.assertEqual(13,r['value'])
        r=asyncio.run(checked_execution('import os\ndef solve(problem):\n return 13','{}'))
        self.assertEqual('error',r['status'])

    def test_reflection_must_revise_without_oracle(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); t=FakeTransport(root,[dict(action='answer',value=2),dict(action='answer',value=3)])
            row=dict(id='test',tier=0,problem='{}',expected=3)
            r=asyncio.run(trial(root,t,row,'reflection',''))
            self.assertTrue(r['correct']);self.assertEqual(2,r['calls'])
            self.assertNotIn('expected',json.dumps(t.messages))
            self.assertIn('draft',t.messages[1][-1]['content'])

    def test_all_arms_get_python_operator_only_operator(self):
        for arm in ARMS:
            self.assertIn('python executes',system_prompt(arm))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);t=FakeTransport(root,[dict(action='operator')])
            r=asyncio.run(trial(root,t,dict(id='a',tier=0,problem='{}',expected=3),'baseline','def solve(problem): return 3'))
            self.assertFalse(r['correct']);self.assertEqual(0,r['tools'])

    def test_operator_trace_and_answer(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);t=FakeTransport(root,[dict(action='operator'),dict(action='answer',value=3)])
            r=asyncio.run(trial(root,t,dict(id='a',tier=0,problem='{}',expected=3),'operator','def solve(problem): return 3'))
            self.assertTrue(r['correct']);self.assertEqual(1,r['tools'])
            self.assertIn('code_sha256',r['events'][0]['tool_result']['tool_trace'])

    def test_call_budget_and_no_draft_scoring(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);t=FakeTransport(root,[dict(action='answer',value=3)]+[dict(action='python',code='def solve(problem): return 3')]*3)
            r=asyncio.run(trial(root,t,dict(id='a',tier=0,problem='{}',expected=3),'reflection',''))
            self.assertFalse(r['correct']);self.assertEqual(4,r['calls'])
            self.assertIsNone(r['value'])


if __name__=='__main__':unittest.main()
