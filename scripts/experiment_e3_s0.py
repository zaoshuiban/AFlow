"""E3-S0: read-only historical evidence audit and prefix-only replay.

Never executes historical programs or calls a model. Missing wall-clock times
remain missing; deterministic inventory order is not called chronology.
"""
from __future__ import annotations
import argparse
import ast
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def runtime_hash(value):
    return sha(json.dumps(value, sort_keys=True, default=str))


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc).isoformat() if dt.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def classify(result, expected, issues, scope):
    if issues:
        return 'UNTRUSTED', False
    error = str(result.get('error', ''))
    if scope in ('subproblem', 'exploration', 'approximation'):
        return 'EXPLORATION', False
    if error.startswith('AssertionError'):
        return 'ASSERTION_PROBE_UNKNOWN', False
    if result.get('status') == 'success' and type(result.get('value')) is int:
        if type(expected) is not int:
            return 'REFERENCE_UNKNOWN', False
        return ('CORRECT', False) if result['value'] == expected else ('WRONG_RESULT', True)
    if result.get('status') == 'declined':
        return 'DECLINED', False
    if result.get('status') == 'error':
        if any(s in error.lower() for s in ('worker_exit', 'timeout', 'memory', 'rss_limit')):
            return 'RESOURCE_OR_PROCESS_UNKNOWN', False
        kind = result.get('error_type')
        if kind in ('INFRASTRUCTURE_ERROR', 'TOOL_INTEGRATION_ERROR'):
            return kind, False
        return kind or 'RUNTIME_ERROR_OBSERVED', True
    return 'UNKNOWN', False


