"""Offline evidence triage; no API calls, arbitrary code or automatic promotion.

Input JSON: scope, discovery_ids, rows, observations, packet, response.
Scope is frozen BEFORE selecting independent evidence. Conditions operate only
on problem text. Statistical repeatability is not semantic mechanism support.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.failure_mechanism_induction import validate_response
from scripts.offline_loop_protocol import atomic_json, digest, file_hash, read_json
from scripts.operator_library import stable_problem_id


def validate_scope(scope):
    if set(scope) != {'mechanism_id', 'required_terms', 'forbidden_terms'}:
        raise ValueError('Scope requires mechanism_id, required_terms, forbidden_terms only')
    if not isinstance(scope['mechanism_id'], str) or not scope['mechanism_id'].strip():
        raise ValueError('Empty mechanism_id')
    for key in ('required_terms', 'forbidden_terms'):
        terms = scope[key]
        if not isinstance(terms, list) or any(not isinstance(t, str) or not t.strip() for t in terms):
            raise ValueError('Terms must be nonempty strings')
        if len(terms) != len(set(terms)):
            raise ValueError('Duplicate terms')
    if not scope['required_terms']:
        raise ValueError('At least one input condition is required')
    if set(scope['required_terms']) & set(scope['forbidden_terms']):
        raise ValueError('Contradictory scope')


def matches(scope, problem):
    # Case-sensitive: mathematical variables A and a must not be collapsed.
    return (all(t in problem for t in scope['required_terms'])
            and not any(t in problem for t in scope['forbidden_terms']))


def evaluate(payload, *, min_problems=3, min_wrong_repeats=2, min_error_rate=.20):
    scope = payload['scope']
    validate_scope(scope)
    if min_problems < 2 or min_wrong_repeats < 2 or not 0 < min_error_rate <= 1:
        raise ValueError('Invalid evidence thresholds')
    # This digest must be saved by preparation before outcomes become available.
    if payload['frozen_scope_sha256'] != digest(scope):
        raise ValueError('Frozen scope changed')
    rows = payload['rows']
    by_id = {r['problem_id']: r for r in rows}
    if len(by_id) != len(rows) or any(stable_problem_id(r['problem']) != r['problem_id'] for r in rows):
        raise ValueError('Duplicate or incorrect problem IDs')
    if set(by_id) & set(payload['discovery_ids']):
        raise ValueError('Independent evidence overlaps discovery')
    seen, counts = set(), {}
    for o in payload['observations']:
        key = (o['problem_id'], o['run'])
        if key in seen or o['problem_id'] not in by_id or type(o['run']) is not int or o['run'] < 1:
            raise ValueError('Duplicate or unknown observation')
        seen.add(key)
        if not o.get('error') and o['score'] not in (0, 1):
            raise ValueError('Expected binary score')
        counts.setdefault(o['problem_id'], []).append(o)
    if set(counts) != set(by_id):
        raise ValueError('Missing problem observations')
    checked = validate_response(payload['packet'], payload['response'], min_problems)
    # Bind semantic diagnostics to the exact independently saved outputs.
    observed = {(o['problem_id'], o['run']): o for o in payload['observations']}
    for c in payload['packet']['cases']:
        if c['problem_id'] not in by_id or c['problem'] != by_id[c['problem_id']]['problem'] or c['reference_solution'] != by_id[c['problem_id']]['solution']:
            raise ValueError('Diagnostic case differs from independent input')
        for r in c['failed_runs']:
            o = observed.get((c['problem_id'], r['run']))
            if not o or o.get('error') or o['score'] != 0 or o['prediction'] != r['prediction']:
                raise ValueError('Diagnostic evidence differs from saved failed answer')
    target_ids = {pid for pid, r in by_id.items() if matches(scope, r['problem'])}
    valid = [o for pid in target_ids for o in counts[pid] if not o.get('error')]
    wrong = sum(o['score'] == 0 for o in valid)
    stable = [pid for pid in target_ids if sum(not o.get('error') and o['score'] == 0 for o in counts[pid]) >= min_wrong_repeats]
    analyses = [a for a in checked['accepted_analyses'] if a['mechanism_id'] == scope['mechanism_id']]
    contradictions = [a for a in analyses if a['constraint_review']['status'] == 'contradiction']
    # Existing checker explicitly never proves semantic validity. Do not convert
    # limited_checks_consistent into independent mechanism confirmation.
    supported = {a['problem_id'] for a in analyses if a['problem_id'] in target_ids and a['constraint_review']['semantic_verified']}
    n = len(valid)
    rate = wrong / n if n else None
    statistical = len(stable) >= min_problems and rate is not None and rate >= min_error_rate
    status = 'refuted' if contradictions else 'independently_supported' if statistical and len(supported) >= min_problems else 'insufficient_evidence'
    return {'schema_version': 1, 'status': status, 'scope_sha256': digest(scope),
            'scope_limit': 'literal input filter only; not a verified mathematical applicability contract',
            'target_problem_count': len(target_ids), 'control_problem_count': len(rows)-len(target_ids),
            'valid_observations': n, 'wrong_observations': wrong, 'observed_error_rate': rate,
            'execution_errors': sum(bool(o.get('error')) for o in payload['observations']),
            'stable_problem_ids': sorted(stable), 'statistical_threshold_met': statistical,
            'semantically_supported_problem_ids': sorted(supported),
            'contradictions': contradictions, 'diagnostic_validation': checked,
            'thresholds': {'min_problems': min_problems, 'min_wrong_repeats': min_wrong_repeats, 'min_error_rate': min_error_rate},
            'promotion_authorized': False, 'model_calls_made': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    original = file_hash(args.input)
    report = evaluate(read_json(args.input))
    if original != file_hash(args.input):
        raise RuntimeError('Input changed during evaluation')
    report['source_sha256'] = original
    args.output_dir.mkdir(parents=True)
    atomic_json(args.output_dir/'report.json', report)
    print(report['status'])


if __name__ == '__main__':
    main()
