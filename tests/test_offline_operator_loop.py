"""No network or paid models: orchestration and real compiled-artifact tests."""
import asyncio
import json
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.offline_loop_protocol import (
    REPO_ROOT, LoopConfig, RunStore, atomic_json, file_hash, prepare_run,
    read_json, run_lock, verify_run,
)
from scripts.offline_loop_runtime import (
    BudgetExceeded, LiveBackend, RecordedLLM, RecordedRequestFailed, RequestLedger,
)
from scripts.offline_operator_loop import OfflineLoop, load_released_router


def design(name, *, good=True):
    return {
        "operator_name": name, "description": "Finite counting decomposition" if good else "Independent enumeration check",
        "reasoning": "Reusable counting instructions within a declared boundary",
        "skeleton_type": "single_step", "prompt_1": "Count exactly and return the final answer.",
        "prompt_1_name": name.upper() + "_PROMPT", "prompt_2": "", "prompt_2_name": "",
        "applicability_features": "counting_problem", "failure_boundary_features": "geometry_problem",
    }


class FakeBackend:
    def __init__(self, mode="pass"):
        self.mode = mode
        self.executions = []
        self.generated = []
        self.interrupt_after = None

    async def generate(self, **inputs):
        self.generated.append(inputs)
        return {"diagnosis": {"missing_capability": "counting", "root_cause": "test hypothesis"},
                "designs": [design("OfflineCountingAlpha"), design("OfflineCountingBeta", good=False)]}

    async def execute(self, *, item, operator, regression, key):
        if self.interrupt_after is not None and len(self.executions) >= self.interrupt_after:
            raise KeyboardInterrupt()
        self.executions.append(key)
        target = "How many" in item["problem"]
        correct = not target or operator == "OfflineCountingAlpha"
        if self.mode == "no_failures":
            correct = True
        if self.mode == "bad_design" and operator and key.startswith("design/"):
            correct = not target
        if self.mode == "bad_gate" and operator and key.startswith("gate/"):
            correct = not target
        if self.mode == "regression" and operator and key.startswith("gate/") and regression:
            correct = False
        error = "TimeoutError" if self.mode == "infrastructure" else ""
        return {"score": int(correct and not error), "prediction": "\\boxed{1}" if correct else "\\boxed{-1}",
                "extracted": "1" if correct else "-1", "error": error, "candidate_error": "",
                "declined": bool(operator and not target), "selected_operator": operator if target and operator else None,
                "used_fallback": not target or operator is None, "tokens": 10, "calls": 1,
                "unknown_usage_calls": 0, "elapsed_seconds": 0.001}

    def usage(self):
        return {"test_backend": True, "api_requests": 0, "fake_executions": len(self.executions), "cost_usd": None}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.data = self.directory / "train.jsonl"
        rows = []
        for n in range(50, 130):
            rows.append({"problem": f"How many ways can {n} distinct objects be arranged in a row?",
                         "solution": "\\boxed{1}", "type": "Counting", "split": "train"})
            rows.append({"problem": f"Find the area of a triangle with base {n} and height 2.",
                         "solution": f"\\boxed{{{n}}}", "type": "Geometry", "split": "train"})
        self.data.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.root = self.directory / "run"
        self.config = LoopConfig(opt_model="test-opt", exec_model="test-exec", candidate_count=2)
        self.counts = {role: 24 for role in ("discovery", "design", "gate", "regression")}

    def prepare(self, **overrides):
        args = dict(root=self.root, data=self.data, source_name="synthetic test fixture; NOT research evidence",
                    counts=self.counts, config=self.config)
        args.update(overrides)
        prepare_run(**args)
        return RunStore(args["root"])

    def execute(self, mode="pass"):
        store = self.prepare()
        backend = FakeBackend(mode)
        result = asyncio.run(OfflineLoop(store, backend).run())
        return store, backend, result


