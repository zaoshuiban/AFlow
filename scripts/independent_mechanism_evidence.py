"""Frozen independent baseline evidence -> semantic review -> candidate generation.

prepare/import-diagnosis/review are offline. run/diagnose/generate require
--allow-api. Candidates are isolated and never promoted by this entry point.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.failure_mechanism_induction import POLICY, build_packet, validate_response
from scripts.mechanism_scope import matches, select_pools
from scripts.mechanism_review import evaluate_review, review_subject, review_template, validate_protocol
from scripts.offline_loop_protocol import (
    REPO_ROOT, TEMPLATE_FILES, LoopConfig, RunStore, atomic_json, code_fingerprints,
    digest, file_hash, load_rows, read_json, run_lock, runtime_environment,
)

PIPELINE = "aflow_independent_mechanism_evidence_v1"


def now():
    return datetime.now(timezone.utc).isoformat()


def request_hashes(store, prefix):
    return {str(p.relative_to(store.root)): file_hash(p)
            for p in sorted((store.root / "requests").glob("*.json"))
            if read_json(p)["key"].startswith(prefix)}


def verify_request_hashes(store, frozen):
    for name, expected in frozen.items():
        if file_hash(store.root / name) != expected:
            raise ValueError(f"Frozen request evidence changed: {name}")


def model_settings(config):
    from scripts.async_llm import LLMsConfig
    from scripts.offline_loop_runtime import RecordedLLM
    configured = LLMsConfig.default()
    result = {}
    for role, name in (("opt", config.opt_model), ("exec", config.exec_model)):
        model = configured.get(name)
        RecordedLLM.api_options(model, config.thinking_mode)
        result[role] = RecordedLLM.settings(model)
    return result


def reserved_pools(parent):
    """Known historical reservations. No prior answer/checkpoint is read."""
    manifests = set()
    for directory in (parent, REPO_ROOT / "workspace/MATH/offline_runs",
                      REPO_ROOT / "workspace/MATH/evidence_runs"):
        if directory.is_dir():
            manifests.update(directory.glob("*/manifest.json"))
    for path in sorted(manifests):
        manifest = read_json(path)
        if manifest.get("pipeline") in {"aflow_offline_loop_v1", "aflow_reviewed_continuation_v1", "aflow_operational_replay_v1"}:
            names = [f"inputs/{role}.json" for role in ("discovery", "design", "gate", "regression")]
        elif manifest.get("pipeline") == PIPELINE:
            names = ["inputs/samples.json"]
        else:
            continue
        for name in names:
            pool = path.parent / name
            if file_hash(pool) != manifest["input_hashes"][name]:
                raise ValueError(f"Historical reserved pool changed: {pool}")
            yield pool, read_json(pool)


def prepare(*, root, data, protocol, source_name, config, target_count, control_count,
            exclude_paths=(), template=None, evidence_mode="independent_natural"):
    from scripts.operator_library import OperatorLibrary, stable_problem_id
    root, data = root.resolve(), data.resolve()
    if root.exists():
        raise FileExistsError(root)
    if evidence_mode not in {"independent_natural", "historical_replay"}:
        raise ValueError("Unknown evidence mode")
    validate_protocol(protocol)
    config.validate()
    if not source_name.strip():
        raise ValueError("Training source declaration is required")
    if any(type(n) is not int or n <= 0 for n in (target_count, control_count)):
        raise ValueError("Target and control counts must be positive")
    if target_count < config.min_stable_problems:
        raise ValueError("Target count is below the frozen minimum support")
    settings = model_settings(config)  # Local names/options only; no request.
    excluded_ids, excluded_families, exclusions = set(), set(), []

    def families(row):
        return {(key, str(row[key])) for key in ("source_id", "family_id") if row.get(key)}

    def exclude(path, rows):
        excluded_ids.update(stable_problem_id(row["problem"]) for row in rows)
        for row in rows:
            excluded_families.update(families(row))
        exclusions.append({"path": str(path.resolve()), "sha256": file_hash(path), "rows": len(rows)})

    paths = set(Path(p).resolve() for p in exclude_paths)
    if evidence_mode == "independent_natural":
        paths.update(p for p in (REPO_ROOT / "data/datasets/math_validate.jsonl",
                                REPO_ROOT / "data/datasets/math_test.jsonl") if p.exists())
    for path in sorted(paths):
        if path == data:
            raise ValueError("Training source is an excluded dataset")
        exclude(path, load_rows(path))
    if evidence_mode == "independent_natural":
        for path, rows in reserved_pools(root.parent):
            exclude(path, rows)
    source_hash = file_hash(data)
    source = load_rows(data, training=evidence_mode == "independent_natural")
    unique, family_owner = {}, {}
    excluded = 0
    for row in source:
        pid = row["problem_id"]
        if pid in excluded_ids or families(row) & excluded_families:
            excluded += 1
            continue
        if pid in unique:
            if row["solution"].strip() != unique[pid]["solution"].strip():
                raise ValueError("Conflicting reference answers")
            continue
        # Conservatively reject variants rather than silently splitting a family.
        for family in families(row):
            if family in family_owner and family_owner[family] != pid:
                raise ValueError("Multiple variants share source_id/family_id; supply family-disjoint data")
            family_owner[family] = pid
        unique[pid] = row
    candidates = sorted(unique.values(), key=lambda r: r["problem_id"])
    random.Random(config.seed).shuffle(candidates)
    targets, controls, selection = select_pools(protocol, candidates, target_count, control_count)
    samples = [{**r, "evidence_role": role} for role, pool in
               (("target", targets[:target_count]), ("control", controls[:control_count])) for r in pool]
    template = template or REPO_ROOT / "workspace/MATH/workflows/template"
    sources = {name: (template / name).read_text(encoding="utf-8") for name in TEMPLATE_FILES}
    from scripts.operator_template_isolation import isolate_template
    sources, isolation = isolate_template(sources)
    if source_hash != file_hash(data):
        raise ValueError("Training source changed during prepare")
    module = "aflow_evidence_" + digest(str(root))[:16]
    code_hashes = code_fingerprints()
    code_sources = {name: (REPO_ROOT / name).read_bytes() for name in code_hashes}
    if code_fingerprints() != code_hashes:
        raise ValueError("Runtime code changed during prepare")
    root.mkdir(parents=True)
    atomic_json(root / "inputs/samples.json", samples)
    atomic_json(root / "inputs/protocol.json", protocol)
    atomic_json(root / "inputs/selection.json", selection)
    atomic_json(root / "model_settings.json", settings)
    for name, source_text in sources.items():
        path = root / "base_template" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(source_text.replace("workspace.MATH.workflows.template", module)
                        if name.endswith(".py") else source_text, encoding="utf-8")
    empty_library = OperatorLibrary._empty_data()
    atomic_json(root / "inputs/base_library.json", empty_library)
    atomic_json(root / "work/operator_library/operators.json", empty_library)
    for name, content in code_sources.items():
        destination = root / "frozen_code" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    hashes = {str(p.relative_to(root)): file_hash(p) for directory in
              (root / "inputs", root / "base_template") for p in sorted(directory.iterdir())}
    hashes["model_settings.json"] = file_hash(root / "model_settings.json")
    hashes.update({"frozen_code/" + name: expected for name, expected in code_hashes.items()})
    manifest = {"schema_version": 1, "pipeline": PIPELINE, "prepared_at": now(),
                "protocol": protocol, "config": asdict(config), "module_name": module,
                "sample_index": [{"problem_id": r["problem_id"], "role": r["evidence_role"]} for r in samples],
                "counts": {"target": target_count, "control": control_count},
                "source": {"path": str(data), "sha256": source_hash, "name": source_name,
                           "declared_split": "train" if evidence_mode == "independent_natural" else "historical_replay",
                           "excluded_rows": excluded, "exclusions": exclusions},
                "baseline": "generic_two_stage", "code_hashes": code_hashes,
                "evidence_mode": evidence_mode, "template_isolation": isolation,
                "runtime_environment": runtime_environment(), "input_hashes": hashes,
                "baseline_request_upper_bound": 2 * len(samples) * config.discovery_runs,
                "request_upper_bound": 2 * len(samples) * config.discovery_runs + 1 + 2 * config.candidate_count,
                "review_policy": {"same_mechanism_confirmed_errors_per_problem": config.min_wrong_repeats,
                                  "whole_scope_human_confirmation_required": True,
                                  "contradictions_block_generation": True},
                "model_calls_made_during_prepare": 0,
                "limitations": ["User-declared train source and human identity; not authenticated",
                                "Local hashes and event ordering are not an external timestamp attestation",
                                "Only known reservations and explicit exclusions are checked",
                                "Input selectors are conservative recognizers, not mathematical applicability proofs"]}
    atomic_json(root / "manifest.json", manifest)
    atomic_json(root / "state.json", {"manifest_sha256": digest(manifest), "status": "prepared"})
    # The first event exists before any API request is permitted.
    atomic_json(root / "events/prepared.json", {"at": manifest["prepared_at"], "manifest_sha256": digest(manifest)})
    return manifest


class EvidenceStore(RunStore):
    def __init__(self, root, progress=None):
        self.root = Path(root).resolve()
        self.manifest = read_json(self.root / "manifest.json")
        self.progress = progress
        if self.manifest.get("pipeline") != PIPELINE:
            raise ValueError("Not an independent evidence run")
        if read_json(self.root / "state.json")["manifest_sha256"] != digest(self.manifest):
            raise ValueError("Frozen manifest changed")
        event = read_json(self.root / "events/prepared.json")
        if event != {"at": self.manifest["prepared_at"], "manifest_sha256": digest(self.manifest)}:
            raise ValueError("Preparation event changed")
        for name, expected in self.manifest["input_hashes"].items():
            if file_hash(self.root / name) != expected:
                raise ValueError(f"Frozen input changed: {name}")
        if code_fingerprints() != self.manifest["code_hashes"]:
            raise ValueError("Runtime code changed; prepare a new run")
        if runtime_environment() != self.manifest["runtime_environment"]:
            raise ValueError("Runtime dependencies changed")
        self.config = LoopConfig(**self.manifest["config"])
        self.config.validate()
        validate_protocol(self.manifest["protocol"])
        if read_json(self.root / "inputs/protocol.json") != self.manifest["protocol"]:
            raise ValueError("Protocol differs from manifest")
        from scripts.operator_library import stable_problem_id
        rows = self.rows()
        ids = [stable_problem_id(r["problem"]) for r in rows]
        if len(set(ids)) != len(ids) or ids != [r["problem_id"] for r in rows]:
            raise ValueError("Duplicate or invalid sample IDs")
        expected_index = [{"problem_id": r["problem_id"], "role": r["evidence_role"]} for r in rows]
        if expected_index != self.manifest["sample_index"]:
            raise ValueError("Sample index differs from frozen rows")
        for row in rows:
            role = "target" if matches(self.manifest["protocol"]["scope"], row["problem"]) else "control"
            if role != row["evidence_role"]:
                raise ValueError("Sample role differs from frozen input scope")
            control_scope = self.manifest["protocol"].get("control_scope")
            if role == "control" and control_scope and not matches(control_scope, row["problem"]):
                raise ValueError("Control does not satisfy frozen boundary scope")
        if "control_scope" in self.manifest["protocol"]:
            _, _, verified = select_pools(self.manifest["protocol"], rows,
                                         self.manifest["counts"]["target"], self.manifest["counts"]["control"])
            saved = read_json(self.root / "inputs/selection.json")
            if verified["selected"] != saved["selected"]:
                raise ValueError("Frozen selection explanations changed")

    def rows(self, role="evidence"):
        if role != "evidence":
            raise ValueError("Evidence run contains no design or hidden data")
        return read_json(self.root / "inputs/samples.json")

    def event(self, name, **values):
        path = self.root / "events" / f"{name}.json"
        if not path.exists():
            atomic_json(path, {"at": now(), "manifest_sha256": digest(self.manifest), **values})

    def observation_inputs(self, row, repeat):
        return {"manifest_sha256": digest(self.manifest), "item": row, "run": repeat,
                "operator": None, "regression": False}

    def complete(self):
        completed = self.get("baseline_complete", digest(self.manifest))
        if completed is None:
            raise ValueError("Complete all baseline observations before diagnosis or review")
        verify_request_hashes(self, completed["request_hashes"])
        observations = []
        for repeat in range(1, self.config.discovery_runs + 1):
            for row in self.rows():
                key = f"evidence/baseline/{row['problem_id']}/{repeat}"
                output = self.get(key, self.observation_inputs(row, repeat))
                if output is None:
                    raise ValueError("Missing frozen baseline observation")
                observations.append({**output, "problem_id": row["problem_id"], "run": repeat})
        if digest(observations) != completed["observations_sha256"]:
            raise ValueError("Completed baseline observations changed")
        if read_json(self.root / "observations.json") != observations:
            raise ValueError("Exported observations differ from baseline checkpoints")
        if read_json(self.root / "baseline_summary.json") != completed:
            raise ValueError("Baseline summary differs from completion checkpoint")
        packet = build_packet(self.rows(), observations, self.config.min_wrong_repeats)
        packet["source_role"] = self.manifest.get("evidence_mode", "independent_natural")
        if read_json(self.root / "packet.json") != packet:
            raise ValueError("Diagnostic packet differs from complete baseline")
        return observations, packet


async def collect(store, backend):
    if store.get("baseline_complete", digest(store.manifest)) is not None:
        store.complete()
        return read_json(store.root / "baseline_summary.json")
    store.event("baseline_started")
    store.status("collecting_baseline")
    observations = []
    for repeat in range(1, store.config.discovery_runs + 1):
        for item in store.rows():
            key = f"evidence/baseline/{item['problem_id']}/{repeat}"
            inputs = store.observation_inputs(item, repeat)
            result = store.get(key, inputs)
            if result is None:
                result = await backend.execute(item=item, operator=None, regression=False, key=key)
                store.put(key, inputs, result)
            observations.append({**result, "problem_id": item["problem_id"], "run": repeat})
            store.status("collecting_baseline", completed=len(observations),
                         expected=len(store.manifest["sample_index"]) * store.config.discovery_runs)
    packet = build_packet(store.rows(), observations, store.config.min_wrong_repeats)
    packet["source_role"] = store.manifest.get("evidence_mode", "independent_natural")
    atomic_json(store.root / "packet.json", packet)
    summary = {"status": "baseline_complete", "observations": len(observations),
               "observations_sha256": digest(observations), "packet_sha256": digest(packet),
               "valid_observations": sum(not o.get("error") for o in observations),
               "execution_errors": sum(bool(o.get("error")) for o in observations),
               "usage": backend.usage(), "promotion_authorized": False,
               "request_hashes": request_hashes(store, "evidence/baseline/")}
    atomic_json(store.root / "observations.json", observations)
    atomic_json(store.root / "baseline_summary.json", summary)
    store.put("baseline_complete", digest(store.manifest), summary)
    store.event("baseline_completed", observations_sha256=digest(observations))
    store.status("baseline_complete", observations=len(observations))
    return summary


def diagnostic_input(store):
    observations, packet = store.complete()
    return {"manifest_sha256": digest(store.manifest), "observations_sha256": digest(observations),
            "packet_sha256": digest(packet)}


def save_diagnostic(store, response, origin):
    inputs = diagnostic_input(store)
    if store.get("diagnostic", inputs) is not None:
        raise FileExistsError("Diagnosis already frozen; do not overwrite it")
    _, packet = store.complete()
    checked = validate_response(packet, response, store.config.min_stable_problems)
    payload = {"response": response, "origin": origin,
               "request_hashes": request_hashes(store, "evidence_diagnosis/")}
    store.put("diagnostic", inputs, payload)
    atomic_json(store.root / "diagnostic_validation.json", checked)
    store.event("diagnosis_frozen", diagnostic_sha256=digest(payload))
    store.status("awaiting_semantic_review")
    return payload


async def diagnose(store):
    from scripts.async_llm import LLMsConfig
    from scripts.offline_loop_runtime import RecordedLLM, RequestLedger
    inputs = diagnostic_input(store)
    existing = store.get("diagnostic", inputs)
    if existing is not None:
        verify_request_hashes(store, existing["request_hashes"])
        return existing
    observations, packet = store.complete()
    if not packet["cases"]:
        return save_diagnostic(store, {"analyses": [], "groups": []}, "empty failure packet; no model call")
    if model_settings(store.config) != read_json(store.root / "model_settings.json"):
        raise ValueError("Model settings changed")
    model_packet = {**packet, "cases": [{**c, "failed_runs": [
        {k: v for k, v in r.items() if k != "prediction"} for r in c["failed_runs"]]} for c in packet["cases"]]}
    prompt = (POLICY + "\nFrozen hypothesis to test (it may be refuted; preserve its mechanism_id only if supported):\n"
              + json.dumps(store.manifest["protocol"], ensure_ascii=False)
              + "\nIndependent baseline evidence:\n" + json.dumps(model_packet, ensure_ascii=False))
    store.event("diagnosis_started", diagnostic_input_sha256=digest(inputs))
    llm = RecordedLLM(RequestLedger(store), LLMsConfig.default().get(store.config.opt_model), "evidence_diagnosis")
    try:
        raw = await llm(prompt)
        (store.root / "diagnostic_raw_response.txt").write_text(raw, encoding="utf-8")
        return save_diagnostic(store, json.loads(raw), "recorded model response")
    finally:
        await llm.close()


def review_context(store):
    observations, packet = store.complete()
    diagnostic = store.get("diagnostic", diagnostic_input(store))
    if diagnostic is None:
        raise ValueError("Freeze a diagnosis before semantic review")
    verify_request_hashes(store, diagnostic["request_hashes"])
    return observations, packet, diagnostic


def make_review_template(store):
    observations, packet, diagnostic = review_context(store)
    checked = validate_response(packet, diagnostic["response"], store.config.min_stable_problems)
    return review_template(review_subject(store.manifest, observations, packet, diagnostic), checked)


def apply_review(store, review):
    observations, packet, diagnostic = review_context(store)
    subject = review_subject(store.manifest, observations, packet, diagnostic)
    # The first submitted review freezes a decision, including rejection. A new
    # experiment is required to revise the hypothesis or rerun a failed gate.
    if store.get("semantic_review", subject) is not None:
        raise FileExistsError("Semantic review already frozen")
    result = evaluate_review(store.manifest, observations, packet, diagnostic, review)
    store.put("semantic_review", subject, {"review": review, "result": result})
    atomic_json(store.root / "review_report.json", result)
    store.event("review_frozen", subject_sha256=subject, review_sha256=digest(review), decision=result["status"])
    store.status(result["status"])
    return result


def authorization(store):
    observations, packet, diagnostic = review_context(store)
    subject = review_subject(store.manifest, observations, packet, diagnostic)
    saved = store.get("semantic_review", subject)
    if saved is None:
        raise ValueError("No frozen semantic review")
    result = evaluate_review(store.manifest, observations, packet, diagnostic, saved["review"])
    if result != saved["result"] or not result["eligible_for_generation"]:
        raise ValueError("Semantic evidence does not authorize generation")
    return result


def generation_context(store, approval):
    observations, packet, diagnostic = review_context(store)
    approved = {(a["problem_id"], a["run"]): a for a in approval["analyses"]
                if a["status"] in {"human_confirmed", "program_verified"}
                and a["mechanism_id"] == store.manifest["protocol"]["scope"]["mechanism_id"]}
    target_ids = set(approval["semantically_supported_problem_ids"])
    context = []
    checked = validate_response(packet, diagnostic["response"], store.config.min_stable_problems)
    for a in checked["accepted_analyses"]:
        key = (a["problem_id"], a["run"])
        if key not in approved or key[0] not in target_ids:
            continue
        case = next(c for c in packet["cases"] if c["problem_id"] == key[0])
        verdict = approved[key]
        # Program proofs do not endorse a model's free-text explanation.
        evidence = a if verdict["status"] == "human_confirmed" else verdict["program_check"]["witness"]
        context.append({"problem_id": key[0], "run": key[1], "problem": case["problem"],
                        "reference_solution": case["reference_solution"], "evidence": evidence,
                        "review_status": verdict["status"]})
    return {"authorization_sha256": digest(approval), "scope": store.manifest["protocol"]["scope"],
            "hypothesis": store.manifest["protocol"]["hypothesis"], "evidence": context,
            "claim_status": "reviewed hypothesis; not a causal proof",
            "evidence_mode": store.manifest.get("evidence_mode", "independent_natural"),
            "promotion_authorized": False}


async def generate(store, backend):
    from scripts.offline_operator_loop import candidate_fingerprint, register_candidates
    from scripts.operator_library import OperatorLibrary
    approved = authorization(store)  # Before any generator request.
    context = generation_context(store, approved)
    plan = store.get("reviewed_generation", context)
    if plan is None:
        store.event("generation_started", authorization_sha256=digest(approved))
        plan = await backend.generate_reviewed(context=context)
        plan["request_hashes"] = request_hashes(store, "reviewed_generation/")
        store.put("reviewed_generation", context, plan)
    verify_request_hashes(store, plan["request_hashes"])
    saved = store.get("candidates", plan)
    if saved is not None:
        library = OperatorLibrary(store.root / "work/operator_library/operators.json", auto_create=False)
        for item in saved:
            if candidate_fingerprint(library.get(item["name"])) != item["fingerprint"]:
                raise ValueError("Reviewed candidate changed")
        return [item["name"] for item in saved]
    names = register_candidates(store, plan)
    store.event("candidates_generated", names=names, authorization_sha256=digest(approved))
    store.status("candidates_generated", names=names, promotion_authorized=False)
    return names


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--output-dir", type=Path, required=True)
    prep.add_argument("--data", type=Path, required=True)
    prep.add_argument("--source-name", required=True)
    prep.add_argument("--protocol", type=Path, required=True)
    prep.add_argument("--config", type=Path, required=True, help="LoopConfig JSON; frozen before requests")
    prep.add_argument("--target-count", type=int, required=True)
    prep.add_argument("--control-count", type=int, required=True)
    prep.add_argument("--exclude-data", type=Path, action="append", default=[])
    prep.add_argument("--evidence-mode", choices=["independent_natural", "historical_replay"],
                      default="independent_natural", help="Replay permits known history; never independent evidence")
    for name in ("run", "diagnose", "import-diagnosis", "review-template", "review", "generate", "status"):
        command = sub.add_parser(name)
        command.add_argument("--output-dir", type=Path, required=True)
        if name in {"run", "diagnose", "generate"}:
            command.add_argument("--allow-api", action="store_true")
        if name == "import-diagnosis": command.add_argument("--response", type=Path, required=True)
        if name == "review-template": command.add_argument("--file", type=Path, required=True)
        if name == "review": command.add_argument("--file", type=Path, required=True)
        if name == "review": command.add_argument("--check-only", action="store_true",
                                                  help="Validate and preview without freezing a decision")
    return p


def main():
    from scripts.offline_loop_runtime import BudgetExceeded, LiveBackend
    args = parser().parse_args()
    if args.command == "prepare":
        result = prepare(root=args.output_dir, data=args.data, protocol=read_json(args.protocol),
                         source_name=args.source_name, config=LoopConfig(**read_json(args.config)),
                         target_count=args.target_count, control_count=args.control_count,
                         exclude_paths=args.exclude_data, evidence_mode=args.evidence_mode)
        print(json.dumps({"status": "prepared", "counts": result["counts"],
                          "request_upper_bound": result["request_upper_bound"], "model_calls": 0}))
        return
    paid = args.command in {"run", "diagnose", "generate"}
    if paid and not args.allow_api:
        raise SystemExit(f"{args.command} requires --allow-api")
    if args.command == "status":
        store = EvidenceStore(args.output_dir)
        print(json.dumps(read_json(store.root / "state.json"), ensure_ascii=False, indent=2))
        return
    with run_lock(args.output_dir):
        store = EvidenceStore(args.output_dir)
        try:
            if args.command == "run":
                # A finished collection can be inspected without model configuration.
                if store.get("baseline_complete", digest(store.manifest)) is not None:
                    store.complete()
                    result = read_json(store.root / "baseline_summary.json")
                else:
                    result = asyncio.run(collect(store, LiveBackend(store)))
            elif args.command == "diagnose":
                if model_settings(store.config) != read_json(store.root / "model_settings.json"):
                    raise ValueError("Model settings changed")
                result = asyncio.run(diagnose(store))
            elif args.command == "import-diagnosis":
                result = save_diagnostic(store, read_json(args.response),
                                         {"kind": "user_import", "source_sha256": file_hash(args.response)})
            elif args.command == "review-template":
                if args.file.exists(): raise FileExistsError(args.file)
                atomic_json(args.file, make_review_template(store))
                result = {"review_template": str(args.file), "note": "Fill genuine reviewer evidence; pending is not approval"}
            elif args.command == "review":
                if args.check_only:
                    observations, packet, diagnostic = review_context(store)
                    result = evaluate_review(store.manifest, observations, packet, diagnostic, read_json(args.file))
                else:
                    result = apply_review(store, read_json(args.file))
            else:
                authorization(store)
                result = {"candidates": asyncio.run(generate(store, LiveBackend(store))), "promotion_authorized": False}
        except BudgetExceeded:
            store.status("budget_exhausted")
            raise
        except KeyboardInterrupt:
            store.status("interrupted")
            raise
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
