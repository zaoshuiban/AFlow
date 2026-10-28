"""Reserve final delivery; verify old tasks, then calibrate CCC development only."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import random
import sys
import tempfile
import time
from collections import Counter,defaultdict
from pathlib import Path

from scripts.experiment_a_pilot import Transport,RequestFailure,BudgetStop,parse_json,SEMANTICS
from scripts.counting_worker import validate,MODULES,BUILTINS,ATTRIBUTES
from scripts.compositional_counting import SPEC
from scripts.counting_benchmark import ROOT as BENCH_ROOT
from scripts.offline_loop_protocol import atomic_json,read_json,digest,file_hash,run_lock

ROOT=Path('results/experiment_a_calibration_20261014_v3')
FILES=('scripts/experiment_a_calibration.py','scripts/counting_worker.py','scripts/frozen_math_worker.py',
       'scripts/experiment_a_pilot.py','scripts/compositional_counting.py','scripts/counting_benchmark.py')


def tools_for(arm,final=False):
    submit=dict(type='function',function=dict(name='submit',description='Deliver your FINAL exact integer answer, or null if unable. This records final delivery; do not return code or prose.',
        parameters=dict(type='object',properties=dict(value=dict(type=['integer','null'])),required=['value'],additionalProperties=False)))
    if final:return [submit]
    tools=[dict(type='function',function=dict(name='python',description='Run imports and solve(problem) function definitions. Argument is JSON string. Return int, never dict or print.',
        parameters=dict(type='object',properties=dict(code=dict(type='string')),required=['code'],additionalProperties=False))),submit]
    if arm=='operator':tools.append(dict(type='function',function=dict(name='operator',description='Run previously frozen operator on the input.',parameters=dict(type='object',properties={},additionalProperties=False))))
    return tools


def prompt(semantics,arm):
    text=('Solve the exact counting task. Full public task specification:\n'+semantics+
        '\nUse exactly one native tool per response. The python tool calls solve(problem), passing a JSON STRING; decode it with json.loads(problem). '
        'Code contains imports and function definitions ONLY, no top-level examples or test invocations. '
        'Optional debug print is captured and never treated as an answer; solve(problem) must RETURN its integer. '
        'Return a Python int (NOT dict, float or string). Do not assume unprovided constraints. '
        'Shared limits: 4 model calls, 8192 cumulative output tokens, 10 seconds cumulative tool wall time, 180 seconds total; '
        'each worker has 5 CPU seconds, monitored RSS 256 MiB. Exact large integers are supported. '
        'No files/network/processes, classes, globals, nonlocal, dynamic code, reflection or private attributes. '
        'Allowed imports: '+','.join(sorted(MODULES))+'. Builtins: '+','.join(BUILTINS)+'. Attributes: '+','.join(sorted(ATTRIBUTES))+'. '
        'The runner reserves the LAST CALL for submit and at least 512 output tokens; you cannot execute another tool then. '
        'Submit an exact integer as soon as ready. If unable, explicitly submit null rather than writing code on the last call. ')
    if arm=='reflection':text+=('Your first successful tool result is a draft, not a reference answer. Independently review it against all constraints; '
        'you may use remaining Python calls to check or correct it, then submit the final integer. '
        'If you submit before having any draft tool result, that first submission becomes a draft and a further review call is required. '
        'On the last call you MUST submit, even if a review remains incomplete; do not waste the final delivery slot. ')
    if arm=='operator':text+='FIRST call must be operator. Then use its result and submit the final answer within the same budget. '
    return text


async def execute(code,problem,wall):
    t=time.monotonic();trace=dict(code_sha256=digest(code),code=code,problem_sha256=digest(problem))
    try:
        validate(code)
        with tempfile.TemporaryDirectory(prefix='ccc-worker-') as cwd:
            proc=await asyncio.create_subprocess_exec(sys.executable,'-I',str(Path(__file__).with_name('counting_worker.py')),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                cwd=cwd,env={'PATH':os.defpath,'LANG':'C.UTF-8'})
            try:
                stdout,stderr=await asyncio.wait_for(proc.communicate(json.dumps(dict(code=code,problem=problem)).encode()),wall)
            except (asyncio.TimeoutError,asyncio.CancelledError) as exc:
                if proc.returncode is None:proc.kill()
                await proc.wait()
                if isinstance(exc,asyncio.CancelledError):raise
                raise ValueError('worker_timeout')
        trace.update(exit_code=proc.returncode,stderr=stderr.decode(errors='replace')[:1000])
        if proc.returncode:raise ValueError('worker_exit_'+str(proc.returncode))
        result=json.loads(stdout)
    except (ValueError,KeyError,SyntaxError) as exc:result=dict(status='error',value=None,error=str(exc))
    return {**result,'elapsed_seconds':time.monotonic()-t,'trace':trace}


async def solve(root,transport,row,arm,semantics,operator_code=None):
    dest=root/'observations'/f"{row['id']}_{arm}.json"
    if dest.exists():return read_json(dest)
    key=f"pilot/{row['id']}/{arm}"
    messages=[dict(role='system',content=prompt(semantics,arm)),dict(role='user',content='Count exactly for this JSON input:\n'+row['problem'])]
    output=0;req_wall=0.;tool_wall=0.;events=[];answer=None;submitted=False;draft=False;reviewed=False;error='';first_draft=None
    for step in range(4):
        final=step==3 or output>=7680 or tool_wall>=9.8
        max_output=8192-output-(0 if final else 512)
        remaining_wall=180-req_wall-tool_wall
        if max_output<=0 or remaining_wall<=0:error='budget_exhausted';break
        turn_tools=tools_for(arm,final=final)
        if arm=='operator' and step==0:turn_tools=[t for t in turn_tools if t['function']['name']=='operator']
        if final:messages.append(dict(role='user',content='FINAL DELIVERY CALL. No execution tools remain. Submit the final exact integer now, or null if unable. Do not return more code.'))
        try:
            r=await transport.call(f'{key}/{step}',messages,max_output,timeout=min(175,remaining_wall),tools=turn_tools)
            output+=(r.get('usage') or {}).get('output_tokens',max_output)
            req_wall+=r.get('elapsed_seconds',0)
            msg=r['message'];tc=msg.get('tool_calls',[])
            if len(tc)!=1:raise ValueError('Expected one function call')
            fn=tc[0]['function'];name=fn['name'];args=parse_json(fn['arguments'])
            if name not in {t['function']['name'] for t in turn_tools}:raise ValueError('Unavailable function')
            event=dict(step=step,function=name,arguments=args,final_slot=final);events.append(event)
            messages.append(dict(role='assistant',content=msg.get('content'),tool_calls=tc))
            if name=='submit':
                value=args.get('value')
                if value is not None and (type(value) is not int or value<0):raise ValueError('Invalid answer')
                if arm=='reflection' and not draft and not final:
                    draft=True;first_draft=value
                    messages.append(dict(role='tool',tool_call_id=tc[0]['id'],content='Draft recorded. No reference/correctness feedback. Review it and submit the final answer using remaining calls.'))
                    continue
                answer=value;submitted=True;reviewed=arm!='reflection' or draft
                if value is None:error='explicit_abstention'
                break
            count_tools=sum('tool_result' in e for e in events)
            if count_tools>=3 or tool_wall>=9.9:error='tool_budget_exhausted';break
            code=operator_code if name=='operator' else args['code']
            result=await execute(code,row['problem'],min(6,9.9-tool_wall,max(.01,180-req_wall-tool_wall)))
            tool_wall+=result['elapsed_seconds'];event['tool_result']=result
            observation={k:v for k,v in result.items() if k not in ('trace',)}
            if arm=='reflection' and result['status']=='success' and not draft:
                draft=True;first_draft=result['value']
                observation['review_instruction']='This is your draft from your own tool, not an oracle. Independently verify the modeling, overlap rules, exact arithmetic and constraints, then submit the final answer. Reserve the last call for submission.'
            messages.append(dict(role='tool',tool_call_id=tc[0]['id'],content=json.dumps(observation)))
        except BudgetStop:raise
        except RequestFailure as exc:error='request_failure:'+str(exc);break
        except Exception as exc:error='invalid_response:'+type(exc).__name__;break
    if not submitted and not error:error='no_final_submission'
    rs=[read_json(p) for p in root.glob('requests/*.json') if read_json(p)['key'].startswith(key+'/')]
    used=sum((r.get('usage') or {}).get('output_tokens',0) for r in rs)
    accounted=sum(r.get('elapsed_seconds',0) for r in rs)+tool_wall
    violations=used>8192 or len(rs)>4 or tool_wall>10 or accounted>180
    if violations:error='budget_exceeded'
    result=dict(id=row['id'],arm=arm,tier=str(row.get('tier','prime')),scale=str(row.get('scale','original')),
        expected=row['expected'],answer=answer,submitted=submitted,correct=submitted and not error and answer==row['expected'],
        error=error,draft=first_draft,review_performed=reviewed,calls=len(rs),tools=sum('tool_result' in e for e in events),
        input_tokens=sum((r.get('usage') or {}).get('input_tokens',0) for r in rs),output_tokens=used,
        unknown_usage=sum(r.get('usage') is None for r in rs),seconds=accounted,tool_seconds=tool_wall,budget_violation=violations,events=events)
    atomic_json(dest,result)
    print(json.dumps({k:result[k] for k in ('id','arm','tier','scale','correct','submitted','error')},ensure_ascii=False),flush=True)
    return result


def summarize(root,transport):
    rows=[read_json(p) for p in root.glob('observations/*.json')];m=read_json(root/'manifest.json');groups={}
    for arm in m['arms']:
        subset=[r for r in rows if r['arm']==arm];cells={}
        for tier in sorted({r['tier'] for r in subset}):
            for scale in sorted({r['scale'] for r in subset}):
                cell=[r for r in subset if r['tier']==tier and r['scale']==scale]
                if cell:cells[tier+'/'+scale]=dict(n=len(cell),correct=sum(r['correct'] for r in cell),submitted=sum(r['submitted'] for r in cell))
        groups[arm]=dict(n=len(subset),correct=sum(r['correct'] for r in subset),submitted=sum(r['submitted'] for r in subset),
            errors=dict(Counter(r['error'] for r in subset if r['error'])),budget_violations=sum(r['budget_violation'] for r in subset),
            calls=sum(r['calls'] for r in subset),input_tokens=sum(r['input_tokens'] for r in subset),output_tokens=sum(r['output_tokens'] for r in subset),
            cells=cells)
    result=dict(stage=m['stage'],complete=len(rows)==m['planned_observations'],groups=groups,usage=transport.usage(),formal_started=False,operator_generated=False)
    atomic_json(root/'summary.json',result);return result


def prepare(stage):
    root=ROOT/stage
    if (root/'manifest.json').exists():return root
    root.mkdir(parents=True,exist_ok=True)
    if stage=='reflection_repair':
        rows=read_json(Path('results/experiment_a_pilot_20261014_v4/inputs/development.json'))[:3]
        arms=['baseline','reflection','operator'];semantics=SEMANTICS;cap=.5;max_requests=36
        old=Path('results/experiment_a_pilot_20261014_v4/candidates/candidate_2.py')
        operator_code=old.read_text();(root/'operator.py').write_text(operator_code)
    else:
        bm=read_json(BENCH_ROOT/'manifest.json')
        if file_hash(BENCH_ROOT/'development.json')!=bm['development_sha256']:raise ValueError('Development hash mismatch')
        rows=read_json(BENCH_ROOT/'development.json')
        arms=['baseline','reflection'];semantics=SPEC;cap=13.5;max_requests=720
    atomic_json(root/'inputs/development.json',rows)
    m=dict(stage=stage,model_alias='deepseek-v4-flash',thinking='disabled',temperature=0,
        api_budget_cny=cap,max_requests=max_requests,arms=arms,semantics=semantics,planned_observations=len(rows)*len(arms),
        budgets=dict(calls=4,output_tokens=8192,final_output_reserve=512,worker_cpu=5,cumulative_tool_wall=10,wall=180),
        final_policy='Last call only submit; reserve 512 tokens; no auto-promotion of drafts; first successful own tool result starts Reflection review.',
        source_hashes={p:file_hash(Path(p)) for p in FILES},input_hash=file_hash(root/'inputs/development.json'),
        operator_hash=digest(operator_code) if stage=='reflection_repair' else None,
        benchmark_manifest_hash=file_hash(BENCH_ROOT/'manifest.json') if stage!='reflection_repair' else None,
        seed=2610141001,concurrency=3,formal_started=False,operator_generated=False)
    atomic_json(root/'manifest.json',m)
    for p in FILES:
        target=root/'frozen_code'/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(Path(p).read_bytes())
    atomic_json(root/'state.json',dict(status='prepared',formal_started=False))
    return root


async def run(stage):
    root=ROOT/stage;m=read_json(root/'manifest.json')
    for p,h in m['source_hashes'].items():
        if file_hash(Path(p))!=h:raise ValueError('Changed frozen source '+p)
    if file_hash(root/'inputs/development.json')!=m['input_hash']:raise ValueError('Changed inputs')
    if stage=='calibration':
        prior=read_json(ROOT/'reflection_repair/summary.json')
        if not prior['complete'] or any(g['submitted']!=g['n'] for g in prior['groups'].values()):raise ValueError('Reflection final-delivery repair must pass first')
    transport=Transport(root);rows=read_json(root/'inputs/development.json')
    schedule=[(r,a) for r in rows for a in m['arms']];random.Random(m['seed']).shuffle(schedule)
    operator_code=(root/'operator.py').read_text() if stage=='reflection_repair' else None
    sem=asyncio.Semaphore(m['concurrency'])
    async def one(row,arm):
        async with sem:
            await solve(root,transport,row,arm,m['semantics'],operator_code)
            summarize(root,transport)
    try:
        await asyncio.gather(*(one(r,a) for r,a in schedule))
        atomic_json(root/'state.json',dict(status='complete',formal_started=False))
    except BudgetStop:atomic_json(root/'state.json',dict(status='budget_stopped',formal_started=False))
    finally:
        summarize(root,transport);await transport.client.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('prepare','run'));p.add_argument('stage',choices=('reflection_repair','calibration'));p.add_argument('--allow-api',action='store_true');args=p.parse_args()
    if args.command=='prepare':prepare(args.stage)
    else:
        if not args.allow_api:p.error('run requires --allow-api')
        with run_lock(ROOT/args.stage):asyncio.run(run(args.stage))


if __name__=='__main__':main()
