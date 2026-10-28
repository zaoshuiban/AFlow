"""Verified constructed prime-count datasets with calibration-before-formal freezing.

prepare-calibration and freeze are local only. run-calibration explicitly opts
into real baseline requests. This is constructed-data evidence, never natural MATH.
"""
from __future__ import annotations
import argparse
import asyncio
import itertools
import json
import math
import random
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from scripts.offline_loop_protocol import (REPO_ROOT, TEMPLATE_FILES, LoopConfig, RunStore,
    atomic_json, digest, file_hash, read_json, code_fingerprints, runtime_environment, run_lock)
from scripts.independent_mechanism_evidence import model_settings, request_hashes, verify_request_hashes
from scripts.offline_loop_runtime import LiveBackend, BudgetExceeded
from scripts.operator_library import OperatorLibrary, stable_problem_id
from scripts.operator_template_isolation import isolate_template

VERSION = 'constructed_prime_dataset_v1'
LEVELS = {'small': (4, 2), 'medium': (5, 3), 'large': (6, 4)}
POLICY = {
    'metric': 'correct_within_frozen_resource_limits',
    'difficulty_selection': 'hardest level with correct completion rate in [0.25, 0.75]',
    'min_correct_rate': 0.25, 'max_correct_rate': 0.75,
    'minimum_valid_observations_per_level': 4,
    'minimum_failed_problems_per_level': 1,
    'failure_classes': ['completed_wrong', 'output_token_limit', 'timeout'],
    'infrastructure_errors': 'exclude from failure evidence; insufficient valid calibration blocks selection',
    'discovery_gate': {'min_distinct_operationally_failed_problems': 3,
                       'min_failed_repeats_per_problem': 2,
                       'failures_include': ['completed_wrong','output_token_limit','timeout'],
                       'claim': 'operational headroom only; not mathematical-mechanism confirmation'},
    'formal_candidate_selection': 'design only; freeze one candidate before confirmation; no retry with runner-up',
    'confirmation_gate': {'min_target_problems': 4, 'min_unique_winning_problems': 2,
                          'min_net_wins': 2, 'max_boundary_accuracy_loss': 0.0,
                          'require_all_planned_pairs_without_infrastructure_errors': True},
    'evidence_claim': 'constructed task families under frozen resources; not natural-data generalization',
}
KINDS = ('prime', 'repeat_prime', 'even', 'composite')


def validate_parameters(digits, maximum, kind):
    if (not isinstance(digits, list) or any(type(d) is not int or not 0 <= d <= 9 for d in digits)
        or len(set(digits)) != len(digits) or not 2 <= len(digits) <= 7
        or type(maximum) is not int or not 1 <= maximum <= min(5, len(digits)) or kind not in KINDS):
        raise ValueError('Unsupported bounded digit-count parameters')


def group_id(digits):
    # All lengths, surfaces, and boundary variants of one digit set stay together.
    return digest(sorted(digits))[:20]


def trial_prime(n):
    return n >= 2 and all(n % d for d in range(2, math.isqrt(n)+1))


def oracle_permutations(digits, maximum, kind):
    values = set()
    for length in range(1, maximum+1):
        tuples = itertools.product(digits, repeat=length) if kind == 'repeat_prime' else itertools.permutations(digits, length)
        for ds in tuples:
            if ds[0] == 0: continue
            n = int(''.join(map(str, ds)))
            if ((kind in ('prime', 'repeat_prime') and trial_prime(n)) or
                (kind == 'even' and n % 2 == 0) or (kind == 'composite' and n > 1 and not trial_prime(n))):
                values.add(n)
    return values


def oracle_integer_scan(digits, maximum, kind):
    # Different enumeration AND primality algorithm; no installed specialist used.
    limit = 10**maximum
    sieve = bytearray(b'\x01') * limit
    sieve[:2] = b'\x00\x00'
    for d in range(2, math.isqrt(limit-1)+1):
        if sieve[d]:
            sieve[d*d:limit:d] = b'\x00' * len(range(d*d, limit, d))
    allowed = set(map(str, digits))
    result = set()
    for n in range(1, limit):
        text = str(n)
        if not set(text) <= allowed or (kind != 'repeat_prime' and len(set(text)) != len(text)): continue
        if ((kind in ('prime', 'repeat_prime') and sieve[n]) or
            (kind == 'even' and n % 2 == 0) or (kind == 'composite' and n > 1 and not sieve[n])):
            result.add(n)
    return result