class OfflineLoopTests(Fixture):
    def test_dependency_drift_refuses_frozen_run(self):
        store = self.prepare()
        self.assertIn("antlr4-python3-runtime", store.manifest["runtime_environment"]["packages"])
        with patch("scripts.offline_loop_protocol.runtime_environment", return_value={"changed": True}):
            with self.assertRaisesRegex(ValueError, "Runtime dependencies changed"):
                verify_run(self.root)

    def test_complete_loop_and_real_router_artifact_without_network(self):
        original = [REPO_ROOT / "workspace/MATH/operator_library/operators.json",
                    *(REPO_ROOT / "workspace/MATH/workflows/template" / name
                      for name in ("operator.py", "operator.json", "op_prompt.py", "operator_an.py"))]
        hashes = {p: file_hash(p) for p in original}
        store, backend, result = self.execute()
        self.assertTrue(result["promoted"], result)
        self.assertEqual(result["winner"], "OfflineCountingAlpha")
        self.assertEqual(hashes, {p: file_hash(p) for p in original})
        self.assertEqual(len(backend.generated), 1)
        visible = backend.generated[0]["failure_cases"]
        for role in ("design", "gate", "regression"):
            self.assertTrue(all(row["problem"] not in visible for row in store.rows(role)))

        class FakeLLM:
            async def call_with_format(self, prompt, formatter):
                return {"response": "\\boxed{1}", "status": "success"}

        router = load_released_router(self.root, FakeLLM())
        question = next(r["problem"] for r in store.rows("gate") if "How many" in r["problem"])
        response = asyncio.run(router(question))
        self.assertEqual(response["selected_operator"], "OfflineCountingAlpha")
        self.assertEqual(response["response"], "\\boxed{1}")
        fallback = asyncio.run(router("Find the area of a triangle with base 3 and height 2."))
        self.assertTrue(fallback["used_fallback"])
        again = asyncio.run(OfflineLoop(RunStore(self.root), FakeBackend()).run())
        self.assertEqual(result, again)

    def test_no_stable_failure_never_generates(self):
        _, backend, result = self.execute("no_failures")
        self.assertEqual(result["status"], "archived_no_stable_mechanism")
        self.assertFalse(backend.generated)

    def test_timeouts_are_not_mechanism_failures(self):
        _, backend, result = self.execute("infrastructure")
        self.assertEqual(result["status"], "archived_no_stable_mechanism")
        self.assertGreater(result["screen"]["execution_errors_excluded"], 0)
        self.assertFalse(backend.generated)

    def test_bad_design_never_opens_hidden_evaluation(self):
        _, backend, result = self.execute("bad_design")
        self.assertEqual(result["status"], "archived_design_failed")
        self.assertFalse(any(key.startswith("gate/") for key in backend.executions))

    def test_hidden_failure_does_not_try_runner_up(self):
        store, backend, result = self.execute("bad_gate")
        self.assertEqual(result["status"], "archived_hidden_failed")
        self.assertFalse(any("gate/OfflineCountingBeta/" in key for key in backend.executions))
        before = len(backend.executions)
        asyncio.run(OfflineLoop(store, backend).run())
        self.assertEqual(before, len(backend.executions))
        self.assertFalse((self.root / "release").exists())

    def test_actual_fallback_regression_is_not_imputed_as_baseline_score(self):
        _, _, result = self.execute("regression")
        self.assertEqual(result["status"], "archived_hidden_failed")
        self.assertGreater(result["gate_metrics"]["regression_losses"], 0)

    def test_checkpoint_resume_does_not_repeat_completed_jobs(self):
        store = self.prepare()
        backend = FakeBackend()
        backend.interrupt_after = 5
        with self.assertRaises(KeyboardInterrupt):
            asyncio.run(OfflineLoop(store, backend).run())
        completed = set(backend.executions)
        resumed = FakeBackend()
        result = asyncio.run(OfflineLoop(RunStore(self.root), resumed).run())
        self.assertTrue(result["promoted"])
        self.assertFalse(completed.intersection(resumed.executions))

    def test_hidden_stage_resume_preserves_candidate_and_completed_pairs(self):
        store = self.prepare()

        class InterruptedGate(FakeBackend):
            async def execute(self, **kwargs):
                if kwargs["key"].startswith("gate/") and sum(k.startswith("gate/") for k in self.executions) >= 4:
                    raise KeyboardInterrupt()
                return await super().execute(**kwargs)

        first = InterruptedGate()
        with self.assertRaises(KeyboardInterrupt):
            asyncio.run(OfflineLoop(store, first).run())
        freeze = file_hash(self.root / "checkpoints/confirmation_freeze.json")
        second = FakeBackend()
        result = asyncio.run(OfflineLoop(RunStore(self.root), second).run())
        self.assertTrue(result["promoted"])
        self.assertEqual(freeze, file_hash(self.root / "checkpoints/confirmation_freeze.json"))
        self.assertFalse(set(first.executions).intersection(second.executions))
        self.assertFalse(second.generated)

    def test_candidate_contract_tamper_stops_before_hidden(self):
        store = self.prepare()
        backend = FakeBackend()
        loop = OfflineLoop(store, backend)
        screen, observations = asyncio.run(loop.discover())
        names = asyncio.run(loop.generate(screen, observations))
        library_path = self.root / "work/operator_library/operators.json"
        payload = read_json(library_path)
        payload["operators"][names[0]]["applicability_rules"][0]["weight"] = 0.99
        atomic_json(library_path, payload)
        with self.assertRaisesRegex(ValueError, "Archived candidate changed"):
            asyncio.run(OfflineLoop(RunStore(self.root), backend).run())
        self.assertFalse(any(key.startswith("gate/") for key in backend.executions))

    def test_router_rejection_is_not_reported_as_promotion(self):
        store = self.prepare()
        with patch("scripts.offline_operator_loop.ContextAwareOperatorRouter.decide",
                   return_value=SimpleNamespace(selected=None)):
            result = asyncio.run(OfflineLoop(store, FakeBackend()).run())
        self.assertEqual(result["status"], "archived_route_not_selectable")
        self.assertFalse(result["promoted"])
        library = read_json(self.root / "release/operator_library/operators.json")
        self.assertEqual(library["operators"]["OfflineCountingAlpha"]["status"], "rejected")

    def test_release_tamper_blocks_loading(self):
        self.execute()
        path = self.root / "release/template/op_prompt.py"
        path.write_text(path.read_text() + "\n# edited\n")
        with self.assertRaisesRegex(ValueError, "Released artifact changed"):
            load_released_router(self.root, None)

    def test_prepare_refuses_existing_directory(self):
        self.prepare()
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_frozen_input_tamper_detected(self):
        self.prepare()
        path = self.root / "inputs/gate.json"
        rows = read_json(path)
        rows[0]["solution"] = "edited"
        atomic_json(path, rows)
        with self.assertRaisesRegex(ValueError, "Frozen input changed"):
            verify_run(self.root)

    def test_previous_reserved_roles_are_excluded_from_new_sibling_run(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "unique unused training rows"):
            self.prepare(root=self.directory / "another")

    def test_test_rows_cannot_be_relabelled_by_cli(self):
        rows = [json.loads(line) for line in self.data.read_text().splitlines()]
        rows[0]["split"] = "test"
        self.data.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaisesRegex(ValueError, "non-training row"):
            self.prepare()
        self.assertFalse(self.root.exists())

    def test_shared_source_variant_id_rejected(self):
        rows = [json.loads(line) for line in self.data.read_text().splitlines()]
        rows[0]["source_id"] = rows[1]["source_id"] = "same-original-task"
        self.data.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaisesRegex(ValueError, "Multiple variants"):
            self.prepare()

    def test_model_budget_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.prepare(config=replace(self.config, max_requests=0))

    def test_concurrent_writer_is_refused(self):
        self.prepare()
        with run_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "Another process"):
                with run_lock(self.root):
                    self.fail("Second writer entered")


