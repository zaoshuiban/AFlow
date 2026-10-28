"""Experiment A: clean generation -> admission -> smoke -> sealed formal test.

Reuses the existing shared Agent, Reflection controller and restricted Python.
The sealed formal JSON is parsed ONLY in formal(), after both frozen gates.
"""
from __future__ import annotations
import argparse
import asyncio
import difflib
import json
import random
import shutil
import time
from pathlib import Path

from scripts.experiment_a_pilot import Transport,BudgetStop,RequestFailure,parse_json
from scripts.experiment_a_calibration import solve,execute,prompt,tools_for
from scripts.counting_worker import validate,MODULES,BUILTINS,ATTRIBUTES
from scripts.compositional_counting import SPEC,make_instance,exact_dp,brute_force
from scripts.counting_benchmark import ROOT as BENCH
from scripts.offline_loop_protocol import atomic_json,read_json,digest,file_hash,run_lock

ROOT=Path('results/generated_operator_experiment_a_v1')
FILES=('scripts/generated_operator_experiment.py','scripts/experiment_a_calibration.py',
       'scripts/experiment_a_pilot.py','scripts/counting_worker.py','scripts/frozen_math_worker.py',
       'scripts/compositional_counting.py','scripts/counting_benchmark.py','scripts/async_llm.py',
       'scripts/offline_loop_protocol.py','scripts/report_generated_operator.py')
ARMS=('baseline','reflection','operator')


def edge_cases():
    # Independent ID-compatible combinations only, lengths <= development max.
    ps=[]
    for n in (1,2,5,7,20,36):
        for cs in ([],[dict(kind='adjacent')],[dict(kind='endpoints')],
            [dict(kind='position',parity='even',forbidden_symbol=3)],
            [dict(kind='count',symbol=1,exact=0)],
            [dict(kind='pattern_count',pattern='010',exact=min(2,max(0,n-2)))],
            [dict(kind='forbidden',patterns=['0','1','2','3'])]):
            p=dict(n=n,alphabet=[0,1,2,3],constraints=cs)
            ans=exact_dp(p)['answer']
            if n<=7:assert ans==brute_force(p)
            ps.append(dict(id=digest(p)[:20],problem=json.dumps(p,sort_keys=True),expected=ans,role='boundary_property'))
    return ps


