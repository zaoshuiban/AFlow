"""Selectors are tested with question-only positive/negative boundary probes."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.mechanism_scope import FAMILIES, classify, matches, question_features, select_pools, validate_scope
from scripts.mechanism_review import validate_protocol
from scripts.independent_mechanism_evidence import EvidenceStore, prepare
from scripts.offline_loop_protocol import LoopConfig, read_json


def protocol():
    return {"scope": {"mechanism_id": "precision", "selector": {"kind": "decimal_reasoning_v1",
                "min_distinct_decimals": 2, "families": sorted(FAMILIES)}},
            "control_scope": {"mechanism_id": "precision", "selector": {"kind": "decimal_boundary_v1",
                "min_distinct_decimals": 2, "boundary_types": ["given_approximation", "final_rounding"]}},
            "sampling_policy": {"one_per_template_family": True,
                                "control_counts": {"given_approximation": 1, "final_rounding": 1}},
            "hypothesis": {k: "Synthetic fixture" for k in ("claim", "violated_constraint", "expected_intervention",
                "applicable_when", "not_applicable_when", "success_criteria")}, "program_verifier": "none"}


GAS = "At constant temperature pressure is inversely proportional to volume. A 2.28 liter container becomes 5.7 liters. Find pressure."
LADDER = "An 8.5-meter ladder reaches 7.5 meters up a wall. Find its base distance."
POLE = "A pole has a cable. Someone 1.5 meters tall walks 2.5 meters. Find the pole height."
APPROX = "Given 0.5 liters is approximately 1.05 pints, find the conversion to the nearest tenth."
ROUND = "The cost is 19.90 and shipping 6.95. Find average cost to the nearest whole number."


class ScopeTests(unittest.TestCase):
    def test_given_values_and_trace_offsets(self):
        result = classify(protocol()["scope"], GAS)
        self.assertTrue(result["matches"])
        self.assertEqual(result["template_family"], "gas_proportion")
        for token in result["decimal_tokens"]:
            self.assertEqual(GAS[token["start"]:token["end"]], token["text"])

    def test_diagram_layout_and_labels_do_not_supply_values_in_v1(self):
        body = "Pressure is inversely proportional to volume."
        for suffix in ('[asy]draw((2.28,5.7)--(0,0));[/asy]',
                       '[asy]label("2.28",(0,0)); label("5.7",(1,0));[/asy]',
                       r'\hspace{2.28mm}\vspace{5.7mm}',
                       '\nA. 2.28\nB. 5.7',
                       ' For example, 2.28 and 5.7.', ' Note: 2.28 and 5.7.'):
            with self.subTest(suffix=suffix):
                self.assertFalse(matches(protocol()["scope"], body + suffix))

    def test_duplicate_numeric_values_are_not_distinct(self):
        self.assertFalse(matches(protocol()["scope"], GAS.replace('5.7', '2.280')))

    def test_boundary_control_disjoint_and_typed(self):
        p = protocol()
        for text, kind in ((APPROX, "given_approximation"), (ROUND, "final_rounding")):
            self.assertFalse(matches(p["scope"], text))
            result = classify(p["control_scope"], text)
            self.assertTrue(result["matches"])
            self.assertEqual(result["template_family"], kind)
        self.assertFalse(matches(p["control_scope"], "How many ways to arrange 5 objects?"))
        self.assertFalse(matches(p["scope"], GAS + " Express approximately."))

    def test_unsupported_and_malformed_forms_fail_closed(self):
        self.assertFalse(matches(protocol()["scope"], GAS + "[asy]"))
        self.assertFalse(matches(protocol()["scope"], 'Compute 2.28 + 5.7.'))
        self.assertEqual(question_features('A volume question. You should enter 10.0000 instead of 0.1000.')["distinct_decimal_values"], 0)

    def test_validate_version_and_policy(self):
        p = protocol()
        validate_protocol(p)
        p["sampling_policy"]["control_counts"]["final_rounding"] = 0
        with self.assertRaises(ValueError): validate_protocol(p)
        s = protocol()["scope"]
        s["selector"]["kind"] = "arbitrary_regex"
        with self.assertRaises(ValueError): validate_scope(s)

    def test_no_duplicate_template_family_or_control_fallback(self):
        from scripts.operator_library import stable_problem_id
        texts = [GAS, GAS.replace('2.28', '3.28'), LADDER, POLE, APPROX, ROUND]
        rows = [{"problem": t, "problem_id": stable_problem_id(t)} for t in texts]
        targets, controls, evidence = select_pools(protocol(), rows, 3, 2)
        self.assertEqual(len(targets), 3)
        self.assertEqual(sum('pressure' in r["problem"] for r in targets), 1)
        self.assertEqual({x["selection"]["template_family"] for x in evidence["selected"] if x["role"] == "control"},
                         {"given_approximation", "final_rounding"})
        with self.assertRaisesRegex(ValueError, "boundary controls"):
            select_pools(protocol(), rows[:-1], 3, 2)
        with self.assertRaisesRegex(ValueError, "after family"):
            select_pools(protocol(), rows, 4, 2)

    def test_prepare_freezes_selection_and_revalidates_without_api(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data = base / "train.jsonl"
            rows = [{"problem": t, "solution": r'\boxed{1}', "split": "train"}
                    for t in (GAS, LADDER, POLE, APPROX, ROUND)]
            data.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            with patch('scripts.independent_mechanism_evidence.model_settings', return_value={}), patch(
                    'scripts.offline_loop_runtime.RecordedLLM.__call__', side_effect=AssertionError('API')):
                m = prepare(root=base/'run', data=data, protocol=protocol(), source_name='synthetic',
                            config=LoopConfig(opt_model='fake', exec_model='fake'), target_count=3, control_count=2)
            store = EvidenceStore(base/'run')
            self.assertEqual(len(store.rows()), 5)
            self.assertIn('inputs/selection.json', m['input_hashes'])
            self.assertEqual(m['model_calls_made_during_prepare'], 0)
            self.assertFalse((base/'run/requests').exists())

    def test_compiled_structured_candidate_refuses_boundary_before_llm(self):
        # Exercise the real compiler through the same guard insertion as a
        # reviewed run. No generator/network is needed for this boundary probe.
        from scripts.offline_operator_loop import register_candidates
        from scripts.operator_library import OperatorLibrary
        from scripts.offline_loop_protocol import digest
        from types import SimpleNamespace
        from test_independent_mechanism_evidence import design
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = OperatorLibrary(root/'work/operator_library/operators.json')
            (root/'inputs').mkdir()
            (root/'inputs/base_library.json').write_text(json.dumps({'operators': {}}))
            approval = {'subject_sha256':'s', 'review_sha256':'r'}
            store = SimpleNamespace(root=root, manifest={'pipeline':'aflow_independent_mechanism_evidence_v1',
                    'module_name':'test', 'protocol':protocol()}, put=lambda *a: None)
            plan = {'diagnosis': {}, 'review_authorization_sha256':digest(approval),
                    'designs':[design('StructuredAlpha'),design('StructuredBeta')]}
            with patch('scripts.independent_mechanism_evidence.authorization', return_value=approval):
                names = register_candidates(store, plan)
            record = library.get(names[0])
            class Operator:
                def __init__(self,*a): pass
            ns = {'Operator':Operator,'AsyncLLM':object,'Custom':lambda *a: None}
            exec(record['artifact']['prompt_code'],ns)
            exec(record['artifact']['class_code'],ns)
            result = asyncio.run(ns[names[0]](None)(APPROX))
            self.assertEqual(result['observation'],'outside_frozen_mechanism_scope')


if __name__ == '__main__': unittest.main()
