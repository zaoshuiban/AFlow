"""Experiment A pilot: frozen generated executable vs equally tooled controls.

Only development, validation and 30 pilot questions; no formal evaluation API.
Secrets are read from existing config and never written to experiment artifacts.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import random
import os
import sys
import tempfile
import shutil
import time
from collections import Counter
from pathlib import Path

from scripts.async_llm import LLMsConfig
from scripts.experiment_a_worker import validate, MODULES, BUILTINS, ATTRIBUTES
from scripts.offline_loop_protocol import atomic_json, digest, file_hash, read_json, run_lock

ROOT = Path('results/experiment_a_pilot_20261014_v4')
REUSE_ROOT = Path('results/experiment_a_pilot_20261014_v2')
ARMS = ('baseline', 'reflection', 'operator')
SEMANTICS = '''Input is a JSON string with exactly these fields: digits (a list of distinct integers 0..9), length (integer 1..7), repeat (boolean), lower and upper (inclusive nonnegative integer bounds). Count distinct length-digit positive decimal primes using only digits in digits. No leading zero; if repeat=false each digit may occur at most once. 0 and 1 are not primes. Return the exact nonnegative integer count. Invalid/unsupported input returns None. No other constraints.'''
SOURCE_FILES = ('scripts/experiment_a_pilot.py', 'scripts/experiment_a_worker.py', 'scripts/frozen_math_worker.py')


def parse_json(raw):
    raw = raw.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(raw)


def reference(p):
    """Oracle uses SymPy primality, independent of generated source."""
    from sympy import isprime
    seq = itertools.product(p['digits'], repeat=p['length']) if p['repeat'] else itertools.permutations(p['digits'], p['length'])
    count = 0
    for ds in seq:
        if ds[0] == 0:
            continue
        n = 0
        for d in ds:
            n = 10*n+d
        if p['lower'] <= n <= p['upper'] and isprime(n):
            count += 1
    return count


def second_reference(p):
    """Independent number scan and trial division for small validation cases."""
    count = 0
    for n in range(max(2, p['lower'], 10**(p['length']-1)), min(p['upper'], 10**p['length']-1)+1):
        ds = list(map(int, str(n)))
        if any(d not in p['digits'] for d in ds) or (not p['repeat'] and len(set(ds)) != len(ds)):
            continue
        if all(n % d for d in range(2, math.isqrt(n)+1)):
            count += 1
    return count


def make_rows(seed, n, role, seen):
    rng = random.Random(seed)
    rows = []
    while len(rows) < n:
        tier = len(rows) % 3
        length = (3, 5, 7)[tier] if role != 'validation' else rng.choice((1, 2, 3, 4))
        m = rng.randint(3, 6)
        ds = sorted(rng.sample(range(10), m))
        repeat = bool(rng.getrandbits(1))
        if not repeat:
            length = min(length, m)
        lo, hi = 10**(length-1), 10**length-1
        if rng.random() < .6:
            a, b = sorted((rng.randrange(lo, hi+1), rng.randrange(lo, hi+1)))
            lo, hi = a, b
        p = dict(digits=ds, length=length, repeat=repeat, lower=lo, upper=hi)
        # Split by digit set, length and reuse rule, not just interval or wording.
        family = digest([ds, length, repeat])
        if family in seen:
            continue
        seen.add(family)
        rows.append(dict(id=digest(p)[:16], family_id=family, role=role, tier=tier,
                         problem=json.dumps(p, sort_keys=True), expected=reference(p)))
    return rows


def prepare(root):
    root = Path(root)
    if (root/'manifest.json').exists():
        return
    root.mkdir(parents=True, exist_ok=True)
    if list(root.glob('requests/*.json')):
        raise ValueError('Cannot prepare over existing requests')
    seen = set()
    dev = make_rows(26101401, 30, 'development', seen)
    validation = make_rows(26101402, 60, 'validation', seen)
    # Entire old pilot pool is excluded after its protocol smoke exposed formatting failures.
    for previous in (REUSE_ROOT, Path('results/experiment_a_pilot_20261014_v3')):
        seen.update(r['family_id'] for r in read_json(previous/'inputs/pilot.json'))
    pilot = make_rows(26101423, 30, 'pilot', seen)
    # Explicit edge cases; independent of candidate output.
    edges = [dict(digits=[0, 1], length=1, repeat=True, lower=0, upper=9),
             dict(digits=[2], length=1, repeat=False, lower=2, upper=2),
             dict(digits=[0, 2, 5], length=3, repeat=False, lower=100, upper=999),
             dict(digits=[1, 3, 7], length=4, repeat=False, lower=1000, upper=9999),
             dict(digits=[1, 3, 7], length=2, repeat=True, lower=13, upper=13),
             dict(digits=[1, 3, 7], length=2, repeat=True, lower=14, upper=14)]
    for p in edges:
        validation.append(dict(id=digest(p)[:16], role='edge', problem=json.dumps(p, sort_keys=True), expected=reference(p)))
    checks = []
    for r in validation:
        other = second_reference(json.loads(r['problem']))
        if other != r['expected']:
            raise ValueError('Oracle disagreement')
        checks.append(dict(id=r['id'], expected=r['expected'], independent_answer=other))
    for name, rows in [('development', dev), ('validation', validation), ('pilot', pilot)]:
        atomic_json(root/'inputs'/f'{name}.json', rows)
    atomic_json(root/'oracle_crosscheck.json', checks)
    protocol = dict(schema_version=1, stage='pilot_only', formal_evaluation=False,
        semantics=SEMANTICS, model_alias='deepseek-v4-flash', thinking='disabled', temperature=0,
        candidates=2, candidate_selection='valid on every validation/development case; lowest total validation wall time, then candidate index',
        pilot_questions=30, repeats=1, arms=ARMS, randomized_interleaving_seed=26101404,
        budgets=dict(calls=4, output_tokens=8192, tools=4, tool_cumulative_wall_seconds=10, trial_wall_seconds=180),
        reflection_policy='First answer is a draft; a mandatory subsequent review must produce the final answer within the same limits; no oracle feedback.',
        pilot_policy='Descriptive only; do not select candidates or tune prompts using pilot outcomes.',
        api_budget_cny=18.5, max_requests=360, network_retries=0,
        previous_attempt='v1 JSON import conflict; v2 text JSON smoke interrupted; v3 native smoke exposed no operator adoption and top-level print confusion; 1.50 CNY reserved for prior calls',
        protocol_revision='Native API functions; A2 first tool forced to frozen operator (no learned routing). All arms told to submit function definitions only, no top-level examples/prints. Same v2 candidate; new pilot families exclude all v2/v3 pilot families.',
        generation_source=dict(path=str(REUSE_ROOT),selection_sha256=file_hash(REUSE_ROOT/'selection.json'),
            manifest_sha256=file_hash(REUSE_ROOT/'manifest.json'),
            generation_request_hashes={str(p):file_hash(p) for p in (REUSE_ROOT/'requests').glob('*.json') if read_json(p)['key'].startswith('generation/')}),
        candidate_hashes={str(p.relative_to(REUSE_ROOT)):file_hash(p) for p in (REUSE_ROOT/'candidates').glob('*')},
        cost_policy='Conservative high-peak uncached input 2 CNY/M and output 8 CNY/M; reserve UTF8 bytes+256 input tokens and output cap before each call; unknown usage retains reservation.',
        source_hashes={p:file_hash(Path(p)) for p in SOURCE_FILES},
        input_hashes={str(p.relative_to(root)):file_hash(p) for p in (root/'inputs').glob('*.json')},
        tool_policy='All arms use the SAME AST validator/isolated worker. Operator action runs the frozen candidate; python action runs fresh solve(problem). Both consume one tool invocation. No subprocess/file/network access in generated code.')
    atomic_json(root/'manifest.json', protocol)
    shutil.copytree(REUSE_ROOT/'candidates',root/'candidates')
    for p in SOURCE_FILES:
        out=root/'frozen_code'/p
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(Path(p).read_bytes())
    atomic_json(root/'state.json', dict(status='prepared', formal_evaluation_started=False))


class RequestFailure(Exception):
    pass


class BudgetStop(Exception):
    pass


class Transport:
    def __init__(self, root):
        from openai import AsyncOpenAI
        self.root = Path(root)
        self.manifest = read_json(self.root/'manifest.json')
        cfg = LLMsConfig.default().get(self.manifest['model_alias'])
        from urllib.parse import urlsplit
        if urlsplit(cfg.base_url).hostname != 'api.deepseek.com':
            raise ValueError('Requires official endpoint for frozen pricing/thinking settings')
        self.client = AsyncOpenAI(api_key=cfg.key, base_url=cfg.base_url, max_retries=0, timeout=175)

    def usage(self):
        rs = [read_json(p) for p in self.root.glob('requests/*.json')]
        inp = sum((r.get('usage') or {}).get('input_tokens', 0) for r in rs)
        out = sum((r.get('usage') or {}).get('output_tokens', 0) for r in rs)
        return dict(requests=len(rs), input_tokens=inp, output_tokens=out,
                    conservative_known_cost_cny=(2*inp+8*out)/1e6,
                    unknown_usage_requests=sum(r.get('usage') is None for r in rs),
                    reserved_or_reported_cost_cny=sum(r['charged_budget_cny'] for r in rs),
                    reported_models=sorted({r['reported_model'] for r in rs if r.get('reported_model')}),
                    invoice_required=True)

    async def call(self, key, messages, output_cap, timeout=175, tools=None):
        path = self.root/'requests'/(digest(key)+'.json')
        request = dict(messages=messages, output_cap=output_cap, model_alias=self.manifest['model_alias'])
        if tools is not None:request['tools']=tools
        if path.exists():
            saved = read_json(path)
            if saved['request_hash'] != digest(request):
                raise ValueError('Changed request on resume')
            if saved['status'] != 'completed':
                raise RequestFailure(saved.get('error', 'interrupted_result_unknown'))
            return saved
        usage = self.usage()
        reserve = (2*(len(json.dumps(request, ensure_ascii=False).encode())+256)+8*output_cap)/1e6
        if usage['requests'] >= self.manifest['max_requests'] or usage['reserved_or_reported_cost_cny']+reserve > self.manifest['api_budget_cny']:
            raise BudgetStop('Frozen monetary/request budget reached')
        saved = dict(key=key, request=request, request_hash=digest(request), status='started',
                     usage=None, charged_budget_cny=reserve)
        atomic_json(path, saved)
        t = time.monotonic()
        try:
            choice = {'type':'function','function':{'name':'operator'}} if key.startswith('pilot/') and key.endswith('/operator/0') else 'required'
            options = {'response_format':{'type':'json_object'}} if tools is None else dict(tools=tools,tool_choice=choice,parallel_tool_calls=False)
            response = await self.client.chat.completions.create(model=self.manifest['model_alias'], messages=messages,
                temperature=0, max_tokens=output_cap, **options,
                extra_body={'thinking':{'type':'disabled'}}, timeout=timeout)
            ch = response.choices[0]
            saved.update(response=ch.message.content or '', finish_reason=ch.finish_reason,
                         message=ch.message.model_dump(exclude_none=True),
                         reported_model=response.model, system_fingerprint=getattr(response,'system_fingerprint',None),
                         status='completed')
            if response.usage:
                saved['usage'] = dict(input_tokens=response.usage.prompt_tokens, output_tokens=response.usage.completion_tokens,
                                      total_tokens=response.usage.total_tokens)
                saved['charged_budget_cny'] = (2*response.usage.prompt_tokens+8*response.usage.completion_tokens)/1e6
            if ch.finish_reason == 'length':
                saved.update(status='failed', error='output_token_limit')
        except BaseException as exc:
            saved.update(status='failed', error=type(exc).__name__)
            if not isinstance(exc, Exception):
                atomic_json(path, saved)
                raise
        saved['elapsed_seconds'] = time.monotonic()-t
        atomic_json(path, saved)
        if saved['status'] != 'completed':
            raise RequestFailure(saved['error'])
        return saved


async def checked_execution(code, problem, timeout=6):
    t = time.monotonic()
    trace=dict(code=code,code_sha256=digest(code),problem_sha256=digest(problem),worker_wall_limit_seconds=timeout)
    try:
        validate(code)
        with tempfile.TemporaryDirectory(prefix='aflow-pilot-') as cwd:
            process=await asyncio.create_subprocess_exec(sys.executable,'-I',str(Path(__file__).with_name('experiment_a_worker.py')),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                cwd=cwd,env={'PATH':os.defpath,'LANG':'C.UTF-8'})
            try:
                stdout,stderr=await asyncio.wait_for(process.communicate(json.dumps(dict(code=code,problem=problem)).encode()),timeout)
            except (asyncio.TimeoutError,asyncio.CancelledError) as exc:
                if process.returncode is None:process.kill()
                await process.wait()
                if isinstance(exc,asyncio.CancelledError):raise
                raise ValueError('worker_timeout')
        trace.update(exit_code=process.returncode,stdout=stdout.decode(errors='replace')[:4000],stderr=stderr.decode(errors='replace')[:1000])
        if process.returncode:raise ValueError('worker_exit_'+str(process.returncode))
        v=json.loads(stdout)
        out=dict(status=v['status'],response='\\boxed{'+str(v['value'])+'}' if v['status']=='success' else '',
                 observation=v.get('error',''),tool_trace=trace)
    except (ValueError,SyntaxError,KeyError) as exc:
        out=dict(status='error',response='',observation=str(exc),tool_trace=trace)
    out['elapsed_seconds'] = time.monotonic()-t
    # Parse only the trusted worker's exact integer result wrapper.
    out['value'] = int(out['response'][7:-1]) if out['status']=='success' else None
    return out


async def generate_validate(root, transport):
    dev = read_json(root/'inputs/development.json')
    cases = dev+read_json(root/'inputs/validation.json')
    reports = []
    for i in range(2):
        artifact = root/'candidates'/f'candidate_{i+1}.json'
        if artifact.exists():
            reports.append(read_json(artifact))
            continue
        prompt = ('Generate one reusable exact mathematical operator. Return JSON with string fields code and rationale. '
            'Define solve(problem), taking the JSON string described below and returning int or None. '
            'Do not use fixed example answer lookups. No classes, files, network, processes, reflection, eval, exec, private attributes. '
            'Imports permitted: '+','.join(sorted(MODULES))+'. Builtins: '+','.join(BUILTINS)+'. '
            'Only these attribute names are allowed: '+','.join(sorted(ATTRIBUTES))+'. '
            'Use a single implementation for every future input. Worker limits: 5 CPU seconds, 6 wall seconds, monitored 256 MiB RSS. '
            f'This is candidate {i+1} of exactly 2 independent attempts.\nSpecification:\n'+SEMANTICS+
            '\nDevelopment examples (all other evaluation inputs are withheld):\n'+json.dumps(dev))
        try:
            r = await transport.call(f'generation/{i+1}', [{'role':'user','content':prompt}], 8192)
            v = parse_json(r['response'])
            if set(v) != {'code','rationale'} or not all(isinstance(x,str) for x in v.values()):
                raise ValueError('Invalid generation schema')
            validate(v['code'])
            cp = root/'candidates'/f'candidate_{i+1}.py'
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(v['code'])
            checks = []
            for row in cases:
                result = await checked_execution(v['code'], row['problem'])
                checks.append(dict(id=row['id'], expected=row['expected'], result=result,
                                   passed=result['status']=='success' and result['value']==row['expected']))
            for j, raw in enumerate(('{}', 'not json', '{"digits":[1,1],"length":2,"repeat":true,"lower":10,"upper":99}')):
                result = await checked_execution(v['code'], raw)
                checks.append(dict(id=f'invalid_{j}', result=result, passed=result['status']=='declined'))
            report = dict(candidate=i+1, code_hash=digest(v['code']), rationale=v['rationale'], checks=checks,
                          passed=all(c['passed'] for c in checks), elapsed=sum(c['result']['elapsed_seconds'] for c in checks))
        except BudgetStop:
            raise
        except Exception as exc:
            report = dict(candidate=i+1, passed=False, error=type(exc).__name__+': '+str(exc)[:300])
        atomic_json(artifact, report)
        reports.append(report)
        print(json.dumps(dict(stage='validation', candidate=i+1, passed=report['passed'],
                              checks=len(report.get('checks',[]))), ensure_ascii=False), flush=True)
    winners = sorted((r for r in reports if r['passed']), key=lambda r:(r['elapsed'],r['candidate']))
    if not winners:
        atomic_json(root/'state.json', dict(status='generation_validation_failed', formal_evaluation_started=False))
        return None
    winner = winners[0]
    code = (root/'candidates'/f"candidate_{winner['candidate']}.py").read_text()
    selection = dict(candidate=winner['candidate'], code_hash=digest(code), selected_before_pilot=True,
                     selection_rule=transport.manifest['candidate_selection'])
    if (root/'selection.json').exists() and read_json(root/'selection.json') != selection:
        raise ValueError('Selection changed')
    atomic_json(root/'selection.json', selection)
    return code


def system_prompt(arm):
    base = ('You are a mathematical Agent. Solve the user task exactly. All input semantics:\n'+SEMANTICS+
        '\nUse the provided native function tools. python executes code defining solve(problem) returning int or None. '
        'submit records your integer answer. Call exactly ONE function per turn. '
        'The solve(problem) argument is a JSON STRING, not a dict; use json.loads(problem) to decode it. '
        'Submit ONLY imports and function definitions as code. The runner calls solve(problem) and captures its RETURN value. '
        'Do NOT print, do NOT use json.dumps, do NOT call solve at module top level, and do NOT include example invocations. '
        'Python tools have no files/network/processes, no classes/global/nonlocal, no reflection/dynamic code/private attributes. '
        'Allowed imports: '+','.join(sorted(MODULES))+'. Allowed builtins: '+','.join(BUILTINS)+'. '
        'Allowed attribute names: '+','.join(sorted(ATTRIBUTES))+'. '
        'All groups have identical generic Python permissions. Max 4 model calls, 8192 TOTAL output tokens, '
        '4 total tools, cumulative tool wall time 10 seconds (each worker CPU cap 5 seconds), 180 seconds total. '
        'Do not rely on approximate counts. Tools execute your solve(problem) on the given input and return the result. ')
    if arm == 'reflection':
        base += ('Your first answer is a draft. After it you MUST recheck or independently resolve the problem using the remaining budget '
                 'and submit a final answer; the draft receives no correctness feedback. Reserve at least one call for this review. ')
    if arm == 'operator':
        base += ('You additionally have the operator function, which runs one previously generated and validated frozen solve(problem) '
                 'for this precise task specification. It consumes ONE tool call and the same local execution budget. '
                 'Your FIRST action MUST call operator: this is a fixed intervention, not a routing test. '
                 'After observing its result, submit the final integer answer, or use remaining generic tools if needed. ')
    return base


def native_tools(arm):
    tools=[dict(type='function',function=dict(name='python',description='Execute your Python solve(problem) on the supplied JSON string. Return exact integer count or None.',
        parameters=dict(type='object',properties=dict(code=dict(type='string')),required=['code'],additionalProperties=False))),
        dict(type='function',function=dict(name='submit',description='Submit the exact nonnegative integer answer. In reflection group the first submission is a draft.',
        parameters=dict(type='object',properties=dict(value=dict(type='integer',minimum=0)),required=['value'],additionalProperties=False)))]
    if arm=='operator':
        tools.append(dict(type='function',function=dict(name='operator',description='Run the frozen generated and validated prime counting operator on this task; one tool invocation.',
            parameters=dict(type='object',properties={},additionalProperties=False))))
    return tools


async def trial(root, transport, row, arm, code):
    key=f"pilot/{row['id']}/{arm}"
    path=root/'observations'/f"{row['id']}_{arm}.json"
    if path.exists():
        return read_json(path)
    messages=[{'role':'system','content':system_prompt(arm)},
              {'role':'user','content':'Count the primes for this input:\n'+row['problem']}]
    tokens=0; inputs=0; tools=0; tool_wall=0.; request_wall=0.; reviewed=False; value=None; events=[]; error=''
    started=time.monotonic()
    for step in range(4):
        remaining=8192-tokens
        # Persisted request durations, rather than resume wall time, count toward the budget.
        remaining_wall=180-request_wall-tool_wall
        if remaining<=0 or remaining_wall<=0:
            error='budget_exhausted';break
        if arm=='reflection' and not reviewed and step==2:
            messages.append({'role':'user','content':'Return your draft answer now; one final review call must remain.'})
        try:
            r=await transport.call(f'{key}/{step}',messages,remaining,timeout=min(175,remaining_wall),tools=native_tools(arm))
            tokens+=(r.get('usage') or {}).get('output_tokens',remaining)
            inputs+=(r.get('usage') or {}).get('input_tokens',0)
            request_wall+=r.get('elapsed_seconds',0)
            msg=r['message']
            calls=msg.get('tool_calls',[])
            if len(calls)!=1:raise ValueError('Expected exactly one native function call')
            fn=calls[0]['function']
            action=parse_json(fn['arguments'])
            action['action']='answer' if fn['name']=='submit' else fn['name']
            events.append(dict(step=step, action=action))
            messages.append({'role':'assistant','content':msg.get('content'), 'tool_calls':calls})
            if action.get('action')=='answer':
                if type(action.get('value')) is not int or action['value']<0:
                    raise ValueError('Invalid answer')
                if arm=='reflection' and not reviewed:
                    reviewed=True
                    messages.append({'role':'tool','tool_call_id':calls[0]['id'],'content':'Draft received. No correctness feedback is available.'})
                    messages.append({'role':'user','content':'This is your draft, with no correctness feedback. Independently check the constraints and arithmetic, correct any mistakes, and return the final answer. You may use remaining tools/calls. Do not simply repeat without checking.'})
                    continue
                value=action['value'];break
            if action.get('action') not in ('python','operator') or (action['action']=='operator' and arm!='operator'):
                raise ValueError('Unavailable action')
            if tools>=4 or tool_wall>=10:
                error='tool_budget_exhausted';break
            toolcode=code if action['action']=='operator' else action['code']
            result=await checked_execution(toolcode,row['problem'],timeout=min(6,10-tool_wall,max(.01,180-request_wall-tool_wall)))
            tools+=1;tool_wall+=result['elapsed_seconds']
            events[-1]['tool_result']=result
            messages.append({'role':'tool','tool_call_id':calls[0]['id'],'content':'Tool observation (not a reference answer): '+json.dumps({k:v for k,v in result.items() if k!='tool_trace'})})
        except BudgetStop:
            raise
        except RequestFailure as exc:
            error='request_failure:'+str(exc);break
        except Exception as exc:
            error='invalid_response:'+type(exc).__name__;break
    if value is None and not error:
        error='no_final_answer_within_budget'
    elapsed=request_wall+tool_wall
    if elapsed>180 or tokens>8192 or tool_wall>10:
        error='budget_exceeded'
    # Failure requests may have billable tokens; report trial accounting from the raw ledger.
    records=[read_json(p) for p in root.glob('requests/*.json') if read_json(p)['key'].startswith(key+'/')]
    tokens=sum((r.get('usage') or {}).get('output_tokens',0) for r in records)
    inputs=sum((r.get('usage') or {}).get('input_tokens',0) for r in records)
    out=dict(id=row['id'],tier=row['tier'],arm=arm,expected=row['expected'],value=value,
        correct=value==row['expected'] and not error,error=error,calls=len(records),tools=tools,
        input_tokens=inputs,output_tokens=tokens,unknown_usage=sum(r.get('usage') is None for r in records),
        accounted_seconds=sum(r.get('elapsed_seconds',0) for r in records)+tool_wall,
        tool_wall_seconds=tool_wall,process_elapsed_seconds=time.monotonic()-started,events=events)
    atomic_json(path,out)
    return out


def summarize(root, transport):
    obs=[read_json(p) for p in root.glob('observations/*.json')]
    groups={}
    for arm in ARMS:
        rows=[r for r in obs if r['arm']==arm]
        groups[arm]=dict(n=len(rows),correct=sum(r['correct'] for r in rows),
            errors=dict(Counter(r['error'] for r in rows if r['error'])),
            input_tokens=sum(r['input_tokens'] for r in rows),output_tokens=sum(r['output_tokens'] for r in rows),
            calls=sum(r['calls'] for r in rows),tools=sum(r['tools'] for r in rows),
            mean_seconds=sum(r['accounted_seconds'] for r in rows)/max(1,len(rows)),
            operator_uses=sum(e['action'].get('action')=='operator' for r in rows for e in r['events']))
    pairs=[]
    for base in ('baseline','reflection'):
        a={r['id']:r for r in obs if r['arm']==base};b={r['id']:r for r in obs if r['arm']=='operator'}
        ids=sorted(a.keys()&b.keys())
        pairs.append(dict(baseline=base,n=len(ids),wins=sum(b[i]['correct'] and not a[i]['correct'] for i in ids),
            losses=sum(a[i]['correct'] and not b[i]['correct'] for i in ids),ties=sum(a[i]['correct']==b[i]['correct'] for i in ids)))
    result=dict(stage='pilot_only',complete=len(obs)==90,groups=groups,pairs=pairs,usage=transport.usage(),
                formal_evaluation_started=False,selection=read_json(root/'selection.json') if (root/'selection.json').exists() else None,
                interpretation='30 tasks x 1 repeat; descriptive pilot, not formal statistical evidence; no formal tasks generated')
    atomic_json(root/'summary.json',result)
    return result


async def run(root):
    root=Path(root)
    m=read_json(root/'manifest.json')
    for p,h in m['source_hashes'].items():
        if file_hash(Path(p))!=h:
            raise ValueError('Source changed since freeze: '+p)
    for p,h in m['input_hashes'].items():
        if file_hash(root/p)!=h:
            raise ValueError('Frozen input changed: '+p)
    for p,h in m['candidate_hashes'].items():
        if file_hash(root/p)!=h:
            raise ValueError('Frozen candidate changed: '+p)
    transport=Transport(root)
    try:
        code=await generate_validate(root,transport)
        if code is None:
            summarize(root,transport);return
        rows=read_json(root/'inputs/pilot.json')
        schedule=[(row,arm) for row in rows for arm in ARMS]
        random.Random(m['randomized_interleaving_seed']).shuffle(schedule)
        for row,arm in schedule:
            result=await trial(root,transport,row,arm,code)
            print(json.dumps(dict(stage='pilot',id=row['id'],arm=arm,correct=result['correct'],error=result['error']),ensure_ascii=False),flush=True)
            summarize(root,transport)
        atomic_json(root/'state.json',dict(status='pilot_complete',formal_evaluation_started=False))
    except BudgetStop:
        atomic_json(root/'state.json',dict(status='budget_stopped',formal_evaluation_started=False))
    finally:
        summarize(root,transport)
        await transport.client.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=('prepare','run'))
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--allow-api',action='store_true')
    args=p.parse_args()
    if args.command=='prepare':
        prepare(args.root)
    else:
        if not args.allow_api:
            p.error('run requires --allow-api')
        with run_lock(args.root):
            asyncio.run(run(args.root))


if __name__=='__main__':
    main()