class LedgerTests(Fixture):
    def test_explicit_thinking_mode_is_sent_and_recorded(self):
        store = self.prepare(config=replace(self.config, thinking_mode="disabled"))
        model_config = SimpleNamespace(model="deepseek-v4-flash", base_url="https://api.deepseek.com",
                                       key="never-written", temperature=0, top_p=1)
        requests = []

        class Client:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=self)

            async def create(self, **kwargs):
                requests.append(kwargs)
                return SimpleNamespace(model="fake-version", usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                                       choices=[SimpleNamespace(message=SimpleNamespace(content="1"), finish_reason="stop")])

        ledger = RequestLedger(store)
        with patch("openai.AsyncOpenAI", Client):
            self.assertEqual(asyncio.run(RecordedLLM(ledger, model_config, "mode")("Q")), "1")
        self.assertEqual(requests[0]["extra_body"], {"thinking": {"type": "disabled"}})
        record = read_json(next((self.root / "requests").glob("*.json")))
        self.assertEqual(record["api_options"]["extra_body"], requests[0]["extra_body"])

    def test_explicit_thinking_mode_refuses_unknown_provider(self):
        config = SimpleNamespace(base_url="https://example.invalid")
        self.assertEqual(RecordedLLM.api_options(config, "provider-default"), {})
        with self.assertRaisesRegex(ValueError, "official DeepSeek"):
            RecordedLLM.api_options(config, "disabled")

    def test_truncated_or_empty_outputs_are_recorded_errors_with_usage(self):
        store = self.prepare()
        config = SimpleNamespace(model="test", key="unused", base_url="https://example.invalid", temperature=0, top_p=1)
        ledger = RequestLedger(store)

        class Client:
            content, reason = "", "stop"

            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=self)

            async def create(self, **kwargs):
                return SimpleNamespace(model="fake-version", usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7),
                                       choices=[SimpleNamespace(message=SimpleNamespace(content=self.content), finish_reason=self.reason)])

        for key, content, reason, error in [("truncated", "partial", "length", "output_token_limit"),
                                            ("empty", "  ", "stop", "empty_model_response")]:
            Client.content, Client.reason = content, reason
            with self.subTest(key=key), patch("openai.AsyncOpenAI", Client):
                with self.assertRaisesRegex(RecordedRequestFailed, error):
                    asyncio.run(RecordedLLM(ledger, config, key)("Q"))
            # A failed, possibly billed response must not be resubmitted on resume.
            with patch("openai.AsyncOpenAI", side_effect=AssertionError("no network on resume")):
                with self.assertRaisesRegex(RecordedRequestFailed, error):
                    asyncio.run(RecordedLLM(ledger, config, key)("Q"))
        self.assertEqual(ledger.summary()["successful_requests"], 0)
        self.assertEqual(ledger.summary()["total_tokens"], 14)
        self.assertEqual(ledger.summary()["requests_with_unknown_usage"], 0)

    def test_request_budget_is_durable_and_replays_do_not_charge(self):
        store = self.prepare(config=replace(self.config, max_requests=1))
        ledger = RequestLedger(store)
        inputs = {"model_settings": {"model": "test"}, "prompt": "a"}
        path, cached = ledger.start("one", inputs)
        self.assertIsNone(cached)
        record = read_json(path)
        record.update(status="completed", response="result", usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5})
        atomic_json(path, record)
        restarted = RequestLedger(store)
        self.assertEqual(restarted.start("one", inputs)[1]["response"], "result")
        with self.assertRaises(BudgetExceeded):
            restarted.start("two", inputs)
        self.assertEqual(restarted.summary()["total_tokens"], 5)
        self.assertIsNone(restarted.summary()["cost_usd"])

    def test_interrupted_request_becomes_unknown_failure_not_resubmission(self):
        store = self.prepare()
        ledger = RequestLedger(store)
        inputs = {"model_settings": {"model": "test"}}
        ledger.start("lost", inputs)
        _, cached = RequestLedger(store).start("lost", inputs)
        self.assertEqual(cached["status"], "failed")
        self.assertEqual(cached["error"], "interrupted_result_unknown")
        self.assertEqual(ledger.summary()["requests_with_unknown_usage"], 1)

    def test_prompt_change_cannot_reuse_request(self):
        store = self.prepare()
        ledger = RequestLedger(store)
        ledger.start("one", {"model_settings": {}, "prompt": "old"})
        with self.assertRaisesRegex(ValueError, "Request changed"):
            ledger.start("one", {"model_settings": {}, "prompt": "new"})

    def test_recorded_llm_uses_fake_transport_and_replays_without_transport(self):
        store = self.prepare()
        ledger = RequestLedger(store)
        config = SimpleNamespace(model="test", key="do-not-persist", base_url="https://example.invalid",
                                 temperature=0, top_p=1)
        requests = []

        class Client:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=self)
                self.kwargs = kwargs
                requests.append(kwargs)

            async def create(self, **kwargs):
                self.kwargs.update(request=kwargs)
                return SimpleNamespace(model="fake-version", usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5),
                                       choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))])

            async def close(self):
                pass

        async def work():
            llm = RecordedLLM(ledger, config, "test")
            with patch("openai.AsyncOpenAI", Client):
                self.assertEqual(await llm("question"), "hello")
                await llm.close()
            other = RecordedLLM(RequestLedger(store), config, "test")
            with patch("openai.AsyncOpenAI", side_effect=AssertionError("network not allowed")):
                self.assertEqual(await other("question"), "hello")

        asyncio.run(work())
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["max_retries"], 0)
        self.assertEqual(requests[0]["request"]["max_tokens"], store.config.max_output_tokens)
        self.assertNotIn("do-not-persist", "".join(p.read_text() for p in (self.root / "requests").glob("*.json")))