def replay(events):
    """Equal timestamps are a batch: no arbitrary within-tie learning."""
    timed = sorted((e for e in events if e['timestamp']),
                   key=lambda e: (e['timestamp'], e['event_id']))
    groups = defaultdict(list)
    for e in timed:
        groups[e['timestamp']].append(e)
    support = defaultdict(set)
    curves = []; candidates = {}; failures = 0; seen = set()
    for time, batch in sorted(groups.items()):
        for e in batch:
            e['prior_distinct_failure_tasks'] = len(support[e['candidate_key']])
        for e in batch:
            seen.add(e['task_id'])
            if not e['observed_failure']:
                continue
            failures += 1
            key = e['candidate_key']; support[key].add(e['task_id'])
            if key not in candidates:
                candidates[key] = dict(candidate_id=key, first_observed_at=time,
                    evidence_event_ids=[], support_task_ids=[], eligible_for_generation=False,
                    status='OBSERVATIONAL_BUCKET_REQUIRES_SEMANTIC_AND_CAUSAL_REVIEW')
            candidates[key]['evidence_event_ids'].append(e['event_id'])
            candidates[key]['support_task_ids'] = sorted(support[key])
            if len(support[key]) >= 3:
                candidates[key].setdefault('three_task_support_at', time)
        curves.append(dict(timestamp=time, cumulative_events=sum(len(x) for t,x in groups.items() if t <= time),
            cumulative_failure_events=failures, cumulative_tasks=len(seen),
            candidate_buckets=len(candidates),
            buckets_with_three_tasks=sum(len(v) >= 3 for v in support.values())))
    return timed, curves, list(candidates.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path('results'))
    parser.add_argument('--output', type=Path, default=Path('results/experiment_e3_s0_20261020_v1'))
    args = parser.parse_args(); out = args.output
    if (out / 'summary.json').exists():
        raise FileExistsError('Use a new output directory; completed evidence is immutable')
    out.mkdir(parents=True, exist_ok=True)
    hashes = {}; inventory = []; exclusions = []; events = []; programs = {}; conflicts = []
    def load(p):
        raw = p.read_bytes(); hashes[str(p)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    dirs = sorted(p for p in args.source.rglob('observations')
        if p.is_dir() and not any('frozen' in s for s in p.parts)
        and out.resolve() not in p.resolve().parents)
    for directory in dirs:
        rows = []
        for p in sorted(directory.glob('*.json')):
            try:
                r = load(p)
            except (ValueError, OSError) as exc:
                exclusions.append(dict(path=str(p), reason=type(exc).__name__)); continue
            if not isinstance(r, dict) or not isinstance(r.get('events'), list):
                exclusions.append(dict(path=str(p), reason='unsupported_schema')); continue
            if not any(e.get('function', e.get('tool')) == 'python' for e in r['events']):
                exclusions.append(dict(path=str(p), reason='no_native_python_events')); continue
            rows.append((p, r))
        if not rows:
            continue
        # Recover actual source from native response tool calls, matched by
        # hash and logical task/arm; source match alone is not execution proof.
        source_index = defaultdict(list)
        for p in sorted((directory.parent / 'api_attempts').glob('*.json')):
            r = load(p)
            for call in r.get('raw_response', {}).get('message', {}).get('tool_calls', []):
                fn = call.get('function', {})
                if fn.get('name') != 'python': continue
                try: code = json.loads(fn['arguments'])['code']
                except (ValueError, KeyError, TypeError): continue
                if isinstance(code, str):
                    source_index[runtime_hash(code)].append((code, str(p), r.get('logical_key', '')))
        before = len(events)
        for p, r in rows:
            task = str(r.get('id', r.get('task_id'))); arm = str(r.get('arm', r.get('run_id', '')))
            trajectory = sha(str(p))[:24]; previous = None
            for index, e in enumerate(r['events']):
                if e.get('function', e.get('tool')) != 'python': continue
                t = e.get('tool_result', e.get('result', {})) or {}; tr = t.get('trace', {})
                code = tr.get('code') or e.get('arguments', {}).get('code')
                source_path = str(p); recovered = False
                if not code and tr.get('code_sha256'):
                    matches = [x for x in source_index[tr['code_sha256']]
                               if f'/{task}/{arm}/' in x[2]]
                    if matches:
                        code, source_path, _ = matches[0]; recovered = True
                issues = []
                if code and tr.get('code_sha256') and tr['code_sha256'] != runtime_hash(code):
                    issues.append('CODE_HASH_MISMATCH')
                time = timestamp(e.get('timestamp'))
                if time and previous and time < previous: issues.append('TRAJECTORY_TIME_REVERSAL')
                if time: previous = time
                if code:
                    h = sha(code); programs.setdefault(h, code)
                    try: ast.parse(code); syntax = True
                    except SyntaxError: syntax = False
                else: h = None; syntax = None
                label, failure = classify(t, r.get('expected'), issues, e.get('declared_scope'))
                # Error family is an observable bucket, never a causal mechanism.
                key = label if label != 'WRONG_RESULT' else 'WRONG_RESULT_CAUSE_UNKNOWN'
                event = dict(event_id=sha(f'{p}:{index}')[:24], trajectory_id=trajectory,
                    task_id=task, arm=arm, source_path=str(p), source_sha256=hashes[str(p)],
                    step_index=index, timestamp=time, chronology='NATIVE_TIMESTAMP' if time else 'WITHIN_TRAJECTORY_ONLY',
                    program_sha256=h, declared_code_hash=tr.get('code_sha256'),
                    code_source_path=source_path if code else None, source_recovered=recovered,
                    syntax_valid=syntax, issues=issues, status=t.get('status'), error=t.get('error'),
                    value=t.get('value'), expected=r.get('expected'), label=label,
                    reference_status='HISTORICAL_EXPECTED_NOT_INDEPENDENTLY_RECOMPUTED',
                    observed_failure=failure, candidate_key=key,
                    online_observable=label != 'WRONG_RESULT',
                    correctness_feedback_time=None,
                    final_task_correct=r.get('correct'),
                    final_outcome_used_for_prefix=False)
                events.append(event)
        inventory.append(dict(directory=str(directory), trajectories=len(rows), events=len(events)-before))
    # Conflict detection is evaluator-only. It never supplies online feedback.
    refs = defaultdict(set)
    for e in events:
        if type(e['expected']) is int: refs[e['task_id']].add(e['expected'])
    bad_tasks = {k for k,v in refs.items() if len(v)>1}
    for task in sorted(bad_tasks): conflicts.append(dict(task_id=task, expected_values=sorted(refs[task])))
    for e in events:
        if e['task_id'] in bad_tasks:
            e['issues'].append('REFERENCE_CONFLICT'); e['observed_failure']=False; e['label']='UNTRUSTED'
    timed, curve, candidates = replay(events)
    # A wrong full-task value is known only to the retrospective evaluator.
    # Do not place it into the online accumulation without feedback timestamps.
    online = [dict(e, observed_failure=e['observed_failure'] and e['online_observable']) for e in events]
    _, online_curve, online_candidates = replay(online)
    for e in events:
        if e['issues']: conflicts.append(dict(event_id=e['event_id'], issues=e['issues']))
    summary = dict(experiment='E3-S0', status='PROVISIONAL_SCOPE_AND_CHRONOLOGY_GAPS',
        requested_tasks=800, requested_programs=2132, inventory=inventory,
        unique_task_ids=len({e['task_id'] for e in events}),
        trajectories=len({e['trajectory_id'] for e in events}), program_execution_events=len(events),
        unique_source_programs=len(programs), source_missing=sum(not e['program_sha256'] for e in events),
        timestamped_events=len(timed), untimestamped_events=len(events)-len(timed),
        observed_failures=sum(e['observed_failure'] for e in events),
        labels=dict(Counter(e['label'] for e in events)),
        candidate_buckets=len(candidates), online_candidate_buckets=len(online_candidates),
        input_files=len(hashes), excluded_observation_files=len(exclusions),
        reference_conflict_tasks=len(bad_tasks),
        source_integrity_issues=sum(bool(e['issues']) for e in events),
        new_model_calls=0, historical_program_executions=0,
        scope_matches_requested=(len({e['task_id'] for e in events})==800 and len(events)==2132),
        limitations=['No authoritative 800-task/2132-program source manifest located',
          'Task IDs are identities in historical records, not semantic deduplication',
          'Observation-only inventory excludes unsupported schemas and non-Python tools',
          'Untimestamped records replay only within trajectory; no invented global ordering',
          'Historical references retained, not independently recomputed',
          'Wrong-result labels are evaluator-only, no online availability timestamp',
          'Candidate buckets are error families, not verified stable or causal mechanisms',
          'Timestamped subset is selected by logging support, not an unbiased online sample'])
    with (out/'failure_events.jsonl').open('w') as f:
        for e in events: f.write(json.dumps(e,ensure_ascii=False)+'\n')
    with (out/'chronological_replay.jsonl').open('w') as f:
        for e in timed: f.write(json.dumps(e,ensure_ascii=False)+'\n')
    with (out/'trajectory_replay.jsonl').open('w') as f:
        for e in sorted(events,key=lambda e:(e['trajectory_id'],e['step_index'])):
            f.write(json.dumps(e,ensure_ascii=False)+'\n')
    for name, rows in [('failure_accumulation',curve),('online_failure_accumulation',online_curve)]:
        with (out/(name+'.csv')).open('w') as f:
            fields=['timestamp','cumulative_events','cumulative_failure_events','cumulative_tasks','candidate_buckets','buckets_with_three_tasks']
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    write(out/'mechanism_candidates.json', candidates)
    write(out/'online_mechanism_candidates.json', online_candidates)
    write(out/'trust_issues.json', conflicts); write(out/'excluded_records.json',exclusions)
    write(out/'source_hashes.json', hashes)
    (out/'programs').mkdir(exist_ok=True)
    for h,code in programs.items(): (out/'programs'/f'{h}.py').write_text(code)
    changed=[p for p,h in hashes.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
    summary['historical_files_changed']=changed
    write(out/'summary.json',summary)
    report=['# E3-S0 历史失败事件流与时间回放','',
      '**状态：完成可定位历史记录的离线审计与回放；目标清单、全局时间和机制可信性尚未通过验收。**','',
      f"目标为800题、2,132份程序。实际盘点：{summary['unique_task_ids']}个题目ID、{summary['trajectories']}条轨迹、{len(events)}次程序执行事件、{len(programs)}份按源码去重的程序。不能将题目、轨迹、执行次数和唯一源码混为同一分母。",'',
      f"有原生时戳{len(timed)}条；缺时戳{len(events)-len(timed)}条只保留轨迹内顺序。未使用文件mtime推断历史时间。同时间事件按批处理，批内不能相互提供先验证据。",'',
      f"历史标签观察失败{summary['observed_failures']}条；输入或引用可信性问题{summary['source_integrity_issues']}条；缺源码{summary['source_missing']}条。所有参考值保留历史身份，未执行新程序或调用模型。",'',
      '## 在线可见性','',
      '在线曲线仅积累当时工具返回的可观察执行错误。错误整数需要参考答案才能识别，因缺少正确性反馈到达时间，仅进入回顾性评价曲线；最终成功/失败不参与前缀决策。断言探测、资源终止和基础设施问题单独保留。','',
      '## 曲线','',
      '![在线失败积累曲线](online_failure_accumulation.svg)','',
      '## 机制候选库','',
      '候选库按可观察错误类型归档，保留首现时间、支持题目和事件ID。三题支持只是待审计线索，不证明同一根因、跨参数复现或稳定能力缺口。所有候选均禁止直接授权生成和路由。未带时戳记录保留在完整事件流中，不参与首现时间推断。','',
      '## 验收与限制','']
    report += ['- '+s for s in summary['limitations']]
    report += ['',f"历史源文件前后哈希变化：{len(changed)}。完整统计见summary.json，来源见source_hashes.json；可信性问题见trust_issues.json。",'',
      '复现：`python -m scripts.experiment_e3_s0 --output <新的输出目录>`。仅读取历史记录，输出单独目录。']
    (out/'report.md').write_text('\n'.join(report)+'\n')
    # Standalone SVG, no plotting dependency and no external resources.
    for filename,rows,title in [('online_failure_accumulation',online_curve,'Online-observable failure accumulation'),('failure_accumulation',curve,'Retrospective evaluator failure accumulation')]:
        n=max(1,len(rows)); peak=max([r['cumulative_failure_events'] for r in rows]+[1])
        points=' '.join(f'{60+800*i/max(1,n-1):.2f},{330-270*r["cumulative_failure_events"]/peak:.2f}' for i,r in enumerate(rows))
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="940" height="420" viewBox="0 0 940 420"><rect width="940" height="420" fill="white"/><g font-family="sans-serif" fill="#172b4d"><text x="45" y="30" font-size="19">{title}</text><path d="M60 55V330H865" fill="none" stroke="#53657a"/><polyline points="{points}" fill="none" stroke="#167d99" stroke-width="2"/><text x="15" y="65">{peak}</text><text x="25" y="334">0</text><text x="60" y="365">Native timestamp batches (ordered); missing timestamps excluded</text><text x="60" y="393">Retrospective replay; no deployment or causal mechanism claim. Batches: {len(rows)}</text></g></svg>'
        (out/(filename+'.svg')).write_text(svg)
    write(out/'artifact_hashes.json',{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file() and p.name!='artifact_hashes.json'})
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
