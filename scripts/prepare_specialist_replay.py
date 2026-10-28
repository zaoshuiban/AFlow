"""Export known specialist tasks for a disclosed replay; never import old answers as new calls.

The optional --freeze prepares NEW baselines with both installed specialists hidden.
No model client is created. Original experiments and production library are read-only.
"""
import argparse
import collections
import json
from dataclasses import asdict
from pathlib import Path

from benchmarks.math import MATHBenchmark
from scripts.independent_mechanism_evidence import prepare
from scripts.mechanism_scope import matches
from scripts.offline_loop_protocol import REPO_ROOT, LoopConfig, atomic_json, file_hash
from scripts.operator_library import stable_problem_id

FAMILIES = {
    'parenthesization': ('parenthesization_distinct_value_dedup', 'parenthesization_hard_confirm_seed99007',
                         ['Fully parenthesize', 'same numerical value count only once']),
    'restricted_prime': ('restricted_digit_prime_enumeration', 'restricted_digit_prime_hard_confirm_seed130003',
                         ['without repetition', 'a leading zero is not allowed', 'How many distinct prime numbers']),
}


def export(root, *, freeze=False):
    root = Path(root).resolve()
    if root.exists(): raise FileExistsError(root)
    scorer = MATHBenchmark('MATH', '', '')
    sources, output = {}, {}
    def read(path):
        sources[str(path)] = file_hash(path)
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for short, (mechanism, folder, terms) in FAMILIES.items():
        path = REPO_ROOT/'workspace/MATH/operational_headroom'/folder/'baseline_runs_deepseek_v4_flash/observations.jsonl'
        observations = read(path)
        rows, counts, wrong = {}, collections.Counter(), collections.Counter()
        for o in observations:
            pid = stable_problem_id(o['problem'])
            rows.setdefault(pid, {'problem': o['problem'], 'solution': o['reference_solution'],
                                  'source_id': 'historical-' + pid,
                                  'historical_origin': str(path), 'source_kind': 'constructed_specialist_replay'})
            if o['error']:
                counts['execution_errors'] += 1
            else:
                score, _ = scorer.calculate_score(o['reference_solution'], o['prediction'], problem=o['problem'])
                counts['completed_correct' if score else 'completed_wrong'] += 1
                if not score: wrong[pid] += 1
        controls = {}
        for p in sorted((REPO_ROOT/'workspace/MATH/router_end_to_end').glob('two_operator_joint_hidden*/mixed_hidden.jsonl')):
            for row in read(p):
                md = row['mechanism_metadata']
                if md.get('near_miss_for_family') != mechanism: continue
                pid = stable_problem_id(row['problem'])
                controls.setdefault(pid, {'problem': row['problem'], 'solution': row['solution'],
                                          'source_id': 'historical-' + pid, 'historical_origin': str(p),
                                          'source_kind': 'constructed_specialist_replay_boundary'})
        protocol = {'scope': {'mechanism_id': mechanism, 'required_terms': terms, 'forbidden_terms': []},
                    'hypothesis': {'claim': 'Repeated completed wrong answers may indicate incomplete constrained counting.',
                                   'violated_constraint': 'Count exactly the distinct legal results under all stated constraints.',
                                   'expected_intervention': 'Generate a reusable prompt strategy that organizes exhaustive counting and checks duplicates and boundaries.',
                                   'applicable_when': 'Exact constrained counting in the frozen literal question scope.',
                                   'not_applicable_when': 'Different counting object, repeated digits allowed, extremum queries, or outside the frozen scope.',
                                   'success_criteria': 'More correct completed answers within the frozen budget, without boundary regression.'},
                    'program_verifier': 'none'}
        targets = sorted(rows.values(), key=lambda r: stable_problem_id(r['problem']))
        boundary = sorted(controls.values(), key=lambda r: stable_problem_id(r['problem']))
        assert len(targets) >= 9 and len(boundary) >= 8
        assert all(matches(protocol['scope'], r['problem']) for r in targets)
        assert all(not matches(protocol['scope'], r['problem']) for r in boundary)
        # Deterministic identity order, not choosing completed wrong answers.
        pools = {'evidence': targets[:3]+boundary[:2],
                 'design': targets[3:6]+boundary[2:5], 'gate': targets[6:9], 'regression': boundary[5:8]}
        output[short] = {'protocol': protocol, 'pools': pools,
                         'historical_audit': {'observations': len(observations), 'different_problems': len(rows),
                                              **dict(counts), 'stable_completed_wrong_problems': sum(n >= 2 for n in wrong.values()),
                                              'scorer_version': scorer.SCORER_VERSION},
                         'selection': 'Stable text ID ordering; 3+2 evidence, 3+3 design, 3 gate, 3 regression; all known constructed replay tasks'}
    # Complete validation before writing any preparation artifacts.
    root.mkdir(parents=True)
    config = LoopConfig(opt_model='deepseek-v4-flash', exec_model='deepseek-v4-flash',
                        seed=170301, candidate_count=2, max_requests=35,
                        max_output_tokens=8192, thinking_mode='disabled')
    for short, entry in output.items():
        directory = root/short
        directory.mkdir()
        for role, rows in entry['pools'].items():
            (directory/(role+'.jsonl')).write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))
        atomic_json(directory/'protocol.json', entry['protocol'])
        atomic_json(directory/'config.json', asdict(config))
        if freeze:
            prepare(root=directory/'evidence_run', data=directory/'evidence.jsonl', protocol=entry['protocol'],
                    source_name='Known constructed specialist tasks; historical replay only, not independent natural evidence',
                    config=config, target_count=3, control_count=2, evidence_mode='historical_replay')
    if sources != {p: file_hash(Path(p)) for p in sources}:
        raise ValueError('Historical source changed during replay export')
    summary = {'mode': 'historical_replay', 'historical_audit': {k:v['historical_audit'] for k,v in output.items()},
               'source_hashes': sources, 'source_files_unchanged': True, 'new_model_calls': 0,
               'baseline_frozen': freeze, 'ready_for_semantic_generation': False,
               'generator': 'New prompt-only fixed skeleton; old DP/prime tool implementations are not supplied',
               'limitation': 'Timeouts are not mathematical error evidence; existing history does not pass semantic support gate.',
               'next': 'Collect new baseline under the frozen setting; if support remains insufficient, archive rather than bypass review.'}
    atomic_json(root/'report.json', summary)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--freeze', action='store_true')
    args = p.parse_args()
    summary = export(args.output_dir, freeze=args.freeze)
    print(json.dumps({k:v for k,v in summary.items() if k!='source_hashes'}, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