class LiveAdapterIntegrationTests(Fixture):
    def test_real_generation_compiler_scorer_router_with_fake_api_transport(self):
        store = self.prepare()
        opt_prompts = []
        all_requests = []
        class Client:
            design_count = 0

            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=self)

            async def create(self, **kwargs):
                all_requests.append(kwargs)
                prompt = kwargs["messages"][0]["content"]
                if kwargs["model"] == "test-opt":
                    opt_prompts.append(prompt)
                    if "<operator_name>" in prompt:
                        Client.design_count += 1
                        values = design("OfflineCountingAlpha" if Client.design_count == 1 else "OfflineCountingBeta",
                                        good=Client.design_count == 1)
                    else:
                        values = {"dominant_error_type": "counting", "root_cause": "missing representation",
                                  "missing_capability": "finite counting decomposition", "evidence": "several failures",
                                  "applicable_when": "counting", "not_applicable_when": "geometry",
                                  "success_criteria": "independent improvement"}
                    response = "\n".join(f"<{key}>{value}</{key}>" for key, value in values.items())
                elif "How many" in prompt:
                    response = "\\boxed{1}" if prompt.startswith("Count exactly") else "\\boxed{-1}"
                else:
                    match = re.search(r"base (\d+)", prompt)
                    response = "\\boxed{" + (match.group(1) if match else "0") + "}"
                return SimpleNamespace(model="test-backend-version", usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20),
                                       choices=[SimpleNamespace(message=SimpleNamespace(content=response))])

            async def close(self):
                pass

        configs = SimpleNamespace(get=lambda name: SimpleNamespace(
            model=name, key="never-written", base_url="https://example.invalid", temperature=0, top_p=1))
        with patch("scripts.offline_loop_runtime.LLMsConfig.default", return_value=configs), patch("openai.AsyncOpenAI", Client):
            backend = LiveBackend(store)
            result = asyncio.run(OfflineLoop(store, backend).run())
        self.assertTrue(result["promoted"], result)
        self.assertEqual(Client.design_count, 2)
        self.assertEqual(result["usage"]["requests_reserved"], len(all_requests))
        self.assertEqual(result["usage"]["total_tokens"], 20 * len(all_requests))
        for role in ("design", "gate", "regression"):
            for row in store.rows(role):
                self.assertTrue(all(row["problem"] not in prompt for prompt in opt_prompts))
        self.assertNotIn("never-written", (self.root / "model_settings.json").read_text())

    def test_execution_budget_and_model_settings_drift_fail_closed(self):
        store = self.prepare(config=replace(self.config, max_requests=1))
        configs = SimpleNamespace(get=lambda name: SimpleNamespace(
            model=name, key="unused", base_url="https://example.invalid", temperature=0, top_p=1))
        with patch("scripts.offline_loop_runtime.LLMsConfig.default", return_value=configs):
            backend = LiveBackend(store)
        backend.ledger.start("used", {"model_settings": {}})
        with self.assertRaises(BudgetExceeded):
            asyncio.run(backend.execute(item=store.rows("discovery")[0], operator=None, regression=False, key="new"))
        configs.get = lambda name: SimpleNamespace(model=name, key="unused", base_url="https://example.invalid", temperature=1, top_p=1)
        with patch("scripts.offline_loop_runtime.LLMsConfig.default", return_value=configs):
            with self.assertRaisesRegex(ValueError, "Model settings changed"):
                LiveBackend(store)


if __name__ == "__main__":
    unittest.main()
