"""Conservative consistency checks for evidence-grounded diagnostic claims.

These rules recognize a limited set of explicit forms. Unrecognized claims
remain pending; absence of a contradiction never proves a mechanism correct.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from scripts.offline_loop_protocol import atomic_json, file_hash, read_json

VERSION = 'diagnostic_constraints_v1'
FIELDS = ('violated_constraint', 'explanation', 'applicability', 'counterexample')


def check_analysis(case, analysis):
    checks = []
    problem, reference = case['problem'], case['reference_solution']
    claims = {k: str(analysis.get(k,'')) for k in FIELDS}

    def add(rule, status, reason, basis, field=None):
        checks.append({'rule':rule, 'status':status, 'reason':reason,
                       'basis':basis, 'claim_field':field,
                       'claim':claims.get(field) if field else None})

    # Recognize only an explicitly numeric square-root radius and a scalar
    # reference distance squared. Never execute Asymptote or parse arbitrary code.
    radius = re.search(r'radius of (?:the )?circle is\s*\$?\\sqrt\{(\d+(?:\.\d+)?)\}',problem,re.I)
    point = re.search(r'square of the distance.*?from\s*\$?([A-Z])\$?\s*to the center',problem,re.I)
    values = re.findall(r'\\boxed\{(\d+(?:\.\d+)?)\}',reference)
    if radius and point and len(values)==1:
        r2, d2 = Decimal(radius[1]), Decimal(values[0])
        position = 'inside' if d2<r2 else 'outside' if d2>r2 else 'on'
        p = point[1]
        basis = f'Reference distance²={d2}; stated radius²={r2}; {p} is {position} for this candidate.'
        # Detect positive assertions about the intended/reference configuration,
        # not a quotation of the solver error or a hypothetical counterexample.
        for field in ('violated_constraint','explanation'):
            claim = claims[field]
            forms = [rf'(?:configuration\s+(?:has|selects).*?|reference\s+(?:has|configuration\s+has)\s+){p}\s+(?:is\s+)?(inside|outside)',
                     rf'selects the root with\s+{p}\s+(?:is\s+)?(inside|outside)']
            matches = [m for pattern in forms for m in re.finditer(pattern,claim,re.I)]
            for m in matches:
                actual = m[1].lower()
                add('circle_position', 'contradiction' if actual!=position else 'consistent',
                    f'Claim says {actual}; numeric relation says {position}.',basis,field)
        counter = claims['counterexample']
        # Concrete logical contradiction: inside configuration rejects a
        # specified candidate whose squared distance is less than radius².
        rejected = re.search(r'if.*?inside the circle.*?(?:root|candidate).*?(?:OB|BO)[²^]*2?\s*=\s*(\d+(?:\.\d+)?).*?(?:reject|discard)',counter,re.I)
        if rejected and Decimal(rejected[1])<r2:
            add('circle_counterexample','contradiction',
                'Counterexample rejects an inside candidate because the point is inside.',basis,'counterexample')
        if not any(c['rule'].startswith('circle') for c in checks):
            add('circle_position','review_required','Circle facts recognized, but claim wording is outside supported assertion forms.',basis)

    # Reference explicitly states collinearity through a straight angle.
    straight = re.findall(r'\\angle\s*([A-Z]{3})\$?\s+is a straight angle',reference)
    for triple in straight:
        a, vertex, b = triple
        for field in ('violated_constraint','explanation'):
            claim = claims[field]
            rays = re.search(rf'\bx\b.*?between\s+(?:{vertex}{a}\s+and\s+{vertex}{b}|{vertex}{b}\s+and\s+{vertex}{a})\b',claim,re.I)
            if rays:
                add('target_angle_rays','contradiction',
                    f'Claim binds x to opposite rays {vertex}{a}/{vertex}{b} (180°).',
                    f'Reference explicitly states angle {triple} is straight; this is not the boxed target angle.',field)

    # Only apply segment checks when reference actually contains two triangle
    # similarities with the same target segment. Do not assume every segment
    # through a diagonal intersection is half the full transversal.
    target = re.search(r'length of\s*\$([A-Z]{2})\$',problem)
    if target and len(re.findall(r'\\sim',reference))>=2:
        segment = target[1]
        for field in ('violated_constraint','explanation'):
            claim = claims[field]
            normalized = re.sub(r'\s+','',claim).lower()
            if 'harmonicmean1/(1/' in normalized or 'equalsharmonicmean1/(1/' in normalized:
                add('mean_definition','contradiction',
                    '1/(1/a+1/b) is half the two-value harmonic mean, not the harmonic mean.',
                    'For positive a,b, harmonic mean=2ab/(a+b); reciprocal sum=ab/(a+b).',field)
            if 'correct intersection height' in claim.lower():
                add('segment_endpoint','review_required',
                    'Height correction is not established: verify the starting endpoint before changing height.',
                    f'Target {segment} and similarity relations specify endpoints; quoted coordinates must be checked.',field)
        scope = claims['applicability'].lower()
        if 'intersection of diagonals' in scope and not any(word in scope for word in ('endpoint','one leg','one side','from the intersection','starts at')):
            add('segment_scope','review_required',
                'Applicability does not specify whether the segment starts at the diagonal intersection or crosses both legs.',
                'Half-segment and full transversal have different lengths.','applicability')

    statuses={c['status'] for c in checks}
    status = 'contradiction' if 'contradiction' in statuses else 'needs_review' if 'review_required' in statuses else 'limited_checks_consistent' if checks else 'not_covered'
    return {'checker_version':VERSION,'status':status,'checks':checks,
            'semantic_verified':False,'requires_semantic_review':True}


def audit_directory(source, output):
    # Import here to avoid a cycle: induction uses check_analysis at validation.
    from scripts.failure_mechanism_induction import validate_response
    source,output=source.resolve(),output.resolve()
    if output.exists(): raise FileExistsError(output)
    if source==output or source in output.parents:
        raise ValueError('Audit output must be outside source directory')
    before={str(p.relative_to(source)):file_hash(p) for p in source.rglob('*') if p.is_file()}
    manifest=read_json(source/'manifest.json')
    if file_hash(source/'packet.json')!=manifest['packet_sha256']:
        raise ValueError('Frozen packet changed')
    packet,response=read_json(source/'packet.json'),read_json(source/'response.json')
    result=validate_response(packet,response,manifest['config']['min_stable_problems'])
    counts=Counter(a['constraint_review']['status'] for a in result['accepted_analyses'])
    lines=['# 诊断约束一致性离线检查','',
           '检查只针对已识别的形式，不是完整数学验证器。引用通过与语义可信分开记录；未覆盖的分析继续待审。','',
           '|题目|运行|检查结果|原因|','|---|---:|---|---|']
    for a in result['accepted_analyses']:
        review=a['constraint_review']
        reasons='；'.join(c['reason'] for c in review['checks']) or 'No supported constraint form recognized.'
        lines.append(f'|{a["problem_id"]}|{a["run"]}|{review["status"]}|{reasons}|')
    report={'checker_version':VERSION,'source':str(source),'source_hashes':before,
            'model_calls_made':0,'counts':dict(counts),'validated_result':result}
    after={str(p.relative_to(source)):file_hash(p) for p in source.rglob('*') if p.is_file()}
    if before!=after: raise RuntimeError('Source changed during audit')
    report['source_unchanged']=True
    output.mkdir(parents=True)
    atomic_json(output/'audit.json',report)
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'output':str(output),'counts':dict(counts),'model_calls_made':0},ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir',required=True,type=Path)
    p.add_argument('--output-dir',required=True,type=Path)
    args=p.parse_args()
    audit_directory(args.input_dir,args.output_dir)


if __name__=='__main__': main()
