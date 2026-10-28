"""Model adapter for the offline loop; no API calls at import or prepare time."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
import time
import types
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from benchmarks.math import MATHBenchmark
from scripts.async_llm import LLMsConfig
from scripts.contextual_operator_router import GenericTwoStageSolver
from scripts.formatter import FormatError
from scripts.offline_loop_protocol import RunStore, atomic_json, digest, read_json
from scripts.operator_library import OperatorLibrary, combined_artifact_sha256
from scripts.optimizer_utils.operator_evolution import OperatorEvolver

PROMPT_POLICY = {
    "execution_mode": "prompt_only", "programmer_allowed": False,
    "allowed_skeleton_types": ["single_step", "two_stage", "verify_fix"],
    "max_llm_calls_per_problem": 2,
}


class BudgetExceeded(RuntimeError):
    pass


class RecordedRequestFailed(RuntimeError):
    pass


def load_template(module_name: str, directory: Path):
    existing = sys.modules.get(module_name)
    if existing is not None and list(existing.__path__) != [str(directory)]:
        raise ValueError("Runtime module already points to a different directory")
    if existing is None:
        package = types.ModuleType(module_name)
        package.__path__ = [str(directory)]
        sys.modules[module_name] = package
    importlib.invalidate_caches()
    return importlib.import_module(module_name + ".operator")


def contract_matches(record: dict, problem: str, library: OperatorLibrary) -> bool:
    context = library.extractor.extract(problem)
    if any(b.get("hard_block", True) and library._boundary_match(b, context)
           for b in record.get("failure_boundaries", [])):
        return False
    return any(library._rule_match(rule, context) for rule in record.get("applicability_rules", []))


class RequestLedger:
    """One durable slot per actual API request; ambiguous requests aren't retried."""
    def __init__(self, store: RunStore):
        self.store = store
        self.path = store.root / "requests"
        self.path.mkdir(exist_ok=True)
        self.exhausted = False

    def start(self, key: str, inputs: dict) -> tuple[Path, dict | None]:
        path = self.path / (digest(key) + ".json")
        if path.exists():
            record = read_json(path)
            if record["input_sha256"] != digest(inputs):
                raise ValueError(f"Request changed on resume: {key}")
            if record["status"] == "started":
                record.update(status="failed", error="interrupted_result_unknown", usage=None)
                atomic_json(path, record)
            return path, record
        if len(list(self.path.glob("*.json"))) >= self.store.config.max_requests:
            self.exhausted = True
            raise BudgetExceeded("Frozen maximum API request count reached")
        atomic_json(path, {"key": key, "input_sha256": digest(inputs), "status": "started",
                           "model_settings": inputs["model_settings"],
                           "api_options": inputs.get("api_options", {}), "usage": None})
        return path, None

    def summary(self) -> dict:
        records = [read_json(p) for p in self.path.glob("*.json")]
        totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for record in records:
            if record.get("usage"):
                for key in totals:
                    totals[key] += record["usage"][key]
        return {
            "requests_reserved": len(records),
            "successful_requests": sum(r["status"] == "completed" for r in records),
            "requests_with_unknown_usage": sum(r.get("usage") is None for r in records),
            "reported_models": sorted({r["reported_model"] for r in records if r.get("reported_model")}),
            "system_fingerprints": sorted({r["system_fingerprint"] for r in records if r.get("system_fingerprint")}),
            **totals, "cost_usd": None,
            "cost_note": "Provider invoice required; zero/unknown configured price is not free usage.",
        }


