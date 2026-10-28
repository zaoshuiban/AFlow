"""Synthetic fixtures and mocked transports only; no research measurements."""
import asyncio
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.independent_mechanism_evidence import (
    EvidenceStore, apply_review, authorization, collect, diagnose, generate,
    generation_context, make_review_template, prepare, save_diagnostic,
)
from scripts.mechanism_review import EXACT_CLAIM, evaluate_review, rational_expression
from scripts.offline_loop_protocol import LoopConfig, RunStore, atomic_json, digest, file_hash, prepare_run, read_json
from scripts.offline_loop_runtime import BudgetExceeded, LiveBackend
from scripts.operator_library import OperatorLibrary
from scripts.optimizer_utils.operator_evolution import OperatorEvolveResponse


def design(name):
    return {"operator_name": name, "description": "Count using a frozen reviewed strategy",
            "reasoning": "Separate constrained counting steps", "skeleton_type": "single_step",
            "prompt_1": "Count exactly.", "prompt_1_name": name.upper() + "_PROMPT",
            "applicability_features": "counting_problem", "failure_boundary_features": "geometry_problem"}


class Backend:
    def __init__(self, stop=None):
        self.keys, self.contexts, self.stop = [], [], stop

    async def execute(self, *, item, operator, regression, key):
        if self.stop == len(self.keys): raise KeyboardInterrupt()
        self.keys.append(key)
        wrong = item["evidence_role"] == "target" and not key.endswith("/3")
        return {"prediction": "\\boxed{-1}" if wrong else item["solution"], "score": int(not wrong),
                "error": "", "calls": 2, "tokens": 8}

    async def generate_reviewed(self, *, context):
        self.contexts.append(context)
        return {"diagnosis": {"root_cause": context["hypothesis"]["claim"]},
                "review_authorization_sha256": context["authorization_sha256"],
                "designs": [design("ReviewedAlpha"), design("ReviewedBeta")]}

    def usage(self): return {"fake_executions": len(self.keys), "model_calls": 0}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.root = self.path / "run"
        self.data = self.path / "data.jsonl"
        self.rows = [{"problem": f"How many ways to arrange {n} unique evidence objects?",
                      "solution": "\\boxed{1}", "split": "train", "source_id": f"evidence-{n}"}
                     for n in range(500, 512)]
        self.rows += [{"problem": f"Find the area of evidence triangle {n}.", "solution": "\\boxed{1}",
                       "split": "train", "source_id": f"control-{n}"} for n in range(500, 512)]
        self.write_rows()
        self.protocol = {"scope": {"mechanism_id": "counting", "required_terms": ["How many"],
                                   "forbidden_terms": ["approximately"]},
                         "hypothesis": {"claim": "Counting constraint is not preserved",
                                        "violated_constraint": "All objects counted exactly once",
                                        "expected_intervention": "Use an explicit partition",
                                        "applicable_when": "Finite counting of arrangements",
                                        "not_applicable_when": "Approximation and geometry",
                                        "success_criteria": "Exact count with no omitted objects"},
                         "program_verifier": "none"}
        self.config = LoopConfig(opt_model="test-opt", exec_model="test-exec", candidate_count=2)
        self.settings = {role: {"model": "fake", "base_url": "https://invalid.example", "temperature": 0,
                                "top_p": 1} for role in ("opt", "exec")}
        patcher = patch("scripts.independent_mechanism_evidence.model_settings", return_value=self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_rows(self):
        self.data.write_text("".join(json.dumps(r) + "\n" for r in self.rows))

    def prepare(self, **kw):
        args = dict(root=self.root, data=self.data, protocol=self.protocol, config=self.config,
                    source_name="synthetic fixture, not research", target_count=3, control_count=2)
        args.update(kw)
        prepare(**args)
        return EvidenceStore(args["root"])

    def diagnosed(self, store=None):
        store = store or self.prepare()
        asyncio.run(collect(store, Backend()))
        _, packet = store.complete()
        response = {"analyses": [], "groups": [{"mechanism_id": self.protocol["scope"]["mechanism_id"],
                                                 "explanation": "same explicit constraint"}]}
        for c in packet["cases"]:
            for r in c["failed_runs"]:
                response["analyses"].append({"problem_id": c["problem_id"], "run": r["run"],
                    "verdict": "mechanism_hypothesis", "mechanism_id": self.protocol["scope"]["mechanism_id"],
                    "violated_constraint": "The exact answer must equal the stated expression",
                    "explanation": "Final answer violates the exact constraint", "applicability": "Exact tasks",
                    "counterexample": "Approximate estimates are excluded",
                    "evidence_ids": [r["fragments"][0]["fragment_id"]]})
        save_diagnostic(store, response, "synthetic imported response")
        return store

    def confirmed(self, store=None, program_only=False):
        store = store or self.diagnosed()
        review = make_review_template(store)
        records = [review["scope_review"]] + ([] if program_only else review["analyses"])
        for record in records:
            record.update(decision="confirm", reviewer="synthetic test reviewer",
                          reviewed_at="2026-10-08T12:00:00+08:00", rationale="Test fixture reviewed",
                          reference_check="Reference independently checked for fixture",
                          boundary_check="Control case and scope checked for fixture")
        return store, review

    def test_prepare_freezes_before_requests_and_does_not_overwrite(self):
        with patch("scripts.offline_loop_runtime.RecordedLLM.__call__", side_effect=AssertionError("network")):
            store = self.prepare()
        self.assertFalse((self.root / "requests").exists())
        self.assertEqual(read_json(self.root / "events/prepared.json")["manifest_sha256"], digest(store.manifest))
        self.assertEqual(len(store.manifest["sample_index"]), 5)
        self.assertIn("model_settings.json", store.manifest["input_hashes"])
        with self.assertRaises(FileExistsError): self.prepare()

    def test_sibling_reservations_and_explicit_exclusions(self):
        first = self.prepare()
        excluded = self.path / "excluded.jsonl"
        excluded.write_text(json.dumps(self.rows[0]) + "\n")
        second = self.prepare(root=self.path / "second", exclude_paths=[excluded])
        ids = {r["problem_id"] for r in first.rows()}
        self.assertFalse(ids & {r["problem_id"] for r in second.rows()})
        self.assertNotIn(self.rows[0]["problem"], [r["problem"] for r in second.rows()])

    def test_shared_family_rejected(self):
        for r in self.rows[:2]: r["family_id"] = "same-family"
        self.write_rows()
        with self.assertRaisesRegex(ValueError, "variants"): self.prepare()

    def test_frozen_samples_and_code_drift_stop(self):
        self.prepare()
        with patch("scripts.independent_mechanism_evidence.code_fingerprints", return_value={}):
            with self.assertRaisesRegex(ValueError, "code changed"): EvidenceStore(self.root)
        rows = read_json(self.root / "inputs/samples.json")
        rows[0]["problem"] += " changed"
        atomic_json(self.root / "inputs/samples.json", rows)
        with self.assertRaisesRegex(ValueError, "input changed"): EvidenceStore(self.root)

    def test_incomplete_collection_blocks_diagnosis_and_resume_skips_completed(self):
        store = self.prepare()
        first = Backend(stop=4)
        with self.assertRaises(KeyboardInterrupt): asyncio.run(collect(store, first))
        with self.assertRaisesRegex(ValueError, "Complete all"): make_review_template(store)
        second = Backend()
        result = asyncio.run(collect(EvidenceStore(self.root), second))
        self.assertEqual(result["observations"], 15)
        self.assertFalse(set(first.keys) & set(second.keys))
        observations, packet = store.complete()
        self.assertEqual(sum(o["score"] == 1 for o in observations), 9)
        self.assertEqual(len(packet["cases"]), 3)
        third = Backend()
        asyncio.run(collect(store, third))
        self.assertFalse(third.keys)

    def test_budget_exhaustion_never_marks_baseline_complete(self):
        store = self.prepare()
        backend = Backend()
        backend.execute = AsyncMock(side_effect=BudgetExceeded("cap"))
        with self.assertRaises(BudgetExceeded): asyncio.run(collect(store, backend))
        with self.assertRaisesRegex(ValueError, "Complete all"): store.complete()

    def test_exception_is_retained_but_not_in_error_rate(self):
        store = self.prepare()
        backend = Backend()
        original = backend.execute
        async def execute(**kw):
            result = await original(**kw)
            if kw["key"].endswith("/3") and kw["item"]["evidence_role"] == "target":
                result.update(error="TimeoutError", score=0)
            return result
        backend.execute = execute
        asyncio.run(collect(store, backend))
        self.diagnosed(store)
        _, review = self.confirmed(store)
        report = apply_review(store, review)
        self.assertEqual(report["target_valid_observations"], 6)
        self.assertEqual(report["observed_error_rate"], 1)
        self.assertEqual(report["execution_errors"], 3)

    def test_references_and_truth_flags_alone_do_not_authorize(self):
        store = self.diagnosed()
        review = make_review_template(store)
        review["semantic_verified"] = True
        for a in review["analyses"]: a["semantic_verified"] = True
        result = apply_review(store, review)
        self.assertEqual(result["status_counts"], {"reference_valid": 6})
        self.assertFalse(result["eligible_for_generation"])
        backend = Backend()
        with self.assertRaises(ValueError): asyncio.run(generate(store, backend))
        self.assertFalse(backend.contexts)

    def test_stale_or_misattributed_review_rejected(self):
        store, review = self.confirmed()
        stale = copy.deepcopy(review)
        stale["subject_sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "stale"): apply_review(store, stale)
        stale = copy.deepcopy(review)
        stale["analyses"][0]["analysis_sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "analysis changed"): apply_review(store, stale)
        review["analyses"][0]["reviewer"] = ""
        with self.assertRaisesRegex(ValueError, "reviewer"): apply_review(store, review)

    def test_same_mechanism_repeated_confirmations_required(self):
        store, review = self.confirmed()
        review["analyses"][0]["decision"] = "pending"
        report = apply_review(store, review)
        self.assertEqual(len(report["semantically_supported_problem_ids"]), 2)
        self.assertFalse(report["eligible_for_generation"])

    def test_human_rejection_blocks_even_with_enough_other_support(self):
        store, review = self.confirmed()
        review["scope_review"]["decision"] = "reject"
        report = apply_review(store, review)
        self.assertEqual(report["status"], "refuted")

    def test_human_confirmation_compiles_scoped_candidates_with_provenance(self):
        store, review = self.confirmed()
        report = apply_review(store, review)
        self.assertEqual(report["status_counts"], {"human_confirmed": 6})
        backend = Backend()
        names = asyncio.run(generate(store, backend))
        self.assertEqual(names, ["ReviewedAlpha", "ReviewedBeta"])
        self.assertFalse(report["promotion_authorized"])
        context = backend.contexts[0]
        for row in store.rows():
            if row["evidence_role"] == "control": self.assertNotIn(row["problem"], json.dumps(context))
        library = OperatorLibrary(self.root / "work/operator_library/operators.json", auto_create=False)
        record = library.get(names[0])
        self.assertEqual(record["status"], "candidate")
        self.assertIn("review_authorization_sha256", record["provenance"])
        # Real compiler output can refuse without an API call or a base class.
        class Operator:
            def __init__(self, *a): pass
        ns = {"Operator": Operator, "AsyncLLM": object, "Custom": lambda *a: None}
        exec(record["artifact"]["prompt_code"], ns)
        exec(record["artifact"]["class_code"], ns)
        result = asyncio.run(ns[names[0]](None)("How many objects approximately?"))
        self.assertEqual(result["observation"], "outside_frozen_mechanism_scope")
        second = Backend()
        asyncio.run(generate(store, second))
        self.assertFalse(second.contexts)
        with self.assertRaises(FileExistsError): apply_review(store, review)

    def test_generator_cannot_replace_approved_diagnosis(self):
        store, review = self.confirmed()
        apply_review(store, review)
        backend = LiveBackend.__new__(LiveBackend)
        backend.store = store
        fake_llm = AsyncMock()
        backend.llm = lambda *a: fake_llm
        with patch("scripts.optimizer_utils.operator_evolution.OperatorEvolver.diagnose_failures",
                   new=AsyncMock(side_effect=AssertionError("must not rediagnose"))), patch(
                   "scripts.optimizer_utils.operator_evolution.OperatorEvolver.design_candidates",
                   new=AsyncMock(return_value=[OperatorEvolveResponse(**design("ReviewedAlpha")),
                                              OperatorEvolveResponse(**design("ReviewedBeta"))])) as call:
            names = asyncio.run(generate(store, backend))
        self.assertEqual(len(names), 2)
        kwargs = call.call_args.kwargs
        self.assertIn(self.protocol["hypothesis"]["claim"], kwargs["diagnosis"].root_cause)
        self.assertIn(self.protocol["hypothesis"]["violated_constraint"], kwargs["diagnosis"].root_cause)
        self.assertEqual(kwargs["design_policy"]["frozen_input_scope"], self.protocol["scope"])

    def test_no_failure_diagnosis_needs_no_api(self):
        store = self.prepare()
        backend = Backend()
        async def correct(**kw): return {"prediction": kw["item"]["solution"], "score": 1, "error": ""}
        backend.execute = correct
        asyncio.run(collect(store, backend))
        with patch("scripts.offline_loop_runtime.RecordedLLM.__call__", side_effect=AssertionError("API")):
            result = asyncio.run(diagnose(store))
        self.assertFalse(result["response"]["analyses"])

    def test_program_verification_records_witness_not_imported_boolean(self):
        self.rows = [{"problem": f"Compute exactly: {n} / 3.", "solution": f"\\boxed{{{n}/3}}", "split": "train"}
                     for n in range(40, 46)] + self.rows[12:]
        self.write_rows()
        self.protocol["scope"] = {"mechanism_id": "exact_arithmetic_mismatch", "required_terms": ["Compute exactly:"],
                                  "forbidden_terms": []}
        self.protocol["hypothesis"]["claim"] = EXACT_CLAIM
        self.protocol["program_verifier"] = "explicit_exact_arithmetic_v1"
        store = self.diagnosed()
        _, review = self.confirmed(store, program_only=True)
        result = apply_review(store, review)
        self.assertTrue(result["eligible_for_generation"])
        self.assertEqual(result["status_counts"], {"program_verified": 6})
        self.assertIn("computed_exact", result["analyses"][0]["program_check"]["witness"])
        context = generation_context(store, authorization(store))
        self.assertNotIn("explanation", context["evidence"][0]["evidence"])

    def test_program_verifier_refutes_wrong_reference(self):
        self.rows = [{"problem": f"Compute exactly: {n} / 3.", "solution": "\\boxed{999}", "split": "train"}
                     for n in range(40, 46)] + self.rows[12:]
        self.write_rows()
        self.protocol["scope"] = {"mechanism_id": "exact_arithmetic_mismatch", "required_terms": ["Compute exactly:"],
                                  "forbidden_terms": []}
        self.protocol["hypothesis"]["claim"] = EXACT_CLAIM
        self.protocol["program_verifier"] = "explicit_exact_arithmetic_v1"
        store, review = self.confirmed()
        result = apply_review(store, review)
        self.assertEqual(result["status"], "refuted")
        self.assertEqual(result["status_counts"], {"contradicted": 6})

    def test_unsupported_arithmetic_is_not_executed(self):
        for expression in ("__import__('os')", "9**999999", "1//2", "x+1", "9" * 241):
            with self.assertRaises(ValueError): rational_expression(expression)
        self.assertEqual(str(rational_expression("0.1 + 0.2")), "3/10")

    def test_constraint_contradiction_cannot_be_overridden_by_human(self):
        self.rows = [{"problem": rf'The radius of the circle is $\sqrt{{50}}$ cm. Find the square of the distance from $B$ to the center. Case {n}.',
                      "solution": r'\boxed{26}', "split": "train"} for n in range(6)] + self.rows[12:]
        self.write_rows()
        self.protocol["scope"] = {"mechanism_id": "circle_position", "required_terms": ["radius"], "forbidden_terms": []}
        store = self.prepare()
        asyncio.run(collect(store, Backend()))
        _, packet = store.complete()
        response = {"groups": [{"mechanism_id": "circle_position", "explanation": "position"}], "analyses": []}
        for case in packet["cases"]:
            for failed in case["failed_runs"]:
                response["analyses"].append({"problem_id": case["problem_id"], "run": failed["run"],
                    "mechanism_id": "circle_position", "verdict": "mechanism_hypothesis",
                    "violated_constraint": "The configuration selects the root with B outside the circle.",
                    "explanation": "outside root", "applicability": "circle", "counterexample": "inside",
                    "evidence_ids": [failed["fragments"][0]["fragment_id"]]})
        save_diagnostic(store, response, "test import")
        _, review = self.confirmed(store)
        report = apply_review(store, review)
        self.assertEqual(report["status"], "refuted")
        self.assertEqual(report["status_counts"], {"contradicted": 6})

    def test_model_drift_stops_before_diagnostic_request(self):
        store = self.prepare()
        asyncio.run(collect(store, Backend()))
        with patch("scripts.independent_mechanism_evidence.model_settings", return_value={}):
            with patch("scripts.offline_loop_runtime.RecordedLLM.__call__", side_effect=AssertionError("API")):
                with self.assertRaisesRegex(ValueError, "Model settings"): asyncio.run(diagnose(store))

    def test_baseline_export_and_packet_tamper_detected(self):
        store = self.prepare()
        asyncio.run(collect(store, Backend()))
        original = read_json(self.root / "observations.json")
        modified = copy.deepcopy(original)
        modified[0]["prediction"] = "modified"
        atomic_json(self.root / "observations.json", modified)
        with self.assertRaisesRegex(ValueError, "Exported observations"): store.complete()
        atomic_json(self.root / "observations.json", original)
        packet = read_json(self.root / "packet.json")
        packet["cases"][0]["reference_solution"] = "different reference"
        atomic_json(self.root / "packet.json", packet)
        with self.assertRaisesRegex(ValueError, "packet differs"): store.complete()

    def test_main_loop_excludes_prior_evidence_samples(self):
        store = self.prepare()
        new = self.path / "main_loop"
        prepare_run(root=new, data=self.data, source_name="test fixture", config=self.config,
                    counts={role: 2 for role in ("discovery", "design", "gate", "regression")})
        old_ids = {r["problem_id"] for r in store.rows()}
        main = RunStore(new)
        for role in ("discovery", "design", "gate", "regression"):
            self.assertFalse(old_ids & {r["problem_id"] for r in main.rows(role)})

    def test_real_adapters_record_complete_prompts_and_generate_without_rediagnosing(self):
        self.settings = {role: {"model": f"test-{role}", "base_url": "https://invalid.example",
                                "temperature": 0, "top_p": 1} for role in ("opt", "exec")}
        requests = []
        configs = SimpleNamespace(get=lambda name: SimpleNamespace(
            model=name, base_url="https://invalid.example", temperature=0, top_p=1, key="SECRET-NOT-IN-ARTIFACTS"))
        with patch("scripts.independent_mechanism_evidence.model_settings", return_value=self.settings):
            store = self.prepare()

        class Client:
            designs = 0
            def __init__(self, **kw): self.chat = SimpleNamespace(completions=self)
            async def create(self, **kw):
                prompt = kw["messages"][0]["content"]
                requests.append(prompt)
                if kw["model"] == "test-opt":
                    if "Independent baseline evidence:" in prompt:
                        _, packet = store.complete()
                        analyses = [{"problem_id": c["problem_id"], "run": r["run"],
                                     "mechanism_id": "counting", "verdict": "mechanism_hypothesis",
                                     "violated_constraint": "count once", "explanation": "miscount",
                                     "applicability": "arrangements", "counterexample": "geometry",
                                     "evidence_ids": [r["fragments"][0]["fragment_id"]]}
                                    for c in packet["cases"] for r in c["failed_runs"]]
                        output = json.dumps({"analyses": analyses, "groups": [{"mechanism_id": "counting", "explanation": "miscount"}]})
                    else:
                        if "<operator_name>" not in prompt:
                            raise AssertionError("Unexpected rediagnosis")
                        Client.designs += 1
                        values = design("ReviewedAlpha" if Client.designs == 1 else "ReviewedBeta")
                        output = "\n".join(f"<{key}>{value}</{key}>" for key, value in values.items())
                else:
                    output = "\\boxed{-1}" if "How many" in prompt else "\\boxed{1}"
                return SimpleNamespace(model="fake-version", system_fingerprint="fake-fingerprint",
                    choices=[SimpleNamespace(message=SimpleNamespace(content=output), finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20))
            async def close(self): pass

        with patch("scripts.offline_loop_runtime.LLMsConfig.default", return_value=configs), patch("openai.AsyncOpenAI", Client):
            backend = LiveBackend(store)
            asyncio.run(collect(store, backend))
            with patch("scripts.independent_mechanism_evidence.model_settings", return_value=self.settings):
                asyncio.run(diagnose(store))
            _, review = self.confirmed(store)
            apply_review(store, review)
            names = asyncio.run(generate(store, backend))
        self.assertEqual(len(names), 2)
        records = [read_json(p) for p in (self.root / "requests").glob("*.json")]
        self.assertEqual(len(records), 33)  # 5 questions x 3 repeats x 2 stages + diagnosis + 2 designs.
        self.assertTrue(all(r.get("prompt") and r.get("response") for r in records))
        self.assertTrue(all(r["input_sha256"] for r in records))
        self.assertNotIn("SECRET-NOT-IN-ARTIFACTS", json.dumps(records))
        self.assertEqual(len(requests), 33)
        # Completed commands need no further model transport.
        with patch("openai.AsyncOpenAI", side_effect=AssertionError("replay called API")):
            asyncio.run(collect(store, backend))
            asyncio.run(generate(store, backend))
        baseline_file = next(p for p in (self.root / "requests").glob("*.json")
                             if read_json(p)["key"].startswith("evidence/baseline/"))
        changed = read_json(baseline_file)
        changed["response"] = "altered original model response"
        atomic_json(baseline_file, changed)
        with self.assertRaisesRegex(ValueError, "request evidence changed"):
            authorization(store)


if __name__ == "__main__": unittest.main()
