"""Local oracles and synthetic transport integration; no paid research data."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from scripts.constructed_prime_dataset import (make_row,history_groups,prepare_calibration,DatasetStore,
    run_calibration,calibration_decision,freeze_formal,classify,LEVELS)
from scripts.offline_loop_protocol import LoopConfig,atomic_json,digest,read_json


class FakeBackend:
    def __init__(self,store,mode='mixed'):
        self.store=store;self.mode=mode;self.calls=0
        self.wrong_medium=next(r['problem_id'] for r in store.rows() if r['difficulty']=='medium')
    async def execute(self,*,item,key,**kwargs):
        self.calls+=1
        correct = item['difficulty']=='small' or (item['difficulty']=='medium' and item['problem_id']!=self.wrong_medium)
        if self.mode=='correct':correct=True
        if self.mode=='wrong':correct=False
        response='\\boxed{'+str(item['answer'] if correct else item['answer']+1)+'}'
        atomic_json(self.store.root/'requests'/(digest(key)+'.json'),
                    {'key':key+'/call_0','status':'completed','response':response,'error':'','fixture':True})
        return {'prediction':response,'score':int(correct),'error':'','calls':1}
    def usage(self):return {'synthetic_fixture':True,'model_calls':0,'executions':self.calls}


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=LoopConfig(opt_model='fake',exec_model='fake',discovery_runs=2,max_requests=24,candidate_count=2,max_output_tokens=8192,thinking_mode='disabled')
        patcher=patch('scripts.constructed_prime_dataset.model_settings',return_value={'opt':{},'exec':{}})
        patcher.start();self.addCleanup(patcher.stop)
    def prepare(self):
        prepare_calibration(self.root/'calibration',self.config,[])
        return DatasetStore(self.root/'calibration')
    def calibrated(self,mode='mixed'):
        s=self.prepare();b=FakeBackend(s,mode);asyncio.run(run_calibration(s,b));return s,b
    def test_known_counts_zero_one_repetition_and_boundaries(self):
        self.assertEqual(1,make_row([0,1,2],2)['answer'])
        self.assertEqual(2,make_row([0,1,2],2,'repeat_prime')['answer'])
        self.assertEqual(4,make_row([0,1,2],2,'even')['answer'])
        self.assertEqual(4,make_row([0,1,2],2,'composite')['answer'])
        self.assertEqual(94,make_row([1,2,3,4,7,8],4)['answer'])
    def test_invalid_parameters(self):
        for ds,k,kind in [([1,1],2,'prime'),([0,10],2,'prime'),([0,1],0,'prime'),([0,1],2,'unknown')]:
            with self.assertRaises(ValueError):make_row(ds,k,kind)
    def test_two_oracles_must_agree(self):
        with patch('scripts.constructed_prime_dataset.oracle_integer_scan',return_value={999}):
            with self.assertRaisesRegex(ValueError,'disagree'):make_row([1,2,3],2)
    def test_prepare_freezes_six_unique_groups_and_no_model_calls(self):
        s=self.prepare()
        self.assertEqual(6,len(s.rows()))
        self.assertEqual(6,len({r['family_id'] for r in s.rows()}))
        self.assertFalse((s.root/'requests').exists())
        with self.assertRaises(FileExistsError):prepare_calibration(s.root,self.config,[])
    def test_history_excludes_entire_digit_group(self):
        p=self.root/'history.jsonl';r=make_row([1,2,3,4],2)
        p.write_text(json.dumps(r)+'\n')
        groups,_=history_groups([p]);self.assertIn(r['family_id'],groups)
        self.assertEqual(r['family_id'],make_row([1,2,3,4],3)['family_id'])
    def test_difficulty_selected_before_disjoint_formal_freeze(self):
        s,b=self.calibrated()
        self.assertEqual('medium',calibration_decision(s)['selected_level'])
        formal=self.root/'formal'
        freeze_formal(formal,s.root,replace(self.config,discovery_runs=3,max_requests=500),[])
        f=DatasetStore(formal)
        self.assertEqual({'discovery':6,'design':4,'confirmation':4,'boundary_design':3,'boundary_confirmation':3},f.manifest['counts'])
        families={r['family_id'] for r in s.rows()}
        for role in f.manifest['counts']:
            for row in f.rows(role):
                self.assertNotIn(row['family_id'],families);families.add(row['family_id'])
        self.assertIn('inputs/calibration_evidence.json',f.manifest['input_hashes'])
    def test_rejects_uncalibrated_or_all_correct_formal_freeze(self):
        s=self.prepare()
        with self.assertRaisesRegex(ValueError,'must finish'):freeze_formal(self.root/'formal',s.root,replace(self.config,max_requests=500),[])
        asyncio.run(run_calibration(s,FakeBackend(s,'correct')))
        with self.assertRaisesRegex(ValueError,'No suitable'):freeze_formal(self.root/'formal',s.root,replace(self.config,max_requests=500),[])
        self.assertFalse((self.root/'formal').exists())
    def test_all_wrong_is_not_automatically_selected(self):
        s,_=self.calibrated('wrong');self.assertIsNone(calibration_decision(s)['selected_level'])
    def test_resource_and_budget_changes_rejected(self):
        s,_=self.calibrated()
        with self.assertRaisesRegex(ValueError,'resources differ'):freeze_formal(self.root/'f',s.root,replace(self.config,timeout=999,max_requests=500),[])
        with self.assertRaisesRegex(ValueError,'at least'):freeze_formal(self.root/'f',s.root,self.config,[])
    def test_corruption_and_idempotent_resume(self):
        s,b=self.calibrated();asyncio.run(run_calibration(s,b));self.assertEqual(12,b.calls)
        p=next((s.root/'requests').glob('*.json'));atomic_json(p,{'changed':True})
        with self.assertRaisesRegex(ValueError,'request evidence changed'):calibration_decision(s)
    def test_frozen_row_tampering_rejected(self):
        s=self.prepare();atomic_json(s.root/'inputs/calibration.json',[])
        with self.assertRaisesRegex(ValueError,'Frozen input'):DatasetStore(s.root)
    def test_failure_categories(self):
        self.assertEqual('output_token_limit',classify({'error':'RecordedRequestFailed'},[{'error':'output_token_limit'}]))
        self.assertEqual('timeout',classify({'error':'TimeoutError'},[]))
        self.assertEqual('infrastructure_error',classify({'error':'RecordedRequestFailed'},[{'error':'APIConnectionError'}]))
        self.assertEqual('completed_wrong',classify({'error':'','score':0},[]))