class RecordedLLM:
    """AsyncLLM-compatible adapter with capped outputs and zero automatic retries."""
    def __init__(self, ledger: RequestLedger, model_config: Any, key: str):
        self.ledger, self.config, self.key = ledger, model_config, key
        self.index = 0
        self.client = None
        self.usage = {"total_tokens": 0, "call_count": 0, "unknown_usage_calls": 0, "request_seconds": 0.0}

    @staticmethod
    def settings(config: Any) -> dict:
        return {key: getattr(config, key) for key in ("model", "base_url", "temperature", "top_p")}

    @staticmethod
    def api_options(config: Any, thinking_mode: str) -> dict:
        if thinking_mode == "provider-default":
            return {}
        if thinking_mode not in {"disabled", "enabled"}:
            raise ValueError("Invalid thinking mode")
        if urlsplit(str(config.base_url)).hostname != "api.deepseek.com":
            raise ValueError("Explicit thinking-mode is supported only for the official DeepSeek endpoint")
        return {"extra_body": {"thinking": {"type": thinking_mode}}}

    async def __call__(self, prompt: str) -> str:
        key = f"{self.key}/call_{self.index}"
        self.index += 1
        options = self.api_options(self.config, self.ledger.store.config.thinking_mode)
        inputs = {"prompt_sha256": digest(prompt), "model_settings": self.settings(self.config),
                  "api_options": options,
                  "max_output_tokens": self.ledger.store.config.max_output_tokens}
        path, cached = self.ledger.start(key, inputs)
        if cached is None:
            started = read_json(path)
            # Preserve both baseline stages and generation context for replay.
            # Credentials remain only in the API client configuration.
            started["prompt"] = prompt
            atomic_json(path, started)
            started_at = time.monotonic()
            try:
                if self.client is None:
                    from openai import AsyncOpenAI
                    self.client = AsyncOpenAI(
                        api_key=self.config.key, base_url=self.config.base_url,
                        max_retries=0, timeout=self.ledger.store.config.timeout,
                    )
                response = await self.client.chat.completions.create(
                    model=self.config.model, messages=[{"role": "user", "content": prompt}],
                    temperature=self.config.temperature, top_p=self.config.top_p,
                    max_tokens=self.ledger.store.config.max_output_tokens,
                    **options,
                )
                usage = response.usage
                choice = response.choices[0]
                cached = {**started, "status": "completed",
                          "response": choice.message.content or "",
                          "finish_reason": getattr(choice, "finish_reason", None),
                          "reported_model": response.model,
                          "system_fingerprint": getattr(response, "system_fingerprint", None),
                          "usage": None if usage is None else {
                              "input_tokens": int(usage.prompt_tokens),
                              "output_tokens": int(usage.completion_tokens),
                              "total_tokens": int(usage.total_tokens),
                          }}
                # Exhausted output budgets or empty responses are execution
                # failures, not evidence of a stable mathematical mechanism.
                # Keep reported usage: these failures may still be billed.
                if cached["finish_reason"] == "length":
                    cached.update(status="failed", error="output_token_limit")
                elif not cached["response"].strip():
                    cached.update(status="failed", error="empty_model_response")
            except BaseException as exc:
                # Catch cancellation only to persist the uncertain outcome, then
                # propagate it. Never include credentials/provider request text.
                cached = {**started, "status": "failed", "error": type(exc).__name__, "usage": None}
                cached["elapsed_seconds"] = time.monotonic() - started_at
                atomic_json(path, cached)
                if not isinstance(exc, Exception):
                    raise
            cached["elapsed_seconds"] = time.monotonic() - started_at
            atomic_json(path, cached)
        self.usage["call_count"] += 1
        self.usage["request_seconds"] += cached.get("elapsed_seconds", 0.0)
        if cached.get("usage"):
            self.usage["total_tokens"] += cached["usage"]["total_tokens"]
        else:
            self.usage["unknown_usage_calls"] += 1
        if cached["status"] != "completed":
            raise RecordedRequestFailed(cached.get("error", "unknown_request_failure"))
        return cached["response"]

    async def call_with_format(self, prompt: str, formatter: Any) -> dict:
        raw = await self(formatter.prepare_prompt(prompt))
        valid, parsed = formatter.validate_response(raw)
        if not valid:
            raise FormatError(formatter.format_error_message())
        return parsed

    def get_usage_summary(self) -> dict:
        return dict(self.usage)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()


