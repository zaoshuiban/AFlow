"""Fetch a pinned original MATH train split and exclude known evaluation/history overlap.

The source test split is used for deduplication only. No model is instantiated.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from benchmarks.math import MATHBenchmark
from scripts.offline_loop_protocol import REPO_ROOT, atomic_json, file_hash
from scripts.operator_library import stable_problem_id

DATASET = "EleutherAI/hendrycks_math"
REVISION = "21a5633873b6a120296cce3e2df9d5550074f4a3"
COUNTS = {
    "algebra": (1744, 1187), "counting_and_probability": (771, 474),
    "geometry": (870, 479), "intermediate_algebra": (1295, 903),
    "number_theory": (869, 540), "prealgebra": (1205, 871),
    "precalculus": (746, 546),
}
TEXT_FIELDS = {"problem", "question", "problem_text", "question_text", "input"}
ID_FIELDS = {"problem_id", "stable_problem_id"}


def compact_key(problem: str) -> str:
    text = unicodedata.normalize("NFC", problem).lower()
    text = re.sub(r"\\(?:left|right)\b", "", text)
    text = re.sub(r"\\[,;!]|\\[()\[\]]", "", text)
    text = re.sub(r"\s+", "", text).replace("$", "")
    return hashlib.sha256(text.encode()).hexdigest()


def download(relative: str, cache: Path) -> dict:
    url = f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{relative}"
    destination = cache / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = destination.with_name(destination.name + ".sha256.json")
    if destination.exists():
        if not receipt_path.exists():
            raise ValueError(f"Unverified cache file exists: {destination}")
        receipt = json.loads(receipt_path.read_text())
        if receipt["url"] != url or receipt["sha256"] != file_hash(destination):
            raise ValueError(f"Cached source changed: {destination}")
        return receipt
    fd, temp_name = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            with requests.get(url, stream=True, timeout=(20, 60)) as response:
                response.raise_for_status()
                for chunk in response.iter_content(1024 * 1024):
                    handle.write(chunk)
        receipt = {"url": url, "sha256": file_hash(Path(temp_name)), "bytes": Path(temp_name).stat().st_size}
        os.replace(temp_name, destination)
        atomic_json(receipt_path, receipt)
        return receipt
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def fetch_source(cache: Path) -> tuple[list[dict], list[dict], list[dict]]:
    import pyarrow.parquet as pq
    receipts = [{"file": "README.md", **download("README.md", cache)}]
    pools = {"train": [], "test": []}
    for subject, expected_counts in COUNTS.items():
        for split, expected in zip(("train", "test"), expected_counts):
            relative = f"{subject}/{split}-00000-of-00001.parquet"
            receipt = download(relative, cache)
            rows = pq.read_table(cache / relative).to_pylist()
            if len(rows) != expected:
                raise ValueError(f"Source split size drift: {relative}: {len(rows)} != {expected}")
            for index, row in enumerate(rows):
                if not all(isinstance(row.get(key), str) and row[key].strip()
                           for key in ("problem", "solution", "type", "level")):
                    raise ValueError(f"Malformed source row: {relative}:{index}")
                pools[split].append({
                    **row, "split": split, "source_id": f"{DATASET}/{split}/{subject}/{index}",
                    "source_dataset": DATASET, "source_revision": REVISION,
                    "source_file": relative, "source_row_index": index,
                })
            receipts.append({"file": relative, "split": split, "rows": len(rows), **receipt})
            print(f"Verified source {relative}: {len(rows)} rows", flush=True)
    if len(pools["train"]) != 7500 or len(pools["test"]) != 5000:
        raise ValueError("Source must preserve the original 7,500 / 5,000 split")
    return pools["train"], pools["test"], receipts


def task_references(value):
    """Walk known task fields only; never treat model answers as new problems."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in TEXT_FIELDS and isinstance(item, str) and item.strip():
                yield "text", item
            elif key in ID_FIELDS and isinstance(item, str) and re.fullmatch(r"[0-9a-f]{16}", item):
                yield "id", item
            elif isinstance(item, (dict, list)):
                yield from task_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from task_references(item)


def scan_history(roots: list[Path], excluded_paths: set[Path]) -> tuple[dict, dict]:
    references = {"exact": defaultdict(set), "compact": defaultdict(set)}
    files = set()
    for root in roots:
        for suffix in ("*.json", "*.jsonl", "*.csv"):
            files.update(root.rglob(suffix))
    audit_files = []
    for path in sorted(files):
        if path.resolve() in excluded_paths:
            continue
        count = 0
        errors = []
        def collect(value):
            nonlocal count
            for kind, text in task_references(value):
                if kind == "id":
                    references["exact"][text].add(str(path))
                else:
                    references["exact"][stable_problem_id(text)].add(str(path))
                    references["compact"][compact_key(text)].add(str(path))
                count += 1
        try:
            with path.open(encoding="utf-8-sig") as handle:
                if path.suffix == ".json":
                    collect(json.load(handle))
                elif path.suffix == ".csv":
                    for row in csv.DictReader(handle):
                        collect(row)
                else:
                    for line_number, line in enumerate(handle, 1):
                        if not line.strip():
                            continue
                        try:
                            collect(json.loads(line))
                        except json.JSONDecodeError:
                            errors.append(f"invalid_jsonl_line:{line_number}")
        except (ValueError, OSError, UnicodeError, csv.Error) as exc:
            errors.append(type(exc).__name__)
        audit_files.append({"path": str(path), "sha256": file_hash(path),
                            "task_references": count, "parse_errors": errors})
    return references, {"roots": [str(p) for p in roots], "files": audit_files,
                        "file_count": len(audit_files),
                        "files_with_parse_errors": sum(bool(p["parse_errors"]) for p in audit_files),
                        "task_reference_count": sum(p["task_references"] for p in audit_files),
                        "exact_reference_keys": len(references["exact"]),
                        "compact_reference_keys": len(references["compact"]),
                        "limitations": ["only structured JSON/JSONL/CSV task fields scanned",
                                        "does not audit unstructured logs, documents, or human memory",
                                        "format-normalized overlap is not semantic/pretraining contamination detection"]}