def render(digits, maximum, kind):
    ds = ', '.join(map(str, digits))
    repetition = 'Digit repetition is allowed.' if kind == 'repeat_prime' else 'Digit repetition is not allowed.'
    noun = {'prime': 'prime numbers', 'repeat_prime': 'prime numbers',
            'even': 'even positive integers', 'composite': 'composite positive integers'}[kind]
    return (f'Use between 1 and {maximum} digits from the set {{{ds}}} to form positive integers. '
            f'{repetition} A leading zero is not allowed. How many distinct {noun} can be formed?')


def make_row(digits, maximum, kind='prime', level=None):
    validate_parameters(digits, maximum, kind)
    a = oracle_permutations(digits, maximum, kind)
    b = oracle_integer_scan(digits, maximum, kind)
    if a != b: raise ValueError('Independent answer verifiers disagree')
    problem = render(digits, maximum, kind)
    return {'problem_id': stable_problem_id(problem), 'problem': problem,
            'solution': '\\boxed{' + str(len(a)) + '}', 'answer': len(a),
            'family_id': group_id(digits), 'source_kind': 'constructed_verified',
            'parameters': {'digits': sorted(digits), 'max_length': maximum, 'kind': kind},
            'difficulty': level, 'expected_scope': kind == 'prime',
            'verification': {'methods': ['permutation_trial_division', 'integer_scan_eratosthenes'],
                             'values_sha256': digest(sorted(a)), 'count': len(a)}}


def history_groups(paths):
    groups, hashes = set(), {}
    for path in sorted(set(Path(p).resolve() for p in paths)):
        hashes[str(path)] = file_hash(path)
        for line in path.read_text().splitlines():
            if not line.strip(): continue
            row = json.loads(line)
            problem = row.get('problem', '')
            m = re.search(r'(?:digit set|set)\s*\$?\s*\\?\{\s*(\d(?:\s*,\s*\d)+)\s*\\?\}', problem, re.I)
            if m: groups.add(group_id([int(x) for x in re.findall(r'\d', m[1])]))
            params = row.get('parameters', row.get('mechanism_metadata', {}).get('parameters', {}))
            if isinstance(params.get('digits'), list): groups.add(group_id(params['digits']))
    return groups, hashes


def default_history():
    roots = [REPO_ROOT/'workspace/MATH', REPO_ROOT/'results']
    return [p for root in roots for p in root.rglob('*.jsonl')
            if not any(part in {'frozen_code', 'source_evidence', '.git'} for part in p.parts)]


def choose_rows(level, n, used, rng, kinds=('prime',)):
    size, maximum = LEVELS[level]
    candidates = list(itertools.combinations(range(10), size))
    rng.shuffle(candidates)
    result = []
    for digits in candidates:
        gid = group_id(digits)
        if gid in used: continue
        row = make_row(list(digits), maximum, kinds[len(result) % len(kinds)], level)
        # Exclude trivial zero-answer instances based on the oracle, never model results.
        if row['answer'] == 0: continue
        used.add(gid)
        result.append(row)
        if len(result) == n: return result
    raise ValueError(f'Insufficient unused nontrivial parameter groups for {level}: need {n}, have {len(result)}')