class LiveBackend:
    def __init__(self, store: RunStore):
        self.store = store
        self.ledger = RequestLedger(store)
        self.module = load_template(store.manifest["module_name"], store.root / "base_template")
        self.library = OperatorLibrary(store.root / "work/operator_library/operators.json", auto_create=False)
        config = LLMsConfig.default()
        self.models = {"opt": config.get(store.config.opt_model), "exec": config.get(store.config.exec_model)}
        for value in self.models.values():
            RecordedLLM.api_options(value, store.config.thinking_mode)
        settings = {key: RecordedLLM.settings(value) for key, value in self.models.items()}
        frozen = store.root / "model_settings.json"
        if frozen.exists() and read_json(frozen) != settings:
            raise ValueError("Model settings changed; do not resume a frozen run")
        if not frozen.exists():
            atomic_json(frozen, settings)

    def llm(self, key: str, model: str = "exec") -> RecordedLLM:
        return RecordedLLM(self.ledger, self.models[model], key)

    def usage(self) -> dict:
        return self.ledger.summary()

    async def generate(self, *, failure_cases: str, bottleneck: str) -> dict:
        llm = self.llm("generation", "opt")
        evolver = OperatorEvolver(str(self.store.root / "work"), llm, "MATH")
        # Only immutable generic descriptions; never pass library evidence or
        # hidden/regression rows into the generation context.
        descriptions = read_json(self.store.root / "base_template/operator.json")
        descriptions = "\n".join(f"{name}: {value.get('description', '')}"
                                 for name, value in descriptions.items() if isinstance(value, dict))
        try:
            diagnosis = await evolver.diagnose_failures(
                bottleneck_info=bottleneck, failure_cases=failure_cases,
                graph_code=inspect.getsource(GenericTwoStageSolver),
            )
            designs = await evolver.design_candidates(
                diagnosis=diagnosis, operator_descriptions=descriptions,
                candidate_count=self.store.config.candidate_count, design_policy=PROMPT_POLICY,
            )
            return {"diagnosis": evolver._model_dict(diagnosis),
                    "designs": [evolver._model_dict(design) for design in designs]}
        finally:
            await llm.close()

    async def generate_reviewed(self, *, context: dict) -> dict:
        """Design directly from evidence-bound approval; do not rediagnose it."""
        import json
        from scripts.independent_mechanism_evidence import authorization, generation_context
        from scripts.optimizer_utils.operator_evolution import FailureDiagnosis

        approved = authorization(self.store)
        if context != generation_context(self.store, approved):
            raise ValueError("Generation context differs from reviewed evidence")
        h = context["hypothesis"]
        diagnosis = FailureDiagnosis(
            dominant_error_type=context["scope"]["mechanism_id"],
            root_cause=("Reviewed hypothesis (not causal proof): " + h["claim"]
                        + "\nViolated constraint: " + h["violated_constraint"]),
            missing_capability=h["expected_intervention"],
            evidence=json.dumps(context["evidence"], ensure_ascii=False),
            applicable_when=h["applicable_when"], not_applicable_when=h["not_applicable_when"],
            success_criteria=h["success_criteria"],
        )
        llm = self.llm("reviewed_generation", "opt")
        evolver = OperatorEvolver(str(self.store.root / "work"), llm, "MATH")
        descriptions = read_json(self.store.root / "base_template/operator.json")
        descriptions = "\n".join(f"{name}: {value.get('description', '')}"
                                 for name, value in descriptions.items() if isinstance(value, dict))
        try:
            designs = await evolver.design_candidates(
                diagnosis=diagnosis, operator_descriptions=descriptions,
                candidate_count=self.store.config.candidate_count,
                design_policy={**PROMPT_POLICY, "frozen_input_scope": context["scope"],
                               "review_authorization_sha256": context["authorization_sha256"],
                               "scope_enforcement": "Compiler enforces the same versioned input selector used by prepare"},
            )
            return {"diagnosis": evolver._model_dict(diagnosis),
                    "designs": [evolver._model_dict(design) for design in designs],
                    "review_authorization_sha256": context["authorization_sha256"]}
        finally:
            await llm.close()

    def factory(self, name: str) -> type:
        record = self.library.get(name)
        if not record or record["status"] != "candidate":
            raise ValueError(f"Not an archived candidate: {name}")
        artifact = record["artifact"]
        code, prompts = artifact["class_code"], artifact["prompt_code"]
        if combined_artifact_sha256(code, prompts) != artifact["artifact_sha256"]:
            raise ValueError(f"Candidate artifact changed: {name}")
        namespace = dict(vars(self.module))
        exec(compile(prompts, f"<{name}:prompts>", "exec"), namespace)
        exec(compile(code, f"<{name}:class>", "exec"), namespace)
        return namespace[name]

    async def execute(self, *, item: dict, operator: str | None, regression: bool, key: str) -> dict:
        """Execute first, score afterwards. Only the problem reaches the solver."""
        llm = self.llm(key)
        baseline = GenericTwoStageSolver(llm, self.module.__name__)
        started = time.monotonic()
        candidate_error = ""
        tool_trace = {}
        declined = False
        selected = None
        used_fallback = operator is None

        async def solve() -> dict:
            nonlocal candidate_error, declined, selected, used_fallback, tool_trace
            if operator is None:
                return await baseline(item["problem"])
            record = self.library.get(operator)
            if not contract_matches(record, item["problem"], self.library):
                result = {"status": "declined", "response": "", "observation": "outside_contract"}
            else:
                try:
                    result = await self.factory(operator)(llm)(problem=item["problem"])
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    result = {"status": "error", "response": "", "observation": type(exc).__name__}
            tool_trace = result.get("tool_trace", {})
            declined = result.get("status") == "declined"
            if result.get("status") == "error" or (not declined and not result.get("response")):
                candidate_error = str(result.get("observation") or "empty_response")
            if regression and (declined or candidate_error):
                used_fallback = True
                return await baseline(item["problem"])
            if not declined and not candidate_error:
                selected = operator
            return result

        try:
            try:
                result = await asyncio.wait_for(solve(), timeout=self.store.config.timeout)
                prediction = str(result.get("response", "") or "")
                error = str(result.get("observation", "")) if result.get("status") == "error" else ""
                if result.get("status") != "declined" and not prediction:
                    error = error or "empty_response"
            except BudgetExceeded:
                raise
            except Exception as exc:
                prediction, error = "", type(exc).__name__
            if self.ledger.exhausted:
                raise BudgetExceeded("Frozen maximum API request count reached")
            scorer = MATHBenchmark("MATH", "", "")
            if error or not prediction:
                score, extracted = 0, ""
            else:
                score, extracted = scorer.calculate_score(
                    item["solution"], prediction, problem=item["problem"]
                )
            usage = llm.get_usage_summary()
            return {"score": int(score), "prediction": prediction, "extracted": extracted,
                    "error": error, "candidate_error": candidate_error, "declined": declined,
                    "selected_operator": selected, "used_fallback": used_fallback,
                    "tokens": usage["total_tokens"], "calls": usage["call_count"],
                    "tool_trace": tool_trace,
                    "unknown_usage_calls": usage["unknown_usage_calls"],
                    "elapsed_seconds": max(time.monotonic() - started, usage["request_seconds"])}
        finally:
            await llm.close()
