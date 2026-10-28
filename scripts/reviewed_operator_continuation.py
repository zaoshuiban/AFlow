"""Frozen handoff from reviewed evidence candidates to design/gate/private release.

No diagnosis or generation occurs here. Historical replay is never relabeled as
independent natural evidence. prepare/status make no model calls.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
import json
import shutil
from dataclasses import asdict, replace
from pathlib import Path

from scripts.independent_mechanism_evidence import (
    EvidenceStore, authorization, generation_context, request_hashes, verify_request_hashes,
)
from scripts.mechanism_scope import matches
from scripts.offline_loop_protocol import (
    REPO_ROOT, TEMPLATE_FILES, LoopConfig, RunStore, atomic_json, code_fingerprints,
    digest, file_hash, load_rows, read_json, run_lock, runtime_environment,
)
from scripts.offline_loop_runtime import BudgetExceeded, LiveBackend
from scripts.offline_operator_loop import OfflineLoop, candidate_fingerprint
from scripts.operator_library import OperatorLibrary, stable_problem_id
from scripts.operator_tournament import PromotionPolicy, QualityWeights

PIPELINE = 'aflow_reviewed_continuation_v1'


def checked_candidates(source):
    approval = authorization(source)
    context = generation_context(source, approval)
    plan = source.get('reviewed_generation', context)
    if plan is None or plan.get('review_authorization_sha256') != digest(approval):
        raise ValueError('No generation bound to this semantic authorization')
    verify_request_hashes(source, plan['request_hashes'])
    saved = source.get('candidates', plan)
    if not saved or len(saved) != len(plan['designs']):
        raise ValueError('Generate all reviewed candidates before handoff')
    library = OperatorLibrary(source.root / 'work/operator_library/operators.json', auto_create=False)
    records = []
    for entry in saved:
        record = library.get(entry['name'])
        if not record or record['status'] != 'candidate' or candidate_fingerprint(record) != entry['fingerprint']:
            raise ValueError('Source candidate changed after generation')
        if record['provenance'].get('review_authorization_sha256') != digest(approval):
            raise ValueError('Candidate is not bound to approval')
        records.append(record)
    return approval, records


def validate_pools(source_rows, pools):
    ids, families = set(), set()
    for role, rows in [('evidence', source_rows), *pools.items()]:
        for row in rows:
            pid = stable_problem_id(row['problem'])
            fs = {(key, str(row[key])) for key in ('source_id', 'family_id') if row.get(key)}
            if pid in ids or fs & families:
                raise ValueError(f'Duplicate/overlapping problem or source/family in {role}')
            ids.add(pid)
            families.update(fs)


def prepare(*, root, evidence_run, design_data, gate_data, regression_data,
            evaluation_runs=3, max_requests=1000, evaluation_mode='independent_natural'):
    root = Path(root).resolve()
    if root.exists():
        raise FileExistsError(root)
    if evaluation_mode not in {'independent_natural', 'historical_replay'}:
        raise ValueError('Unknown evaluation mode')
    source = EvidenceStore(evidence_run)
    if root.is_relative_to(source.root):
        raise ValueError("Continuation must be outside the source evidence directory")
    approval, records = checked_candidates(source)
    replay = source.manifest.get('evidence_mode') == 'historical_replay'
    if replay and evaluation_mode != 'historical_replay':
        raise ValueError('Historical evidence cannot be promoted to independent natural evaluation')
    config = replace(source.config, evaluation_runs=evaluation_runs, max_requests=max_requests)
    config.validate()
    paths = {'design': Path(design_data), 'gate': Path(gate_data), 'regression': Path(regression_data)}
    source_hashes = {role: file_hash(p) for role, p in paths.items()}
    pools = {role: load_rows(p, training=evaluation_mode == 'independent_natural') for role, p in paths.items()}
    validate_pools(source.rows(), pools)
    if evaluation_mode == 'independent_natural':
        # Reject known history even if copied into a new JSONL. This is still
        # only a local history/identity check, not semantic or pretraining isolation.
        from scripts.independent_mechanism_evidence import reserved_pools
        history = list(reserved_pools(root.parent))
        for name in ('math_validate.jsonl', 'math_test.jsonl'):
            p = REPO_ROOT / 'data/datasets' / name
            if p.exists(): history.append((p, load_rows(p)))
        blocked = set()
        blocked_families = set()
        for _, rows in history:
            blocked.update(stable_problem_id(r['problem']) for r in rows)
            blocked_families.update((k, str(r[k])) for r in rows for k in ('source_id', 'family_id') if r.get(k))
        for rows in pools.values():
            for r in rows:
                if r['problem_id'] in blocked or any((k, str(r[k])) in blocked_families for k in ('source_id', 'family_id') if r.get(k)):
                    raise ValueError('Evaluation pool overlaps known reserved/test history')
    scope = source.manifest['protocol']['scope']
    policy = PromotionPolicy()
    for role in ('design', 'gate'):
        if sum(matches(scope, r['problem']) for r in pools[role]) < policy.min_unique_target_problems:
            raise ValueError(f'Insufficient in-scope {role} problems')
    for role in ('design', 'regression'):
        if sum(not matches(scope, r['problem']) for r in pools[role]) < config.min_regression_problems:
            raise ValueError(f'Insufficient out-of-scope {role} regression problems')
    if source_hashes != {role: file_hash(p) for role, p in paths.items()}:
        raise ValueError('Evaluation sources changed during prepare')
    # Snapshot under the source lock at the CLI. Revalidate the snapshot before
    # freezing; the original source directory is never written by continuation.
    root.mkdir(parents=True)
    snapshot = root / 'source_evidence'
    shutil.copytree(source.root, snapshot, ignore=shutil.ignore_patterns('.lock', '__pycache__'))
    snap_approval, snap_records = checked_candidates(EvidenceStore(snapshot))
    if snap_approval != approval or snap_records != records:
        raise ValueError('Source changed during handoff')
    module = 'aflow_reviewed_' + digest(str(root))[:16]
    (root / 'base_template').mkdir()
    for name in TEMPLATE_FILES:
        content = (snapshot / 'base_template' / name).read_text()
        (root / 'base_template' / name).write_text(content.replace(source.manifest['module_name'], module))
    for role, rows in {'discovery': source.rows(), **pools}.items():
        atomic_json(root / 'inputs' / f'{role}.json', rows)
    empty = OperatorLibrary._empty_data()
    atomic_json(root / 'inputs/base_library.json', empty)
    candidates = copy.deepcopy(records)
    for record in candidates:
        record['module'] = module + '.operator'
    payload = {**empty, 'operators': {r['name']: r for r in candidates}}
    atomic_json(root / 'inputs/reviewed_candidates.json', payload)
    atomic_json(root / 'work/operator_library/operators.json', payload)
    atomic_json(root / 'model_settings.json', read_json(snapshot / 'model_settings.json'))
    atomic_json(root / 'inputs/authorization.json', approval)
    hashes = {str(p.relative_to(root)): file_hash(p) for directory in
              (root / 'inputs', root / 'base_template', snapshot)
              for p in sorted(directory.rglob('*')) if p.is_file()}
    hashes['model_settings.json'] = file_hash(root / 'model_settings.json')
    n = len(candidates)
    bound = config.evaluation_runs * (len(pools['design']) * (2 + 4*n)
                                     + 4*len(pools['gate']) + 6*len(pools['regression']))
    manifest = {'schema_version': 1, 'pipeline': PIPELINE, 'config': asdict(config),
                'counts': {r: len(rows) for r, rows in {'discovery': source.rows(), **pools}.items()},
                'module_name': module, 'baseline': 'generic_two_stage',
                'generation_backend': 'reviewed_candidate_handoff_no_regeneration_v1',
                'source_evidence': {'original_path': str(source.root), 'manifest_sha256': digest(source.manifest),
                                    'authorization_sha256': digest(approval),
                                    'candidate_fingerprints': {r['name']: candidate_fingerprint(r) for r in records}},
                'candidate_fingerprints': {r['name']: candidate_fingerprint(r) for r in candidates},
                'scope': scope, 'evaluation_mode': evaluation_mode,
                'evidence_mode': source.manifest.get('evidence_mode', 'independent_natural'),
                'evaluation_sources': {role: {'path': str(p.resolve()), 'sha256': source_hashes[role]} for role, p in paths.items()},
                'promotion_policy': asdict(policy), 'quality_weights': asdict(QualityWeights()),
                'request_upper_bound': bound, 'code_hashes': code_fingerprints(),
                'runtime_environment': runtime_environment(), 'input_hashes': hashes,
                'confirmation_evidence_source': 'historical_replay_confirmation' if evaluation_mode == 'historical_replay' else 'offline_independent_confirmation',
                'scientific_scope': ('Historical replay / engineered task families; NOT new independent natural evidence'
                                     if evaluation_mode == 'historical_replay' else 'Reviewed offline confirmation; NOT a final natural-MATH evaluation'),
                'limitations': ['One frozen candidate, one confirmation gate; no replacement after gate failure',
                                'Known identity/family exclusion is not complete semantic isolation',
                                'Private experimental release only; production library unchanged']}
    atomic_json(root / 'manifest.json', manifest)
    atomic_json(root / 'state.json', {'manifest_sha256': digest(manifest), 'status': 'prepared'})
    return manifest


class ReviewedStore(RunStore):
    def __init__(self, root, progress=None):
        super().__init__(Path(root).resolve(), progress)
        if self.manifest.get('pipeline') != PIPELINE:
            raise ValueError('Not a reviewed continuation')
        source = EvidenceStore(self.root / 'source_evidence')
        approval, records = checked_candidates(source)
        if digest(approval) != self.manifest['source_evidence']['authorization_sha256']:
            raise ValueError('Source authorization changed')
        if {r['name']: candidate_fingerprint(r) for r in records} != self.manifest['source_evidence']['candidate_fingerprints']:
            raise ValueError('Source candidate fingerprint changed')
        library = OperatorLibrary(self.root / 'work/operator_library/operators.json', auto_create=False)
        for name, fp in self.manifest['candidate_fingerprints'].items():
            if candidate_fingerprint(library.get(name)) != fp:
                raise ValueError('Handoff candidate changed')
        validate_pools(source.rows(), {r: self.rows(r) for r in ('design', 'gate', 'regression')})
        # Check raw request evidence even when run() returns a cached summary.
        for p in (self.root / 'checkpoints').rglob('*.json'):
            saved = read_json(p)
            output = saved.get('output')
            if isinstance(output, dict) and '_request_hashes' in output:
                if saved['output_sha256'] != digest(output):
                    raise ValueError('Evaluation checkpoint changed')
                verify_request_hashes(self, output['_request_hashes'])


class ReviewedLoop(OfflineLoop):
    async def discover(self):
        # Semantic scope is frozen and already approved, never re-cluster it.
        return {'selected': {'features': [self.store.manifest['scope']['mechanism_id']]}}, []

    async def generate(self, screen, observations):
        return list(self.store.manifest['candidate_fingerprints'])

    def split_scope(self, rows, screen):
        scope = self.store.manifest['scope']
        return ([r for r in rows if matches(scope, r['problem'])],
                [r for r in rows if not matches(scope, r['problem'])])

    async def execute(self, *, item, stage, repeat, operator=None, regression=False):
        key = f"{stage}/{operator or 'baseline'}/{item['problem_id']}/{repeat}"
        inputs = {'item': item, 'operator': operator, 'regression': regression,
                  'fingerprint': candidate_fingerprint(self.library.get(operator)) if operator else None}
        result = self.store.get(key, inputs)
        if result is None:
            result = await self.backend.execute(item=item, operator=operator, regression=regression, key=key)
            # Commit answer and request binding together; no crash window between
            # caching a prediction and freezing its raw response hashes.
            result = {**result, '_request_hashes': request_hashes(self.store, key + '/')}
            self.store.put(key, inputs, result)
        verify_request_hashes(self.store, result['_request_hashes'])
        return {**result, 'problem_id': item['problem_id'], 'run': repeat}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--evidence-run', type=Path, required=True)
    for role in ('design', 'gate', 'regression'):
        p.add_argument('--' + role + '-data', type=Path, required=True)
    p.add_argument('--evaluation-runs', type=int, default=3)
    p.add_argument('--max-requests', type=int, default=1000)
    p.add_argument('--evaluation-mode', choices=['independent_natural', 'historical_replay'], default='independent_natural')
    for name in ('run', 'status'):
        p = sub.add_parser(name)
        p.add_argument('--output-dir', type=Path, required=True)
        if name == 'run': p.add_argument('--allow-api', action='store_true')
    args = parser.parse_args()
    if args.command == 'prepare':
        with run_lock(args.evidence_run):
            manifest = prepare(root=args.output_dir, evidence_run=args.evidence_run,
                               design_data=args.design_data, gate_data=args.gate_data, regression_data=args.regression_data,
                               evaluation_runs=args.evaluation_runs, max_requests=args.max_requests,
                               evaluation_mode=args.evaluation_mode)
        print(json.dumps({'status': 'prepared', 'request_upper_bound': manifest['request_upper_bound'], 'model_calls': 0}))
    elif args.command == 'status':
        store = ReviewedStore(args.output_dir)
        print(json.dumps(read_json(store.root / 'state.json'), indent=2))
    else:
        if not args.allow_api: raise SystemExit('run requires --allow-api')
        with run_lock(args.output_dir):
            store = ReviewedStore(args.output_dir, progress=print)
            try:
                print(json.dumps(asyncio.run(ReviewedLoop(store, LiveBackend(store)).run()), indent=2))
            except BudgetExceeded:
                store.status('budget_exhausted')
                raise

if __name__ == '__main__': main()