def prepare(root=ROOT,protocol=None):
    prepared_at=time.monotonic()
    root=Path(root)
    if (root/'manifest.json').exists():raise FileExistsError('Existing frozen experiment; use run to resume')
    bm=read_json(BENCH/'manifest.json')
    if file_hash(BENCH/'development.json')!=bm['development_sha256']:raise ValueError('Development altered')
    root.mkdir(parents=True,exist_ok=True)
    dev=read_json(BENCH/'development.json')
    # Train n<=7, disjoint from all sealed ID/OOD lengths (min 8).
    rng=random.Random(2610142001);train=[];seen=set()
    pools=bm['development_combination_pools']
    for i in range(30):
        tier=('Easy','Medium','Hard')[i%3];mask=pools[tier][(i//3)%10]
        while True:
            p=make_instance(rng,mask,rng.randint(1,7));pid=digest(p)
            if pid not in seen:break
        seen.add(pid);ans=brute_force(p)
        assert ans==exact_dp(p)['answer']
        train.append(dict(id=pid[:20],problem=json.dumps(p,sort_keys=True),expected=ans,role='independent_training'))
    boundaries=edge_cases()
    held={frozenset(x) for x in bm['held_out_combinations']}
    assert not any(frozenset(c['kind'] for c in json.loads(r['problem'])['constraints']) in held for r in train+boundaries)
    for name,rows in [('training',train),('development',dev),('boundary',boundaries)]:atomic_json(root/'inputs'/f'{name}.json',rows)
    atomic_json(root/'inputs/smoke.json',[dev[i] for i in (0,30,60)])
    m=dict(experiment='Generated Operator Capability Gain A v1',model_alias='deepseek-v4-flash',thinking='disabled',temperature=0,
        api_budget_cny=30,max_requests=2928,generation=dict(candidates=3,repairs_per_candidate=3,selection='first candidate passing ALL tests; stop generation immediately',
        prompt_visibility='public semantics, environment, 30 new n<=7 training examples; own validation feedback only; NO ground truth source, old Agent failures, diagnosed mechanisms or sealed tasks'),
        admission=dict(training=30,development=90,boundary=len(boundaries),required_accuracy=1.0,max_worker_cpu_seconds=5,max_worker_wall_seconds=6),
        formal=dict(total=240,arms=list(ARMS),runs=1,ID=180,OOD=60,random_interleave_seed=2610142101,concurrency=3,
            primary='Operator minus Reflection on ALL 180 ID tasks',secondary='Operator minus Baseline',
            strong_gain=dict(min_delta=.08,bootstrap_ci_lower_exceeds=0,max_operator_resource_failure_rate=.05,max_unreached_operator_noninfra_rate=.01,max_budget_violations=0),
            bootstrap_replicates=10000,mcnemar='two-sided exact',bootstrap_seed=2610142102,
            infrastructure='zero automatic retries; keep all denominators; report availability and valid-pair sensitivity separately',
            failure_categories=['constraint_understanding','algorithm_design','state_modeling','state_transition','counting','complexity','tool_call','final_submission','infrastructure','unknown']),
        online=dict(calls=4,output_tokens=8192,tool_wall_seconds=10,wall_seconds=180,concurrency=3,final_reserve_tokens=512,
            intervention='C first call forced to frozen operator; then actual Agent submit; same generic fallback if error; never substitute oracle or tool result for final answer'),
        benchmark_manifest_sha256=file_hash(BENCH/'manifest.json'),development_sha256=bm['development_sha256'],formal_seal_sha256=bm['formal_seal']['sha256'],
        input_hashes={str(p.relative_to(root)):file_hash(p) for p in (root/'inputs').glob('*.json')},
        source_hashes={p:file_hash(Path(p)) for p in FILES},
        cost_policy='Token costs are conservative estimates at input 2/output 8 CNY per million, not an invoice. Prior user-confirmed 4 CNY belongs to the earlier calibration, not this run.')
    atomic_json(root/'manifest.json',m)
    if protocol:
        shutil.copyfile(protocol,root/'user_protocol.txt')
        m['user_protocol_sha256']=file_hash(root/'user_protocol.txt');atomic_json(root/'manifest.json',m)
    atomic_json(root/'preparation.json',dict(wall_seconds=time.monotonic()-prepared_at,training_crosschecked_by_brute_and_dp=True,formal_content_loaded=False))
    for p in FILES:
        out=root/'frozen_code'/p;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(Path(p).read_bytes())
    for arm in ARMS:
        atomic_json(root/'configs'/f'{arm}.json',dict(arm=arm,system_prompt=prompt(SPEC,arm),tools=tools_for(arm),final_tools=tools_for(arm,True),budget=m['online']))
    atomic_json(root/'config_hashes.json',{str(p.relative_to(root)):file_hash(p) for p in (root/'configs').glob('*.json')})
    atomic_json(root/'state.json',dict(status='prepared',formal_started=False))
    print(json.dumps(dict(prepared=True,training=30,development=90,boundaries=len(boundaries),formal_still_sealed=True)),flush=True)


def verify(root):
    m=read_json(root/'manifest.json')
    for p,h in m['source_hashes'].items():
        if file_hash(Path(p))!=h:raise ValueError('Frozen source mismatch '+p)
    for p,h in m['input_hashes'].items():
        if file_hash(root/p)!=h:raise ValueError('Frozen input mismatch '+p)
    for p,h in read_json(root/'config_hashes.json').items():
        if file_hash(root/p)!=h:raise ValueError('Frozen config mismatch '+p)
    if file_hash(BENCH/'manifest.json')!=m['benchmark_manifest_sha256']:raise ValueError('Benchmark manifest changed')
    if file_hash(BENCH/'development.json')!=m['development_sha256']:raise ValueError('Benchmark development changed')
    return m


def generation_prompt(root):
    samples=[dict(input=json.loads(r['problem']),answer=r['expected']) for r in read_json(root/'inputs/training.json')]
    return ('Generate a reusable Python exact counting algorithm, not an answer lookup table. Return a JSON object with string fields code and rationale ONLY. '
        'Define solve(problem), where problem is the JSON STRING specified below; return an exact nonnegative Python int. '
        'No human will edit your algorithm. It must work on unseen valid instances and combinations. '
        'Allowed imports: '+','.join(sorted(MODULES))+'. Allowed builtins: '+','.join(BUILTINS)+'. Allowed attributes: '+','.join(sorted(ATTRIBUTES))+'. '
        'No files/network/processes/classes/global/nonlocal/reflection/dynamic code/private attributes. '
        'Submit imports and function definitions only, no top-level execution. Worker: 5 CPU seconds, 6 wall seconds, monitored 256 MiB RSS. '
        'Specification:\n'+SPEC+'\nIndependent training examples:\n'+json.dumps(samples))


async def validate_candidate(root,code,attempt_dir):
    path=attempt_dir/'validation.json'
    if path.exists():
        r=read_json(path)
        if r['source_sha256']!=file_hash(attempt_dir/'operator.py'):raise ValueError('Validation source changed')
        return r
    started=time.monotonic();checks=[];static_error=None
    try:validate(code)
    except (ValueError,SyntaxError) as exc:static_error=str(exc)
    if static_error is None:
        # Evaluate ALL pools; never use formal contents here.
        for pool in ('training','development','boundary'):
            for row in read_json(root/'inputs'/f'{pool}.json'):
                result=await execute(code,row['problem'],6)
                checks.append(dict(pool=pool,id=row['id'],problem=row['problem'],expected=row['expected'],result=result,
                    passed=result['status']=='success' and result['value']==row['expected']))
    report=dict(source_sha256=file_hash(attempt_dir/'operator.py'),static_error=static_error,checks=checks,
        passed=static_error is None and bool(checks) and all(c['passed'] for c in checks),seconds=time.monotonic()-started,
        pool_counts={p:dict(n=sum(c['pool']==p for c in checks),correct=sum(c['pool']==p and c['passed'] for c in checks)) for p in ('training','development','boundary')})
    atomic_json(path,report);return report


async def generate(root,t):
    frozen=root/'operator/freeze.json'
    if frozen.exists():
        f=read_json(frozen)
        if file_hash(root/'operator/solve.py')!=f['sha256']:raise ValueError('Frozen operator modified')
        return True
    base=generation_prompt(root)
    for candidate in range(1,4):
        messages=[dict(role='user',content=base+f'\nCandidate {candidate} of at most 3. This is an independent initial attempt.')]
        old_code=''
        for repair in range(4):
            d=root/'candidates'/f'candidate_{candidate}'/f'attempt_{repair}';d.mkdir(parents=True,exist_ok=True)
            genpath=d/'generation.json'
            if genpath.exists():g=read_json(genpath)
            else:
                try:
                    response=await t.call(f'generation/{candidate}/{repair}',messages,8192)
                    v=parse_json(response['response'])
                    if set(v)!={'code','rationale'} or not all(isinstance(x,str) for x in v.values()):raise ValueError('Expected code/rationale strings')
                    g=dict(**v,raw_response=response['response'],candidate=candidate,repair=repair)
                except BudgetStop:raise
                except Exception as exc:g=dict(code='',rationale='',error=type(exc).__name__+': '+str(exc)[:400],candidate=candidate,repair=repair)
                atomic_json(genpath,g)
                (d/'operator.py').write_text(g['code'])
                (d/'change.diff').write_text(''.join(difflib.unified_diff(old_code.splitlines(True),g['code'].splitlines(True),fromfile='previous',tofile='generated')))
            v=await validate_candidate(root,g['code'],d)
            print(json.dumps(dict(phase='generation',candidate=candidate,repair=repair,passed=v['passed'],pools=v['pool_counts'],static_error=v['static_error'])),flush=True)
            if v['passed']:
                dest=root/'operator';dest.mkdir(exist_ok=True)
                (dest/'solve.py').write_text(g['code'])
                f=dict(candidate=candidate,repair=repair,sha256=file_hash(dest/'solve.py'),trace_code_digest=digest(g['code']),
                    source_attempt=str(d.relative_to(root)),validation_sha256=file_hash(d/'validation.json'),
                    selected_before_formal=True,manual_source_edits=False,validation=v['pool_counts'],build_usage=t.usage())
                atomic_json(dest/'freeze.json',f);atomic_json(root/'state.json',dict(status='operator_frozen',formal_started=False));return True
            # Feedback is exclusively from THIS candidate's own admission checks.
            failures=[dict(pool=c['pool'],input=json.loads(c['problem']),expected=c['expected'],actual=c['result'].get('value'),error=c['result'].get('error')) for c in v['checks'] if not c['passed']][:5]
            feedback=dict(static_error=v['static_error'],generation_error=g.get('error'),failures=failures,summary=v['pool_counts'])
            messages.extend([dict(role='assistant',content=g.get('raw_response',json.dumps(dict(code=g['code'],rationale=g['rationale'])))),
                dict(role='user',content='Automatically repair your own source to satisfy the original specification. Return the FULL replacement code/rationale JSON, no markdown. Validation feedback:\n'+json.dumps(feedback))])
            old_code=g['code']
    atomic_json(root/'state.json',dict(status='generation_failed',formal_started=False))
    return False


async def trial(root,t,row,arm,code,stage):
    dest=root/'observations'/f"{row['id']}_{arm}.json"
    if dest.exists():return read_json(dest)
    before=time.monotonic()
    try:
        r=await asyncio.wait_for(solve(root,t,row,arm,SPEC,code),180)
    except BudgetStop:raise
    except Exception as exc:
        rs=[read_json(p) for p in root.glob('requests/*.json') if read_json(p)['key'].startswith(f"pilot/{row['id']}/{arm}/")]
        r=dict(id=row['id'],arm=arm,tier=row['tier'],scale=row['scale'],expected=row['expected'],answer=None,submitted=False,correct=False,
            error='global_wall_timeout' if isinstance(exc,asyncio.TimeoutError) else 'runner_exception:'+type(exc).__name__,calls=len(rs),tools=None,events=[],
            input_tokens=sum((r.get('usage') or {}).get('input_tokens',0) for r in rs),output_tokens=sum((r.get('usage') or {}).get('output_tokens',0) for r in rs),
            unknown_usage=sum(r.get('usage') is None for r in rs),seconds=time.monotonic()-before,tool_seconds=None,budget_violation=False,
            partial_trace_source='raw request messages/responses retained; cancellation does not fabricate an answer')
    r.update(stage=stage,ood=row.get('ood'),constraint_count=len(json.loads(row['problem'])['constraints']),
             sequence_length=json.loads(row['problem'])['n'],end_to_end_wall_seconds=time.monotonic()-before)
    if r['end_to_end_wall_seconds']>180:
        r.update(correct=False,error='global_wall_timeout')
    atomic_json(dest,r);return r


def observations(root,stage):
    return [read_json(p) for p in root.glob('observations/*.json') if read_json(p).get('stage')==stage]


async def smoke(root,t):
    path=root/'smoke_gate.json'
    if path.exists():return read_json(path)['passed']
    code=(root/'operator/solve.py').read_text();checks=[]
    for row in read_json(root/'inputs/smoke.json'):
        for arm in ARMS:
            r=await trial(root,t,row,arm,code,'smoke')
            op_events=[e for e in r['events'] if e['function']=='operator']
            passed=r['submitted'] and not r['budget_violation']
            if arm=='operator':passed=passed and len(op_events)==1 and op_events[0]['tool_result']['status']=='success' and op_events[0]['tool_result']['trace']['code_sha256']==digest(code)
            checks.append(dict(id=row['id'],arm=arm,passed=bool(passed),correct=r['correct'],error=r['error']))
    gate=dict(passed=all(c['passed'] for c in checks),checks=checks,operator_sha256=file_hash(root/'operator/solve.py'),config_hashes=read_json(root/'config_hashes.json'))
    atomic_json(path,gate)
    atomic_json(root/'state.json',dict(status='smoke_passed' if gate['passed'] else 'smoke_failed',formal_started=False))
    return gate['passed']


async def formal(root,t):
    if not read_json(root/'smoke_gate.json')['passed']:raise ValueError('Smoke gate required')
    freeze=read_json(root/'operator/freeze.json');m=verify(root)
    if file_hash(root/'operator/solve.py')!=freeze['sha256']:raise ValueError('Operator changed')
    # No formal JSON parsing exists anywhere before this point.
    path=BENCH/'sealed/formal.json'
    if file_hash(path)!=m['formal_seal_sha256']:raise ValueError('Formal seal changed')
    if not (root/'formal_start.json').exists():
        atomic_json(root/'formal_start.json',dict(operator_sha256=freeze['sha256'],config_hashes=read_json(root/'config_hashes.json'),
            smoke_gate_sha256=file_hash(root/'smoke_gate.json'),manifest_sha256=file_hash(root/'manifest.json'),formal_seal_sha256=m['formal_seal_sha256'],
            protocol_fixed_before_formal=True,formal_started=True))
    rows=read_json(path)
    if len(rows)!=240 or len({r['id'] for r in rows})!=240:raise ValueError('Unexpected formal grid')
    atomic_json(root/'inputs/formal_unsealed.json',rows)
    schedule=[(r,a) for r in rows for a in ARMS];random.Random(m['formal']['random_interleave_seed']).shuffle(schedule)
    code=(root/'operator/solve.py').read_text();sem=asyncio.Semaphore(3)
    async def one(row,arm):
        async with sem:
            await trial(root,t,row,arm,code,'formal')
            completed=len(observations(root,'formal'))
            atomic_json(root/'progress.json',dict(formal_completed=completed,planned=720,usage=t.usage()))
    await asyncio.gather(*(one(r,a) for r,a in schedule))
    atomic_json(root/'state.json',dict(status='formal_complete',formal_started=True,observations=720))


async def run(root):
    verify(root);t=Transport(root)
    try:
        if not await generate(root,t):return
        if not await smoke(root,t):return
        await formal(root,t)
    except BudgetStop:
        atomic_json(root/'state.json',dict(status='budget_stopped',formal_started=(root/'formal_start.json').exists()))
    finally:
        atomic_json(root/'usage.json',t.usage());await t.client.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('prepare','run'));p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--protocol',type=Path);p.add_argument('--allow-api',action='store_true');a=p.parse_args()
    if a.command=='prepare':prepare(a.root,a.protocol)
    else:
        if not a.allow_api:p.error('--allow-api required')
        with run_lock(a.root):asyncio.run(run(a.root))


if __name__=='__main__':main()