def filter_train(train: list[dict], test: list[dict], history: dict) -> tuple[list[dict], list[dict], dict]:
    exact_test = {stable_problem_id(r["problem"]) for r in test}
    compact_test = {compact_key(r["problem"]) for r in test}
    groups = defaultdict(list)
    for row in train:
        if row.get("split") != "train":
            raise ValueError("Non-train row in candidate training pool")
        groups[compact_key(row["problem"])].append(row)
    retained, excluded = [], []
    scorer = MATHBenchmark("MATH", "", "")
    counts = Counter()
    for key, group in groups.items():
        # Different solution text is allowed if the extracted final answer is identical.
        answers = {" ".join(scorer.extract_model_answer(row["solution"]).split()) for row in group}
        conflicting = len(answers) > 1
        kept = False
        for row in group:
            pid = stable_problem_id(row["problem"])
            reasons = []
            if pid in exact_test:
                reasons.append("source_test_exact_overlap")
            elif key in compact_test:
                reasons.append("source_test_format_overlap")
            paths = set(history["exact"].get(pid, set())) | set(history["compact"].get(key, set()))
            if paths:
                reasons.append("historical_task_overlap")
            if conflicting:
                reasons.append("duplicate_question_reference_conflict")
            if not scorer.extract_model_answer(row["solution"]).strip():
                reasons.append("empty_reference_answer")
            if kept:
                reasons.append("duplicate_training_question")
            if reasons:
                excluded.append({"source_id": row["source_id"], "problem_id": pid,
                                 "reasons": reasons, "history_paths": sorted(paths)})
                counts.update(reasons)
            else:
                retained.append({**row, "problem_id": pid})
                kept = True
    if any(stable_problem_id(r["problem"]) in exact_test or compact_key(r["problem"]) in compact_test for r in retained):
        raise AssertionError("Post-filter source test overlap")
    return retained, excluded, dict(counts)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data/datasets/math_train_independent.jsonl")
    parser.add_argument("--cache", type=Path, default=REPO_ROOT / "data/sources/hendrycks_math_21a5633873")
    parser.add_argument("--audit-dir", type=Path, default=REPO_ROOT / "results/math_train_readiness_20260926/data")
    args = parser.parse_args()
    output, cache, audit_dir = args.output.resolve(), args.cache.resolve(), args.audit_dir.resolve()
    if output.exists() or audit_dir.exists():
        raise SystemExit("Output/audit directory exists; preserve it and select a new location")
    train, test, receipts = fetch_source(cache)
    history, history_audit = scan_history(
        [REPO_ROOT / "workspace/MATH", REPO_ROOT / "results", REPO_ROOT / "data/datasets"], {output},
    )
    retained, excluded, exclusion_counts = filter_train(train, test, history)
    write_jsonl(output, retained)
    audit_dir.mkdir(parents=True, exist_ok=False)
    write_jsonl(audit_dir / "excluded_rows.jsonl", excluded)
    atomic_json(audit_dir / "history_scan.json", history_audit)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "dataset": DATASET, "revision": REVISION,
        "dataset_url": f"https://huggingface.co/datasets/{DATASET}/tree/{REVISION}",
        "source_train_rows": len(train), "source_test_rows_for_exclusion_only": len(test),
        "retained_rows": len(retained), "excluded_rows": len(excluded),
        "exclusion_counts_nonexclusive": exclusion_counts, "source_files": receipts,
        "output": str(output), "output_sha256": file_hash(output),
        "retained_by_subject": dict(Counter(r["type"] for r in retained)),
        "retained_by_level": dict(Counter(r["level"] for r in retained)),
        "history_file_count": history_audit["file_count"],
        "history_files_with_parse_errors": history_audit["files_with_parse_errors"],
        "remaining_known_exact_or_format_overlap": 0,
        "source_field_policy": "problem/solution/type/level preserved; added provenance and train split only",
        "limitations": history_audit["limitations"], "model_requests_made": 0,
    }
    atomic_json(audit_dir / "manifest.json", manifest)
    print(json.dumps({key: manifest[key] for key in (
        "source_train_rows", "retained_rows", "excluded_rows", "exclusion_counts_nonexclusive",
        "history_file_count", "history_files_with_parse_errors", "output", "output_sha256",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
