"""Synthetic transport tests plus real local worker execution; no paid calls."""
import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_independent_mechanism_evidence as fixtures
from scripts.independent_mechanism_evidence import collect
from scripts.offline_loop_protocol import atomic_json,read_json,digest
from scripts.operational_operator_replay import (prepare,OperationalStore,OperationalLoop,failure_class,wrapper)
from scripts.frozen_math_execution import run_frozen_math
from scripts.frozen_math_worker import validate
from scripts.offline_loop_runtime import LiveBackend


class HistoryBackend:
    def __init__(self,store,network=False):self.store=store;self.network=network
    async def execute(self,*,item,key,**kwargs):
        target=item['evidence_role']=='target'
        atomic_json(self.store.root/'requests'/(digest(key)+'.json'),{'key':key+'/call_0','status':'failed' if target else 'completed',
            'error':('APIConnectionError' if self.network else 'output_token_limit') if target else '',
            'response':'synthetic fixture partial response','fixture':True})
        return {'prediction':'' if target else item['solution'],'score':int(not target),'error':'RecordedRequestFailed' if target else '', 'calls':1}
    def usage(self):return {'model_calls':0,'fixture':True}


class EvaluationBackend:
    def __init__(self,store,mode='pass'):self.store=store;self.mode=mode;self.generated=[];self.executions=[]
    async def generate_executable(self,context,index):
        self.generated.append(context)
        return {'code':'def solve(problem):\n return '+str(index+1),'rationale':'Synthetic fixture only'}
    async def execute(self,*,item,operator,regression,key):
        self.executions.append(key)
        target='How many' in item['problem']
        basefailure=operator is None and target
        correct=not target or operator=='OperationalGeneratedCandidate1'
        if self.mode=='gate_fail' and key.startswith('gate/') and operator:correct=not target
        error='RecordedRequestFailed' if basefailure else ''
        if basefailure:
            atomic_json(self.store.root/'requests'/(digest(key)+'.json'),{'key':key+'/call_0','status':'failed',
                'error':'APIConnectionError' if self.mode=='network' else 'output_token_limit','response':'fixture'})
        return {'score':int(correct),'prediction':'\\boxed{1}' if correct else '', 'error':error,'candidate_error':'',
                'declined':bool(operator and not target),'selected_operator':operator if target and operator else None,
                'used_fallback':not target or not operator,'tokens':1,'calls':1,'elapsed_seconds':.001}
    def usage(self):return {'fixture':True,'model_calls':0}


class OperationalTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EvidenceTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.protocol['scope']['mechanism_id']='counting_problem'
        self.source=self.f.prepare(evidence_mode='historical_replay')
        asyncio.run(collect(self.source,HistoryBackend(self.source)))
        self.paths={}
        for role,start in [('design',700),('gate',710),('regression',720)]:
            rows=[]
            for n in range(start,start+3):
                if role!='regression':rows.append({'problem':f'How many ways to arrange {n} operational objects?','solution':'\\boxed{1}'})
                if role!='gate':rows.append({'problem':f'Find the area of triangle operational {n}.','solution':'\\boxed{1}'})
            p=self.f.path/(role+'.jsonl');p.write_text(''.join(json.dumps(r)+'\n' for r in rows));self.paths[role]=p
        self.root=self.f.path/'operational'
        patcher=patch('scripts.operational_operator_replay.model_settings',return_value=self.f.settings);patcher.start();self.addCleanup(patcher.stop)
    def prepare(self):
        prepare(root=self.root,evidence_run=self.source.root,design_data=self.paths['design'],gate_data=self.paths['gate'],regression_data=self.paths['regression'])
        return OperationalStore(self.root)
    def test_resource_failure_to_generation_confirmation_release(self):
        s=self.prepare();b=EvaluationBackend(s);r=asyncio.run(OperationalLoop(s,b).run())
        self.assertTrue(r['promoted'],r)
        self.assertEqual(2,len(b.generated));self.assertNotIn('gate',json.dumps(b.generated))
        self.assertIn('Historical',r['scientific_scope'])
        before=len(b.executions);again=asyncio.run(OperationalLoop(OperationalStore(self.root),b).run())
        self.assertEqual(r,again);self.assertEqual(before,len(b.executions))
    def test_network_failure_is_not_a_win(self):
        s=self.prepare();r=asyncio.run(OperationalLoop(s,EvaluationBackend(s,'network')).run())
        self.assertEqual('archived_design_failed',r['status']);self.assertFalse((self.root/'release').exists())
        self.assertEqual(0,r['design_ranking'][0]['metrics']['wins'])
    def test_single_hidden_failure_archives_no_runner_up(self):
        s=self.prepare();b=EvaluationBackend(s,'gate_fail');r=asyncio.run(OperationalLoop(s,b).run())
        self.assertEqual('archived_hidden_failed',r['status'])
        self.assertFalse(any('gate/OperationalGeneratedCandidate2/' in k for k in b.executions))
    def test_historical_source_tampering_rejected(self):
        atomic_json(self.source.root/'observations.json',[])
        with self.assertRaisesRegex(ValueError,'observations changed'):self.prepare()
    def test_code_and_raw_request_tampering_rejected(self):
        s=self.prepare();b=EvaluationBackend(s);loop=OperationalLoop(s,b)
        screen,obs=asyncio.run(loop.discover());asyncio.run(loop.generate(screen,obs))
        p=s.root/'work/operator_library/operators.json';data=read_json(p);data['operators']['OperationalGeneratedCandidate1']['artifact']['class_code']+='\n#changed';atomic_json(p,data)
        with self.assertRaisesRegex(ValueError,'candidate changed'):OperationalStore(self.root)
    def test_worker_executes_same_generated_source_and_refuses(self):
        code='def solve(problem):\n return len(problem) if problem else None'
        a=asyncio.run(run_frozen_math(code,'abc'));b=asyncio.run(run_frozen_math(code,''))
        self.assertEqual('\\boxed{3}',a['response']);self.assertEqual('declined',b['status'])
        self.assertEqual(a['tool_trace']['code_sha256'],b['tool_trace']['code_sha256'])
    def test_worker_limits_and_prohibited_access(self):
        for code in ['import os\ndef solve(problem): return 1','def solve(problem): return open(problem)','import re\ndef solve(problem): return re.sys.modules']:
            with self.assertRaises(ValueError):validate(code)
        r=asyncio.run(run_frozen_math('def solve(problem):\n while True: pass','x',timeout=.2))
        self.assertEqual('candidate_worker_timeout',r['observation'])
    def test_live_backend_executes_generated_wrapper_and_records_trace(self):
        s=self.prepare();loop=OperationalLoop(s,EvaluationBackend(s));screen,obs=asyncio.run(loop.discover());asyncio.run(loop.generate(screen,obs))
        # Avoid API model lookup; instantiate the real execution/factory path with
        # a fake LLM that would fail if the local candidate tried to call it.
        from scripts.offline_loop_runtime import load_template
        backend=LiveBackend.__new__(LiveBackend);backend.store=s;backend.library=loop.library
        backend.module=load_template(s.manifest['module_name'],s.root/'base_template')
        class NoLLM:
            async def __call__(self,*a,**k):raise AssertionError('candidate made model call')
            async def close(self):pass
            def get_usage_summary(self):return {'total_tokens':0,'call_count':0,'unknown_usage_calls':0,'request_seconds':0}
        backend.llm=lambda *a,**k:NoLLM()
        backend.ledger=type('Ledger',(),{'exhausted':False})()
        row=next(r for r in s.rows('design') if 'How many' in r['problem'])
        r=asyncio.run(backend.execute(item=row,operator='OperationalGeneratedCandidate1',regression=False,key='localtest'))
        self.assertEqual(1,r['score']);self.assertTrue(r['tool_trace']['code_sha256']);self.assertEqual(0,r['calls'])
    def test_no_stable_resource_failure_archives_without_generation(self):
        # A fresh source containing only network failures must not authorize generation.
        source=self.f.prepare(root=self.f.path/'network_source',evidence_mode='historical_replay')
        asyncio.run(collect(source,HistoryBackend(source,network=True)))
        prepare(root=self.root,evidence_run=source.root,design_data=self.paths['design'],gate_data=self.paths['gate'],regression_data=self.paths['regression'])
        s=OperationalStore(self.root);b=EvaluationBackend(s);r=asyncio.run(OperationalLoop(s,b).run())
        self.assertEqual('archived_no_stable_mechanism',r['status']);self.assertEqual([],b.generated)
    def test_invalid_generated_code_archives_without_evaluation(self):
        s=self.prepare()
        class Invalid(EvaluationBackend):
            async def generate_executable(self,context,index):
                return {'code':'import os\ndef solve(problem):return 1','rationale':'fixture'}
        b=Invalid(s);r=asyncio.run(OperationalLoop(s,b).run())
        self.assertEqual('archived_generation_failed',r['status']);self.assertEqual([],b.executions)
    def test_candidate_worker_error_is_not_network_error(self):
        s=self.prepare()
        class Broken(EvaluationBackend):
            async def execute(self,**kwargs):
                result=await super().execute(**kwargs)
                if kwargs['operator']:
                    result.update(score=0,error='candidate_worker_exit_1',candidate_error='candidate_worker_exit_1',used_fallback=False)
                return result
        loop=OperationalLoop(s,Broken(s));screen,obs=asyncio.run(loop.discover());asyncio.run(loop.generate(screen,obs))
        row=s.rows('design')[0]
        r=asyncio.run(loop.execute(item=row,stage='design',repeat=1,operator='OperationalGeneratedCandidate1'))
        self.assertEqual('candidate_execution_error',r['outcome_class'])