def save_bundle(root, *, kind, pools, config, extra):
    root = Path(root).resolve()
    if root.exists(): raise FileExistsError(root)
    settings = model_settings(config)
    sources = {name: (REPO_ROOT/'workspace/MATH/workflows/template'/name).read_text() for name in TEMPLATE_FILES}
    sources, isolation = isolate_template(sources)
    code = code_fingerprints()
    module = 'aflow_constructed_' + digest(str(root))[:16]
    root.mkdir(parents=True)
    for role, rows in pools.items():
        atomic_json(root/'inputs'/f'{role}.json', rows)
        (root/'inputs'/f'{role}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))
    extra = dict(extra)
    provenance = extra.pop('calibration_evidence', None)
    if provenance is not None:
        atomic_json(root/'inputs/calibration_evidence.json', provenance)
    for name, content in sources.items():
        p = root/'base_template'/name
        p.parent.mkdir(exist_ok=True)
        p.write_text(content.replace('workspace.MATH.workflows.template', module))
    atomic_json(root/'model_settings.json', settings)
    atomic_json(root/'inputs/base_library.json', OperatorLibrary._empty_data())
    atomic_json(root/'work/operator_library/operators.json', OperatorLibrary._empty_data())
    for rel, sha in code.items():
        dest = root/'frozen_code'/rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((REPO_ROOT/rel).read_bytes())
        if file_hash(dest) != sha: raise ValueError('Source code changed while freezing')
    manifest = {'pipeline': VERSION, 'stage': kind, 'module_name': module, 'config': asdict(config),
                'policy': POLICY, 'template_isolation': isolation,
                'counts': {role: len(rows) for role, rows in pools.items()}, 'code_hashes': code,
                'runtime_environment': runtime_environment(), **extra,
                'visibility': {'calibration': 'calibration only, excluded from formal pools',
                               'discovery': 'generator may see after baseline',
                               'design': 'candidate selection only', 'confirmation': 'withheld from generator',
                               'boundary_design': 'development regression', 'boundary_confirmation': 'confirmation regression'},
                'input_hashes': {str(p.relative_to(root)): file_hash(p) for folder in ('inputs','base_template','frozen_code')
                                 for p in sorted((root/folder).rglob('*')) if p.is_file()}}
    manifest['input_hashes']['model_settings.json'] = file_hash(root/'model_settings.json')
    atomic_json(root/'manifest.json', manifest)
    atomic_json(root/'state.json', {'manifest_sha256': digest(manifest), 'status': 'prepared'})
    return manifest


class DatasetStore(RunStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.progress = None
        self.manifest = read_json(self.root/'manifest.json')
        if self.manifest.get('pipeline') != VERSION: raise ValueError('Not a constructed prime dataset')
        if read_json(self.root/'state.json')['manifest_sha256'] != digest(self.manifest): raise ValueError('Manifest changed')
        for rel, sha in self.manifest['input_hashes'].items():
            if file_hash(self.root/rel) != sha: raise ValueError('Frozen input changed: '+rel)
        if code_fingerprints() != self.manifest['code_hashes']: raise ValueError('Runtime code changed since freeze')
        if runtime_environment() != self.manifest['runtime_environment']: raise ValueError('Runtime dependencies changed')
        self.config = LoopConfig(**self.manifest['config'])
        self.config.validate()
        seen = set()
        for role in self.manifest['counts']:
            rows = self.rows(role)
            if len(rows) != self.manifest['counts'][role]: raise ValueError('Pool count changed')
            for row in rows:
                if row['family_id'] in seen: raise ValueError('Parameter-group overlap across pools')
                seen.add(row['family_id'])
                expected = make_row(**{'digits':row['parameters']['digits'], 'maximum':row['parameters']['max_length'],
                                       'kind':row['parameters']['kind'], 'level':row['difficulty']})
                if row != expected: raise ValueError('Row or independently verified answer changed')

    def rows(self, role='calibration'):
        if role not in self.manifest['counts']: raise ValueError('Unknown pool role')
        return read_json(self.root/'inputs'/f'{role}.json')


def prepare_calibration(root, config, history_paths):
    config.validate()
    if config.discovery_runs != 2: raise ValueError('Calibration requires two repeats per problem')
    if config.max_requests < 24: raise ValueError('Calibration requires budget for at most 24 baseline requests')
    used, hashes = history_groups(history_paths)
    original = sorted(used)
    rng = random.Random(config.seed)
    rows = [r for level in LEVELS for r in choose_rows(level, 2, used, rng)]
    return save_bundle(root, kind='calibration', pools={'calibration': rows}, config=config,
                       extra={'history_hashes': hashes, 'excluded_parameter_groups': original,
                              'selection': '2 unique parameter groups per level, seeded; no outcome filtering',
                              'baseline_request_upper_bound': 24})


def classify(result, raw):
    if not result.get('error'):
        return 'correct' if result['score'] == 1 else 'completed_wrong'
    errors = [str(r.get('error', '')) for r in raw]
    if 'output_token_limit' in errors: return 'output_token_limit'
    if 'TimeoutError' in str(result.get('error')) or any(e.startswith('APITimeoutError') for e in errors): return 'timeout'
    return 'infrastructure_error'


async def run_calibration(store, backend):
    if store.manifest['stage'] != 'calibration': raise ValueError('Not calibration stage')
    entries = []
    for repeat in (1, 2):
        for row in store.rows():
            key = f"calibration/baseline/{row['problem_id']}/{repeat}"
            result = store.get(key, {'row': row, 'repeat': repeat})
            if result is None:
                response = await backend.execute(item=row, operator=None, regression=False, key=key)
                hashes = request_hashes(store, key+'/')
                raw = [read_json(store.root/p) for p in hashes]
                result = {'response': response, 'failure_class': classify(response, raw), 'request_hashes': hashes}
                store.put(key, {'row': row, 'repeat': repeat}, result)
            verify_request_hashes(store, result['request_hashes'])
            entries.append({'problem_id': row['problem_id'], 'level': row['difficulty'], 'run': repeat, **result})
    result = {'manifest_sha256': digest(store.manifest), 'observations': entries, 'usage': backend.usage()}
    # Idempotent exports: never overwrite a completed calibration with a different history.
    old = store.get('calibration_complete', digest(store.manifest))
    if old is not None:
        if old['observations'] != entries: raise ValueError('Completed calibration changed')
        return old
    store.put('calibration_complete', digest(store.manifest), result)
    atomic_json(store.root/'calibration_result.json', result)
    store.status('calibration_complete')
    return result


def calibration_decision(store):
    result = store.get('calibration_complete', digest(store.manifest))
    if result is None: raise ValueError('Real calibration must finish before formal dataset freeze')
    if result != read_json(store.root/'calibration_result.json'): raise ValueError('Calibration export changed')
    expected = {(r['problem_id'], run): r for r in store.rows() for run in (1, 2)}
    entries = result['observations']
    if len(entries) != len(expected) or {(e['problem_id'], e['run']) for e in entries} != set(expected):
        raise ValueError('Incomplete calibration observations')
    for e in entries:
        row = expected[(e['problem_id'], e['run'])]
        if e['level'] != row['difficulty']: raise ValueError('Calibration level changed')
        key = f"calibration/baseline/{e['problem_id']}/{e['run']}"
        saved = store.get(key, {'row':row, 'repeat': e['run']})
        if saved != {k:e[k] for k in ('response','failure_class','request_hashes')}: raise ValueError('Calibration record changed')
        verify_request_hashes(store, e['request_hashes'])
        if not e['request_hashes']: raise ValueError('Calibration lacks real request evidence')
        raw = [read_json(store.root/p) for p in e['request_hashes']]
        if classify(e['response'], raw) != e['failure_class']: raise ValueError('Failure classification changed')
    stats = {}
    selected = None
    for level in LEVELS:
        rows = [e for e in entries if e['level'] == level]
        eligible = [e for e in rows if e['failure_class'] != 'infrastructure_error']
        counts = Counter(e['failure_class'] for e in rows)
        rate = counts['correct']/len(eligible) if eligible else None
        failed_ids = {e['problem_id'] for e in eligible if e['failure_class'] != 'correct'}
        qualifies = (len(eligible) >= POLICY['minimum_valid_observations_per_level'] and
                     rate is not None and POLICY['min_correct_rate'] <= rate <= POLICY['max_correct_rate'] and
                     len(failed_ids) >= POLICY['minimum_failed_problems_per_level'])
        stats[level] = {'counts':dict(counts), 'valid':len(eligible), 'correct_rate':rate, 'qualifies':qualifies}
        if qualifies: selected = level
    return {'selected_level':selected, 'levels':stats, 'calibration_sha256':digest(result),
            'meaning':'small-sample operational difficulty screen, not a stable semantic mechanism proof'}


def freeze_formal(root, calibration, config, history_paths):
    source = DatasetStore(calibration)
    if Path(root).resolve().is_relative_to(source.root):
        raise ValueError('Formal dataset must be outside calibration directory')
    decision = calibration_decision(source)
    if decision['selected_level'] is None: raise ValueError('No suitable calibrated difficulty; do not force a formal run')
    config.validate()
    for key in ('exec_model','opt_model','timeout','max_output_tokens','thinking_mode'):
        if getattr(config,key) != getattr(source.config,key): raise ValueError('Formal resources differ from calibration: '+key)
    if model_settings(config) != read_json(source.root/'model_settings.json'): raise ValueError('Model configuration changed after calibration')
    used, hashes = history_groups(history_paths)
    used.update(source.manifest['excluded_parameter_groups'])
    used.update(r['family_id'] for r in source.rows())
    excluded = sorted(used)
    rng = random.Random(config.seed)
    level = decision['selected_level']
    pools = {role: choose_rows(level, n, used, rng, kinds)
             for role,n,kinds in [('discovery',6,('prime',)), ('design',4,('prime',)),
                                  ('confirmation',4,('prime',)),
                                  ('boundary_design',3,('repeat_prime','even','composite')),
                                  ('boundary_confirmation',3,('repeat_prime','even','composite'))]}
    # Freeze the full planned budget: 2 calls baseline, up to 2 candidate calls +
    # 2 fallback calls; account for repeated baseline per candidate conservatively.
    upper = (12*config.discovery_runs + 1 + 2*config.candidate_count +
             config.evaluation_runs * (7*6*config.candidate_count + 7*6))
    if config.max_requests < upper: raise ValueError(f'Formal maximum requests must be at least {upper}')
    m = save_bundle(root, kind='formal', pools=pools, config=config,
                    extra={'calibration': {'path':str(source.root), 'manifest_sha256':digest(source.manifest), **decision},
                           'history_hashes':hashes, 'excluded_parameter_groups':excluded,
                           'calibration_evidence': {'manifest':source.manifest,
                               'result':read_json(source.root/'calibration_result.json'),
                               'requests':{str(p.relative_to(source.root)):read_json(p) for p in (source.root/'requests').glob('*.json')}},
                           'request_upper_bound':upper, 'candidate_execution_limit': 'at most 2 LLM calls plus at most 2 fallback calls per task',
                           'budget_note':'Request and token ceilings, not a dollar price. No automatic budget increases.',
                           'formal_execution_status':'dataset frozen only; operational-generation runner must consume this protocol explicitly'})
    return m


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command',required=True)
    for name in ('prepare-calibration','freeze'):
        q = sub.add_parser(name)
        q.add_argument('--output-dir',type=Path,required=True)
        q.add_argument('--config',type=Path,required=True)
        q.add_argument('--exclude-data',type=Path,action='append',default=[])
        if name=='freeze':q.add_argument('--calibration',type=Path,required=True)
    q = sub.add_parser('run-calibration'); q.add_argument('--output-dir',type=Path,required=True);q.add_argument('--allow-api',action='store_true')
    q = sub.add_parser('status');q.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args()
    if args.command in ('prepare-calibration','freeze'):
        config=LoopConfig(**read_json(args.config)); history=default_history()+args.exclude_data
        if args.command=='prepare-calibration': result=prepare_calibration(args.output_dir,config,history)
        else:
            with run_lock(args.calibration): result=freeze_formal(args.output_dir,args.calibration,config,history)
        print(json.dumps({'stage':result['stage'],'counts':result['counts'],'model_calls':0},indent=2))
    elif args.command=='status':
        store=DatasetStore(args.output_dir)
        result=read_json(store.root/'state.json')
        if result['status']=='calibration_complete':result={**result,'decision':calibration_decision(store)}
        print(json.dumps(result,indent=2))
    else:
        if not args.allow_api:raise SystemExit('run-calibration requires --allow-api')
        with run_lock(args.output_dir):
            store=DatasetStore(args.output_dir)
            try:
                asyncio.run(run_calibration(store,LiveBackend(store)))
                print(json.dumps(calibration_decision(store),indent=2))
            except BudgetExceeded:
                store.status('budget_exhausted');raise

if __name__=='__main__':main()
