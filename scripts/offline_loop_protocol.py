"""Frozen inputs and crash-safe storage for the offline operator loop.

This is a provenance/visibility boundary, not a sandbox or proof that public
questions were absent from a foundation model's pretraining data.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from scripts.operator_library import ACTIVE_STATUSES, OperatorLibrary, combined_artifact_sha256, stable_problem_id

REPO_ROOT = Path(__file__).resolve().parents[1]
ROLES = ("discovery", "design", "gate", "regression")
TEMPLATE_FILES = ("operator.py", "operator_an.py", "op_prompt.py", "operator.json")


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def run_lock(root: Path) -> Iterator[None]:
    with (root / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another process is already using this run directory") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass(frozen=True)
class LoopConfig:
    seed: int = 170101
    discovery_runs: int = 3
    evaluation_runs: int = 3
    candidate_count: int = 3
    min_stable_problems: int = 3
    min_wrong_repeats: int = 2
    min_error_rate: float = 0.20
    min_regression_problems: int = 3
    timeout: float = 180.0
    max_requests: int = 10000
    max_output_tokens: int = 4096
    thinking_mode: str = "provider-default"
    opt_model: str = ""
    exec_model: str = ""

    def validate(self) -> None:
        for name in ("discovery_runs", "evaluation_runs", "candidate_count",
                     "min_stable_problems", "min_wrong_repeats",
                     "min_regression_problems", "max_requests", "max_output_tokens"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.candidate_count < 2:
            raise ValueError("candidate_count must be at least 2")
        if not 2 <= self.min_wrong_repeats <= self.discovery_runs:
            raise ValueError("Require 2 <= min_wrong_repeats <= discovery_runs")
        if self.min_stable_problems < 2:
            raise ValueError("At least two distinct stable failures are required")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if not 0 < self.min_error_rate <= 1:
            raise ValueError("min_error_rate must be in (0, 1]")
        if not self.opt_model or not self.exec_model:
            raise ValueError("Specify both opt_model and exec_model")
        if self.thinking_mode not in {"provider-default", "disabled", "enabled"}:
            raise ValueError("Invalid thinking_mode")


def load_rows(path: Path, *, training: bool = False) -> list[dict]:
    from benchmarks.math import MATHBenchmark
    scorer = MATHBenchmark("MATH", "", "") if training else None
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not all(
            isinstance(row.get(key), str) and row[key].strip()
            for key in ("problem", "solution")
        ):
            raise ValueError(f"{path}:{line_number}: require nonempty problem and solution")
        if training and str(row.get("split", "train")).lower() not in ("train", "training"):
            raise ValueError(f"{path}:{line_number}: non-training row cannot enter discovery")
        if scorer is not None and not scorer.extract_model_answer(row["solution"]).strip():
            raise ValueError(f"{path}:{line_number}: empty extracted reference answer")
        rows.append({**row, "problem_id": stable_problem_id(row["problem"])})
    if not rows:
        raise ValueError(f"Empty dataset: {path}")
    return rows


def code_fingerprints() -> dict[str, str]:
    # Include transitive runtime modules; document-only changes do not matter.
    paths = [p for folder in ("scripts", "benchmarks")
             for p in (REPO_ROOT / folder).rglob("*.py")]
    return {str(p.relative_to(REPO_ROOT)): file_hash(p) for p in sorted(paths)}


def runtime_environment() -> dict:
    """Freeze scoring/API dependencies too; equal source hashes are not enough."""
    from benchmarks.math import MATHBenchmark

    versions = {}
    for name in ("sympy", "antlr4-python3-runtime", "mpmath", "regex", "openai", "pydantic"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"python": platform.python_version(), "packages": versions,
            "scorer_version": MATHBenchmark.SCORER_VERSION}


def prepare_run(
    *, root: Path, data: Path, source_name: str, counts: dict[str, int],
    config: LoopConfig, exclude_paths: list[Path] | None = None,
    template: Path | None = None, library: Path | None = None,
    policy: dict | None = None,
) -> dict:
    """Prepare immutable train-derived pools without creating an API client."""
    from scripts.operator_tournament import PromotionPolicy, QualityWeights

    config.validate()
    root, data = root.resolve(), data.resolve()
    if root.exists():
        raise FileExistsError(f"Run directory already exists; use run/status: {root}")
    if not source_name.strip():
        raise ValueError("A training source/provenance description is required")
    if set(counts) != set(ROLES) or any(type(n) is not int or n <= 0 for n in counts.values()):
        raise ValueError(f"Specify positive counts for all roles: {ROLES}")
    template = (template or REPO_ROOT / "workspace/MATH/workflows/template").resolve()
    library = (library or REPO_ROOT / "workspace/MATH/operator_library/operators.json").resolve()
    excluded = list(exclude_paths or [])
    excluded += [p for p in (REPO_ROOT / "data/datasets/math_validate.jsonl",
                             REPO_ROOT / "data/datasets/math_test.jsonl") if p.exists()]
    if data in {p.resolve() for p in excluded}:
        raise ValueError("Evaluation/development dataset cannot be used as independent training data")
    excluded_ids: set[str] = set()
    exclusions = []
    for path in sorted(set(p.resolve() for p in excluded)):
        excluded_ids.update(row["problem_id"] for row in load_rows(path))
        exclusions.append({"path": str(path), "sha256": file_hash(path)})
    # Prevent a new sibling run from silently reusing previously reserved
    # development/hidden pools. Other historical workflows must be supplied
    # explicitly through --exclude-data; this is not a complete history audit.
    prior_runs = set()
    for parent in (root.parent, REPO_ROOT / "workspace/MATH/offline_runs",
                   REPO_ROOT / "workspace/MATH/evidence_runs"):
        if parent.is_dir():
            prior_runs.update(parent.glob("*/manifest.json"))
    for prior in sorted(prior_runs):
        value = read_json(prior)
        if value.get("pipeline") == "aflow_independent_mechanism_evidence_v1":
            pool_path = prior.parent / "inputs/samples.json"
            if file_hash(pool_path) != value["input_hashes"]["inputs/samples.json"]:
                raise ValueError("Historical evidence pool changed")
            previous = read_json(pool_path)
            excluded_ids.update(stable_problem_id(row["problem"]) for row in previous)
            exclusions.append({"path": str(pool_path), "sha256": file_hash(pool_path), "role": "evidence"})
            continue
        if value.get("pipeline") not in {"aflow_offline_loop_v1", "aflow_reviewed_continuation_v1", "aflow_operational_replay_v1"}:
            continue
        for role in ROLES:
            pool_path = prior.parent / "inputs" / f"{role}.json"
            previous = read_json(pool_path)
            excluded_ids.update(stable_problem_id(row["problem"]) for row in previous)
            exclusions.append({"path": str(pool_path), "sha256": file_hash(pool_path), "role": role})
    source_rows = load_rows(data, training=True)
    unique: dict[str, dict] = {}
    excluded_count = duplicates = 0
    source_ids = {}
    for row in source_rows:
        pid = row["problem_id"]
        if pid in excluded_ids:
            excluded_count += 1
            continue
        if pid in unique:
            if unique[pid]["solution"].strip() != row["solution"].strip():
                raise ValueError(f"Conflicting reference solutions for duplicated problem {pid}")
            duplicates += 1
            continue
        if row.get("source_id"):
            source_id = str(row["source_id"])
            if source_id in source_ids and source_ids[source_id] != pid:
                raise ValueError(f"Multiple variants share source_id {source_id}; supply group-disjoint data")
            source_ids[source_id] = pid
        unique[pid] = row
    rows = list(unique.values())
    if len(rows) < sum(counts.values()):
        raise ValueError(f"Need {sum(counts.values())} unique unused training rows; have {len(rows)}")
    rng = random.Random(config.seed)
    # Seeded sample, then spread the selected subject/level groups over pools.
    # This balances allocation approximately; it is not an exact stratified draw.
    rng.shuffle(rows)
    selected = rows[:sum(counts.values())]
    selected.sort(key=lambda r: (str(r.get("type", r.get("subject", ""))), str(r.get("level", ""))))
    pools: dict[str, list[dict]] = {role: [] for role in ROLES}
    for row in selected:
        eligible = [role for role in ROLES if len(pools[role]) < counts[role]]
        role = min(eligible, key=lambda role: (len(pools[role]) / counts[role], rng.random()))
        pools[role].append(row)

    # Finish read-only validation before creating output files.
    sources = {name: (template / name).read_text(encoding="utf-8") for name in TEMPLATE_FILES}
    base = OperatorLibrary(library, auto_create=False)
    active = base.list_records(statuses=ACTIVE_STATUSES)
    module_name = "aflow_offline_" + hashlib.sha256(str(root).encode()).hexdigest()[:16]
    for record in active:
        record["module"] = module_name + ".operator"
        artifact = record.get("artifact", {})
        for inline, path_key in (("class_code", "class_code_path"), ("prompt_code", "prompt_code_path")):
            artifact[inline] = base._artifact_text(artifact, inline, path_key)
            artifact.pop(path_key, None)
        if combined_artifact_sha256(artifact["class_code"], artifact["prompt_code"]) != artifact.get("artifact_sha256"):
            raise ValueError(f"Original active artifact failed integrity check: {record['name']}")
    promotion = asdict(PromotionPolicy(**(policy or {})))
    for name, value in promotion.items():
        if name.startswith("min_") and (type(value) is not int or value <= 0):
            raise ValueError(f"Invalid promotion count: {name}")
        if name.startswith("max_") and (not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError(f"Invalid promotion rate: {name}")
    if promotion["min_unique_target_problems"] < 2 or promotion["min_unique_winning_problems"] < 2:
        raise ValueError("Promotion must require distinct problems and distinct wins")
    root.mkdir(parents=True, exist_ok=False)
    for role, pool in pools.items():
        atomic_json(root / "inputs" / f"{role}.json", pool)
    (root / "base_template").mkdir()
    old_module = "workspace.MATH.workflows.template"
    for name, source in sources.items():
        (root / "base_template" / name).write_text(
            source.replace(old_module, module_name) if name.endswith(".py") else source,
            encoding="utf-8",
        )
    base_payload = {**base._empty_data(), "operators": {r["name"]: r for r in active}}
    atomic_json(root / "inputs/base_library.json", base_payload)
    atomic_json(root / "work/operator_library/operators.json", base_payload)
    manifest = {
        "schema_version": 1, "pipeline": "aflow_offline_loop_v1",
        "config": asdict(config), "counts": counts,
        "source": {"name": source_name, "declared_split": "train", "path": str(data),
                   "sha256": file_hash(data), "input_rows": len(source_rows),
                   "excluded_rows": excluded_count, "duplicate_rows": duplicates,
                   "exclusions": exclusions},
        "baseline": "generic_two_stage", "module_name": module_name,
        "generation_backend": "fixed_skeleton_prompt_only_v1",
        "request_upper_bound": (
            2 * counts["discovery"] * config.discovery_runs
            + 2 * (1 + config.candidate_count)
            + counts["design"] * config.evaluation_runs * (2 + 4 * config.candidate_count)
            + config.evaluation_runs * (4 * counts["gate"] + 6 * counts["regression"])
        ),
        "promotion_policy": promotion, "quality_weights": asdict(QualityWeights()),
        "runtime_environment": runtime_environment(),
        "code_hashes": code_fingerprints(),
        "input_hashes": {str(p.relative_to(root)): file_hash(p)
                         for directory in (root / "inputs", root / "base_template")
                         for p in sorted(directory.iterdir())},
        "limitations": ["training origin is user-declared, not independently verified",
                        "exact normalized-text disjointness is not semantic disjointness",
                        "feature clusters are mechanism hypotheses, not causal proof",
                        "one frozen candidate gets one gate; not a final natural-distribution test"],
    }
    atomic_json(root / "manifest.json", manifest)
    atomic_json(root / "state.json", {"manifest_sha256": digest(manifest), "status": "prepared"})
    return manifest


def verify_run(root: Path) -> dict:
    manifest = read_json(root / "manifest.json")
    state = read_json(root / "state.json")
    if digest(manifest) != state["manifest_sha256"]:
        raise ValueError("Frozen manifest changed; create a new run instead")
    for relative, expected in manifest["input_hashes"].items():
        if file_hash(root / relative) != expected:
            raise ValueError(f"Frozen input changed: {relative}")
    if code_fingerprints() != manifest["code_hashes"]:
        raise ValueError("Runtime code changed since prepare; do not continue the frozen run")
    if runtime_environment() != manifest.get("runtime_environment"):
        raise ValueError("Runtime dependencies changed or were not frozen; create a new run instead")
    seen: set[str] = set()
    for role in ROLES:
        pool = read_json(root / "inputs" / f"{role}.json")
        ids = [stable_problem_id(row["problem"]) for row in pool]
        if len(set(ids)) != len(ids) or seen.intersection(ids):
            raise ValueError(f"Data overlap in frozen role {role}")
        seen.update(ids)
    return manifest


class RunStore:
    def __init__(self, root: Path, progress: Callable[[str], None] | None = None):
        self.root = root
        self.manifest = verify_run(root)
        self.config = LoopConfig(**self.manifest["config"])
        self.progress = progress

    def rows(self, role: str) -> list[dict]:
        if role not in ROLES:
            raise ValueError(role)
        return read_json(self.root / "inputs" / f"{role}.json")

    def get(self, key: str, inputs: Any) -> Any | None:
        path = self.root / "checkpoints" / f"{key}.json"
        if not path.exists():
            return None
        value = read_json(path)
        if value["input_sha256"] != digest(inputs) or value["output_sha256"] != digest(value["output"]):
            raise ValueError(f"Checkpoint input/output drift: {key}")
        return value["output"]

    def put(self, key: str, inputs: Any, output: Any) -> Any:
        atomic_json(self.root / "checkpoints" / f"{key}.json", {
            "input_sha256": digest(inputs), "output_sha256": digest(output), "output": output,
        })
        return output

    def status(self, status: str, **fields: Any) -> None:
        atomic_json(self.root / "state.json", {
            "manifest_sha256": digest(self.manifest), "status": status, **fields,
        })
        if self.progress:
            self.progress(status)
