"""Train-only offline automation: discover -> generate -> select -> gate -> release.

Usage: python -m scripts.offline_operator_loop {prepare,run,status} --help
The live run is opt-in. A rejected candidate is a completed scientific outcome,
not permission to search against the same hidden gate again.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import random
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.contextual_operator_router import ContextAwareOperatorRouter
from scripts.offline_loop_protocol import (
    ROLES, TEMPLATE_FILES, LoopConfig, RunStore, atomic_json, digest,
    file_hash, prepare_run, read_json, run_lock,
)
from scripts.offline_loop_runtime import BudgetExceeded, LiveBackend, PROMPT_POLICY, RequestLedger, load_template
from scripts.operator_library import OperatorLibrary, ProblemFeatureExtractor
from scripts.operator_tournament import PromotionPolicy, QualityWeights, calculate_operator_quality
from scripts.optimizer_utils.operator_evolution import OperatorEvolver, OperatorEvolveResponse


def feature_signature(problem: str) -> tuple[str, ...]:
    return tuple(sorted(ProblemFeatureExtractor().extract(problem).features))


def select_mechanism(rows: list[dict], observations: list[dict], config: LoopConfig) -> dict:
    """Transparent repeatability screen. Does not claim causal identification."""
    by_id: dict[str, list[dict]] = {}
    for observation in observations:
        by_id.setdefault(observation["problem_id"], []).append(observation)
    groups: dict[tuple[str, ...], list[dict]] = {}
    stable = []
    errors = 0
    for item in rows:
        outputs = by_id[item["problem_id"]]
        valid = [o for o in outputs if not o["error"]]
        errors += len(outputs) - len(valid)
        wrong = sum(o["score"] == 0 for o in valid)
        entry = {"problem_id": item["problem_id"], "valid_observations": len(valid),
                 "wrong_observations": wrong, "stable": wrong >= config.min_wrong_repeats}
        groups.setdefault(feature_signature(item["problem"]), []).append(entry)
        if entry["stable"]:
            stable.append(item["problem_id"])
    candidates = []
    for signature, items in groups.items():
        valid_n = sum(item["valid_observations"] for item in items)
        wrong_n = sum(item["wrong_observations"] for item in items)
        stable_ids = [item["problem_id"] for item in items if item["stable"]]
        rate = wrong_n / valid_n if valid_n else 0.0
        candidates.append({
            "features": list(signature), "problems": len(items),
            "valid_observations": valid_n, "wrong_observations": wrong_n,
            "observed_error_rate": rate, "stable_problem_ids": stable_ids,
            "eligible": bool(signature) and len(stable_ids) >= config.min_stable_problems
                        and rate >= config.min_error_rate,
        })
    candidates.sort(key=lambda g: (-len(g["stable_problem_ids"]), -g["observed_error_rate"], g["features"]))
    eligible = [group for group in candidates if group["eligible"]]
    return {"selected": eligible[0] if eligible else None, "groups": candidates,
            "distinct_problems": len(rows), "observations": len(observations),
            "execution_errors_excluded": errors, "stable_problem_count": len(stable),
            "interpretation": "repeatable errors under frozen settings; mechanism hypothesis only",
            "scorer_audit": "automatic nonempty-reference checks only; no human/independent scorer audit"}


def candidate_fingerprint(record: dict) -> str:
    if not record:
        raise ValueError("Archived candidate changed or disappeared")
    return digest({key: record.get(key) for key in (
        "name", "class_name", "version", "skeleton_type", "artifact",
        "applicability_rules", "failure_boundaries", "description",
    )})


def load_released_router(run_dir: str | Path, llm: Any) -> ContextAwareOperatorRouter:
    """Load a completed isolated release in a fresh Python process, without API calls."""
    root = Path(run_dir).resolve()
    store = RunStore(root)
    summary = store.get("summary", digest(store.manifest))
    if not summary or not summary.get("promoted"):
        raise ValueError("This run has no completed promoted release")
    publication = summary["publication"]
    for relative, expected in publication["release_hashes"].items():
        if file_hash(root / relative) != expected:
            raise ValueError(f"Released artifact changed: {relative}")
    module_name = publication["operator_module"].removesuffix(".operator")
    load_template(module_name, Path(publication["template_dir"]))
    return ContextAwareOperatorRouter(llm, publication["library_path"], publication["operator_module"])


def register_candidates(store: RunStore, plan: dict) -> list[str]:
    reviewed = None
    if store.manifest.get("pipeline") == "aflow_independent_mechanism_evidence_v1":
        from scripts.independent_mechanism_evidence import authorization
        reviewed = authorization(store)
        if plan.get("review_authorization_sha256") != digest(reviewed):
            raise ValueError("Candidate plan is not bound to the semantic authorization")
    library = OperatorLibrary(store.root / "work/operator_library/operators.json", auto_create=False)
    compiler = OperatorEvolver.__new__(OperatorEvolver)
    designs = [OperatorEvolveResponse(**raw) for raw in plan["designs"]]
    base_names = set(read_json(store.root / "inputs/base_library.json")["operators"])
    if len(designs) < 2 or len({d.operator_name for d in designs}) != len(designs):
        raise ValueError("Need at least two distinct valid candidate designs")
    prepared = []
    for design in designs:
        if design.operator_name in base_names:
            raise ValueError("A new candidate cannot overwrite an existing operator")
        for valid, reason in (compiler._validate_response(design),
                              compiler._validate_design_policy(design, PROMPT_POLICY)):
            if not valid:
                raise ValueError(f"Invalid candidate {design.operator_name}: {reason}")
        code, prompts = compiler._generate_class_code(design), compiler._generate_prompt_code(design)
        if reviewed is not None:
            scope = store.manifest["protocol"]["scope"]
            marker = "        refusal = self._preflight(problem)"
            if code.count(marker) != 1:
                raise ValueError("Expected one fixed-skeleton preflight guard")
            guard = ("        from scripts.mechanism_scope import matches as matches_frozen_scope\n"
                     f"        if not matches_frozen_scope({scope!r}, problem):\n"
                     "            return {'status': 'declined', 'response': '', 'observation': 'outside_frozen_mechanism_scope'}\n")
            code = code.replace(marker, guard + marker)
        compile(code, f"<{design.operator_name}>", "exec")
        compile(prompts, f"<{design.operator_name}:prompts>", "exec")
        prepared.append((design, code, prompts))
    records = []
    for design, code, prompts in prepared:
        record = library.register_evolved_candidate(
            name=design.operator_name, description=design.description,
            module=store.manifest["module_name"] + ".operator", skeleton_type=design.skeleton_type,
            applicability_features=design.applicability_features,
            failure_features=design.failure_boundary_features,
            provenance={"kind": "offline_fixed_skeleton", "diagnosis": plan["diagnosis"],
                        "design": compiler._model_dict(design),
                        "visible_roles": [store.manifest.get("evidence_mode", "independent_evidence")] if reviewed else ["discovery"],
                        **({"review_authorization_sha256": digest(reviewed),
                            "review_subject_sha256": reviewed["subject_sha256"],
                            "review_sha256": reviewed["review_sha256"],
                            "frozen_scope": store.manifest["protocol"]["scope"],
                            "source_manifest_sha256": digest(store.manifest),
                            "promotion_authorized": False} if reviewed else {})},
            class_code=code, prompt_code=prompts,
        )
        records.append({"name": record["name"], "fingerprint": candidate_fingerprint(record)})
    store.put("candidates", plan, records)
    return [record["name"] for record in records]


class OfflineLoop:
    def __init__(self, store: RunStore, backend: Any):
        self.store, self.backend = store, backend
        self.config = store.config
        self.library = OperatorLibrary(store.root / "work/operator_library/operators.json", auto_create=False)
        self.policy = PromotionPolicy(**store.manifest["promotion_policy"])
        self.weights = QualityWeights(**store.manifest["quality_weights"])

    async def execute(self, *, item: dict, stage: str, repeat: int,
                      operator: str | None = None, regression: bool = False) -> dict:
        key = f"{stage}/{operator or 'baseline'}/{item['problem_id']}/{repeat}"
        inputs = {"item": item, "operator": operator, "regression": regression,
                  "fingerprint": candidate_fingerprint(self.library.get(operator)) if operator else None}
        result = self.store.get(key, inputs)
        if result is None:
            result = await self.backend.execute(item=item, operator=operator, regression=regression, key=key)
            self.store.put(key, inputs, result)
        return {**result, "problem_id": item["problem_id"], "run": repeat}

    async def discover(self) -> tuple[dict, list[dict]]:
        rows = self.store.rows("discovery")
        observations = []
        for repeat in range(1, self.config.discovery_runs + 1):
            for item in rows:
                observations.append(await self.execute(item=item, stage="discovery", repeat=repeat))
        screen = select_mechanism(rows, observations, self.config)
        self.store.put("screen", {"rows": rows, "observations": observations}, screen)
        return screen, observations

    async def generate(self, screen: dict, observations: list[dict]) -> list[str]:
        ids = set(screen["selected"]["stable_problem_ids"][:12])
        context = []
        for item in self.store.rows("discovery"):
            if item["problem_id"] not in ids:
                continue
            failed = [o for o in observations if o["problem_id"] == item["problem_id"]
                      and not o["error"] and o["score"] == 0]
            context.append({"problem_id": item["problem_id"], "problem": item["problem"],
                            "reference_solution": item["solution"],
                            "wrong_repeats": len(failed),
                            "failed_outputs": [o["prediction"][:2000] for o in failed]})
        inputs = {"failure_cases": json.dumps(context, ensure_ascii=False),
                  "bottleneck": json.dumps({
                      "feature_group": screen["selected"]["features"],
                      "status": "repeatable failure hypothesis, not established cause",
                      "request": "Propose a reusable, narrow capability with explicit refusal boundaries",
                  }, ensure_ascii=False)}
        plan = self.store.get("generation", inputs)
        if plan is None:
            plan = await self.backend.generate(**inputs)
            self.store.put("generation", inputs, plan)
        saved = self.store.get("candidates", plan)
        if saved is not None:
            for record in saved:
                if candidate_fingerprint(self.library.get(record["name"])) != record["fingerprint"]:
                    raise ValueError("Archived candidate changed on resume")
            return [record["name"] for record in saved]
        return register_candidates(self.store, plan)

    def split_scope(self, rows: list[dict], screen: dict) -> tuple[list[dict], list[dict]]:
        signature = tuple(screen["selected"]["features"])
        return ([r for r in rows if feature_signature(r["problem"]) == signature],
                [r for r in rows if feature_signature(r["problem"]) != signature])

    def baseline_is_infrastructure(self, row):
        return bool(row["baseline_error"])

    async def compare(self, name: str, targets: list[dict], regressions: list[dict], stage: str) -> dict:
        inputs = {"operator": name, "fingerprint": candidate_fingerprint(self.library.get(name)),
                  "targets": targets, "regressions": regressions}
        cached = self.store.get(f"{stage}_metrics/{name}", inputs)
        if cached is not None:
            return cached
        details = []
        for repeat in range(1, self.config.evaluation_runs + 1):
            items = [(r, "target") for r in targets] + [(r, "regression") for r in regressions]
            random.Random(f"{self.config.seed}/{stage}/{repeat}").shuffle(items)
            for item, split in items:
                arms = [None, name]
                random.Random(f"{self.config.seed}/{stage}/{repeat}/{item['problem_id']}").shuffle(arms)
                pair = {}
                for arm in arms:
                    pair[arm] = await self.execute(
                        item=item, stage=stage, repeat=repeat, operator=arm,
                        regression=split == "regression" if arm else False,
                    )
                base, candidate = pair[None], pair[name]
                details.append({
                    "run": repeat, "problem_id": item["problem_id"], "split": split,
                    "baseline_score": base["score"], "candidate_score": candidate["score"],
                    # Use actual fallback execution, never impute baseline correctness.
                    "effective_candidate_score": candidate["score"],
                    "baseline_tokens": base.get("tokens", 0), "candidate_tokens": candidate.get("tokens", 0),
                    "declined": candidate["declined"],
                    "error": candidate["error"] or candidate.get("candidate_error", ""),
                    "baseline_error": base["error"], "selected_operator": candidate.get("selected_operator"),
                    "used_fallback": candidate.get("used_fallback", False),
                    "baseline": base, "candidate": candidate,
                })
        target_rows = [r for r in details if r["split"] == "target"]
        regression_rows = [r for r in details if r["split"] == "regression"]
        metrics = calculate_operator_quality(target_rows, regression_rows, weights=self.weights, policy=self.policy)
        for label, selected_rows in (("target", target_rows), ("regression", regression_rows)):
            baseline_correct = sum(r["baseline_score"] for r in selected_rows)
            candidate_correct = sum(r["candidate_score"] for r in selected_rows)
            metrics[label + "_baseline_correct"] = baseline_correct
            metrics[label + "_candidate_correct"] = candidate_correct
            metrics[label + "_net_gain_percentage_points"] = (
                100 * (candidate_correct - baseline_correct) / len(selected_rows) if selected_rows else None
            )
        metrics["reported_baseline_tokens"] = sum(r["baseline"].get("tokens", 0) for r in details)
        metrics["reported_candidate_tokens"] = sum(r["candidate"].get("tokens", 0) for r in details)
        clean_wins = {r["problem_id"] for r in target_rows if not self.baseline_is_infrastructure(r) and not r["error"]
                      and r["candidate_score"] > r["baseline_score"] and r["selected_operator"] == name}
        # Timeout wins remain reported, but cannot alone establish a reusable capability.
        metrics["clean_unique_winning_problems"] = len(clean_wins)
        if len(clean_wins) < self.policy.min_unique_winning_problems:
            metrics["gate_failures"].append("insufficient distinct non-infrastructure specialist wins")
        if len(regressions) < self.config.min_regression_problems:
            metrics["gate_failures"].append("insufficient distinct non-specialist regression problems")
        metrics["passed"] = not metrics["gate_failures"]
        result = {"operator_name": name, "metrics": metrics, "rows": details}
        return self.store.put(f"{stage}_metrics/{name}", inputs, result)

    def frozen_confirmation(self, winner: str, screen: dict) -> dict:
        inputs = {"winner": winner, "record_fingerprint": candidate_fingerprint(self.library.get(winner)),
                  "features": screen["selected"]["features"]}
        frozen = self.store.get("confirmation_freeze", inputs)
        if frozen is not None:
            return frozen
        targets, _ = self.split_scope(self.store.rows("gate"), screen)
        _, regressions = self.split_scope(self.store.rows("regression"), screen)
        frozen = {**inputs, "target_ids": [r["problem_id"] for r in targets],
                  "regression_ids": [r["problem_id"] for r in regressions],
                  "gate_pool_count": len(self.store.rows("gate")),
                  "regression_pool_count": len(self.store.rows("regression")),
                  "candidate_record": self.library.get(winner),
                  "policy": asdict(self.policy), "selection_uses": "problem features only, before scoring"}
        return self.store.put("confirmation_freeze", inputs, frozen)

    def publish(self, winner: str, gate: dict, frozen: dict) -> dict:
        inputs = {"winner": winner, "gate_sha256": digest(gate), "freeze_sha256": digest(frozen)}
        cached = self.store.get("publication", inputs)
        if cached is not None:
            self.verify_release(cached)
            return cached
        if candidate_fingerprint(self.library.get(winner)) != frozen["record_fingerprint"]:
            raise ValueError("Candidate changed after the confirmation freeze")
        release = self.store.root / "release"
        template = release / "template"
        template.mkdir(parents=True, exist_ok=True)
        module_name = self.store.manifest["module_name"] + "_release"
        for name in TEMPLATE_FILES:
            source = (self.store.root / "base_template" / name).read_text(encoding="utf-8")
            (template / name).write_text(source.replace(self.store.manifest["module_name"], module_name), encoding="utf-8")
        # Copy original active operators and archive the unsuccessful new candidates.
        payload = copy.deepcopy(self.library.load())
        base_names = set(read_json(self.store.root / "inputs/base_library.json")["operators"])
        for name, record in payload["operators"].items():
            record["module"] = module_name + ".operator"
            if name not in base_names:
                record["status"] = "candidate" if name == winner else "rejected"
                record["status_reason"] = "offline design selection"
        library_path = release / "operator_library/operators.json"
        atomic_json(library_path, payload)
        library = OperatorLibrary(library_path, auto_create=False)
        record = library.get(winner)
        for rule in record["applicability_rules"]:
            scoped = [r for r in gate["rows"] if r["split"] == "target"]
            # Current compiler emits one rule. Check scope rather than relying on it.
            problem_map = {r["problem_id"]: r for r in self.store.rows("gate")}
            scoped = [r for r in scoped if library._rule_match(rule, library.extractor.extract(problem_map[r["problem_id"]]["problem"]))]
            wins = [r for r in scoped if r["candidate_score"] > r["baseline_score"]]
            losses = [r for r in scoped if r["candidate_score"] < r["baseline_score"]]
            library.record_evidence(winner, {
                "id": digest({**inputs, "scope": rule["name"]}),
                "source": self.store.manifest.get("confirmation_evidence_source", "offline_independent_confirmation"), "scope": rule["name"],
                "baseline": "generic_two_stage", "protocol": "paired_correct_within_timeout_offline_gate_v1",
                "routing_eligible": True, "wins": len(wins), "losses": len(losses),
                "ties": len(scoped) - len(wins) - len(losses),
                "unique_problems": len({r["problem_id"] for r in scoped}),
                "unique_winning_problems": len({r["problem_id"] for r in wins}),
                "gate_sha256": digest(gate), "gate_failures": [],
                **({"reviewed_handoff": self.store.manifest["source_evidence"],
                    "frozen_scope": self.store.manifest["scope"],
                    "evaluation_mode": self.store.manifest["evaluation_mode"]}
                   if self.store.manifest.get("pipeline") in {"aflow_reviewed_continuation_v1", "aflow_operational_replay_v1"} else {}),
            })
        library.install_artifact(winner, template)
        library.set_status(winner, "experimental", "Passed single-candidate " + self.store.manifest.get("evaluation_mode", "independent") + " offline gate")
        load_template(module_name, template)
        router = ContextAwareOperatorRouter(None, str(library_path), module_name + ".operator")
        # Real router and artifact resolver; no model call needed to check reachability.
        probes = []
        winning_ids = {r["problem_id"] for r in gate["rows"] if r["split"] == "target"
                       and not self.baseline_is_infrastructure(r) and not r["error"]
                       and r["candidate_score"] > r["baseline_score"]}
        reachable_wins = set()
        for item in self.store.rows("gate"):
            if item["problem_id"] not in frozen["target_ids"]:
                continue
            decision = router.decide(item["problem"])
            selected = decision.selected.operator_name if decision.selected else None
            if selected == winner:
                router._resolve(winner)  # verifies installed class/prompt/version hashes
                if item["problem_id"] in winning_ids:
                    reachable_wins.add(item["problem_id"])
            probes.append({"problem_id": item["problem_id"], "selected_operator": selected})
        promoted = len(reachable_wins) >= self.policy.min_unique_winning_problems
        if not promoted:
            library.set_status(winner, "rejected", "Independent capability passed, but router did not reach enough distinct winning tasks")
        result = {"promoted": promoted, "operator": winner,
                  "library_path": str(library_path), "operator_module": module_name + ".operator",
                  "template_dir": str(template), "routing_probes": probes,
                  "reachable_unique_wins": len(reachable_wins),
                  "status": "promoted" if promoted else "archived_route_not_selectable"}
        result["release_hashes"] = {str(path.relative_to(self.store.root)): file_hash(path)
                                    for path in [library_path, *(template / name for name in TEMPLATE_FILES)]}
        atomic_json(release / "manifest.json", result)
        return self.store.put("publication", inputs, result)

    def verify_release(self, publication: dict) -> None:
        for relative, expected in publication.get("release_hashes", {}).items():
            if file_hash(self.store.root / relative) != expected:
                raise ValueError(f"Released artifact changed: {relative}")

    def finish(self, status: str, **fields: Any) -> dict:
        result = {"status": status, "promoted": status == "promoted", **fields,
                  "usage": self.backend.usage(),
                  "generation_backend": self.store.manifest["generation_backend"],
                  "scientific_scope": self.store.manifest.get("scientific_scope", "offline discovery/development/confirmation; NOT a final natural-MATH evaluation"),
                  "original_system_modified": False,
                  "automation_boundary": "user supplies train data/protocol; feature clusters and fixed-skeleton generation; scorer audit not automated"}
        self.store.put("summary", digest(self.store.manifest), result)
        atomic_json(self.store.root / "summary.json", result)
        self.store.status(status, promoted=result["promoted"])
        return result

    async def run(self) -> dict:
        cached = self.store.get("summary", digest(self.store.manifest))
        if cached is not None:
            if cached.get("publication"):
                self.verify_release(cached["publication"])
            return cached
        self.store.status("discovery")
        screen, observations = await self.discover()
        if screen["selected"] is None:
            return self.finish("archived_no_stable_mechanism", screen=screen)
        self.store.status("generation")
        try:
            candidates = await self.generate(screen, observations)
        except BudgetExceeded:
            raise
        except (RuntimeError, ValueError) as exc:
            # A drift/integrity failure must not silently become a scientific rejection.
            if isinstance(exc, ValueError) and ("changed" in str(exc) or "drift" in str(exc)):
                raise
            return self.finish("archived_generation_failed", screen=screen, reason=str(exc))
        self.store.status("design")
        targets, regressions = self.split_scope(self.store.rows("design"), screen)
        if len(targets) < self.policy.min_unique_target_problems or len(regressions) < self.config.min_regression_problems:
            return self.finish("archived_insufficient_design_data", screen=screen, candidate_names=candidates,
                               target_count=len(targets), regression_count=len(regressions))
        ranking = [await self.compare(name, targets, regressions, "design") for name in candidates]
        ranking.sort(key=lambda item: (not item["metrics"]["passed"], -item["metrics"]["quality_score"], item["operator_name"]))
        self.store.put("design_ranking", candidates, ranking)
        if not ranking[0]["metrics"]["passed"]:
            return self.finish("archived_design_failed", screen=screen,
                               design_ranking=[{"operator_name": r["operator_name"], "metrics": r["metrics"]} for r in ranking])
        winner = ranking[0]["operator_name"]
        frozen = self.frozen_confirmation(winner, screen)
        if (len(frozen["target_ids"]) < self.policy.min_unique_target_problems
                or len(frozen["regression_ids"]) < self.config.min_regression_problems):
            return self.finish("archived_insufficient_hidden_data", winner=winner, confirmation=frozen)
        self.store.status("hidden_confirmation", winner=winner)
        targets = [r for r in self.store.rows("gate") if r["problem_id"] in frozen["target_ids"]]
        regressions = [r for r in self.store.rows("regression") if r["problem_id"] in frozen["regression_ids"]]
        gate = await self.compare(winner, targets, regressions, "gate")
        if not gate["metrics"]["passed"]:
            return self.finish("archived_hidden_failed", winner=winner, gate_metrics=gate["metrics"],
                               hidden_reuse_forbidden=True)
        self.store.status("publication", winner=winner)
        publication = self.publish(winner, gate, frozen)
        return self.finish(publication["status"], winner=winner, gate_metrics=gate["metrics"], publication=publication)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Freeze independent training pools; no API calls")
    prepare.add_argument("--output-dir", required=True, type=Path)
    prepare.add_argument("--data", required=True, type=Path)
    prepare.add_argument("--source-name", required=True, help="Provenance of the independent training data")
    prepare.add_argument("--source-split", required=True, choices=["train"])
    prepare.add_argument("--exclude-data", type=Path, action="append", default=[])
    for role, default in zip(ROLES, (400, 200, 200, 200)):
        prepare.add_argument(f"--{role}-count", type=int, default=default)
    defaults = LoopConfig()
    for name in ("seed", "discovery_runs", "evaluation_runs", "candidate_count", "min_stable_problems",
                 "min_wrong_repeats", "min_regression_problems", "max_requests", "max_output_tokens"):
        prepare.add_argument("--" + name.replace("_", "-"), type=int, default=getattr(defaults, name))
    for name in ("timeout", "min_error_rate"):
        prepare.add_argument("--" + name.replace("_", "-"), type=float, default=getattr(defaults, name))
    prepare.add_argument("--opt-model", required=True)
    prepare.add_argument("--exec-model", required=True)
    prepare.add_argument("--thinking-mode", choices=["provider-default", "disabled", "enabled"],
                         default=defaults.thinking_mode,
                         help="Freeze DeepSeek thinking mode; explicit modes require its official endpoint")
    for name in ("run", "status"):
        command = commands.add_parser(name)
        command.add_argument("--output-dir", required=True, type=Path)
        if name == "run":
            command.add_argument("--allow-api", action="store_true", help="Explicitly permit paid API requests")
    return result


def main() -> None:
    args = parser().parse_args()
    root = args.output_dir.resolve()
    if args.command == "prepare":
        config = LoopConfig(**{name: getattr(args, name) for name in LoopConfig.__dataclass_fields__})
        # Resolve local names before reserving data. No API client or request.
        from scripts.async_llm import LLMsConfig
        from scripts.offline_loop_runtime import RecordedLLM
        configured = LLMsConfig.default()
        for name in (config.opt_model, config.exec_model):
            RecordedLLM.api_options(configured.get(name), config.thinking_mode)
        manifest = prepare_run(root=root, data=args.data, source_name=args.source_name,
                               counts={role: getattr(args, role + "_count") for role in ROLES},
                               config=config, exclude_paths=args.exclude_data)
        print(f"Prepared: {root / 'manifest.json'}")
        print(f"Pools: {manifest['counts']}; API requests made: 0")
        print(f"Frozen request cap: {config.max_requests}; max output tokens/request: {config.max_output_tokens}")
        print(f"Conservative all-stage request upper bound: {manifest['request_upper_bound']}")
        if config.max_requests < manifest["request_upper_bound"]:
            print("The cap may stop this run before all stages finish; review the protocol before run.")
        return
    if not root.is_dir():
        raise SystemExit("Run directory does not exist; use prepare first")
    if args.command == "status":
        # Atomic snapshots allow inspecting progress while a writer owns the lock.
        store = RunStore(root)
        state = read_json(root / "state.json")
        if (root / "requests").is_dir():
            state["usage"] = RequestLedger(store).summary()
        print(json.dumps(state, ensure_ascii=False, indent=2))
        if (root / "summary.json").exists():
            print(f"Summary: {root / 'summary.json'}")
        return
    with run_lock(root):
        if not args.allow_api:
            raise SystemExit("run requires --allow-api; prepare/status never call the model")
        store = RunStore(root, progress=lambda status: print(f"[offline] {status}", flush=True))
        # Completed runs are read-only and don't even load model credentials.
        done = store.get("summary", digest(store.manifest))
        if done is not None:
            for relative, expected in done.get("publication", {}).get("release_hashes", {}).items():
                if file_hash(root / relative) != expected:
                    raise ValueError(f"Released artifact changed: {relative}")
            print(f"Already complete: {done['status']}; no requests made")
            return
        backend = LiveBackend(store)
        try:
            summary = asyncio.run(OfflineLoop(store, backend).run())
        except BudgetExceeded as exc:
            store.status("budget_exhausted", reason=str(exc), usage=backend.usage())
            raise SystemExit(str(exc)) from exc
        except KeyboardInterrupt:
            store.status("interrupted", usage=backend.usage())
            raise SystemExit("Interrupted; repeat the same run command to resume saved work")
        except Exception as exc:
            store.status("blocked", error_type=type(exc).__name__)
            raise
        print(f"Offline loop: {summary['status']}; promoted={summary['promoted']}")
        print(f"Summary: {root / 'summary.json'}")
        print("Original library and workflow template were not modified.")


if __name__ == "__main__":
    main()
