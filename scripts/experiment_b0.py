"""One-mechanism exploratory B0, isolated from all Experiment A outputs."""
from __future__ import annotations
import argparse,ast,asyncio,copy,difflib,json,random,shutil,time
from pathlib import Path
from collections import Counter
from scripts.compositional_counting import SPEC,exact_dp,brute_force,make_instance,KINDS
from scripts.experiment_a_calibration import execute,solve
from scripts.experiment_a_pilot import Transport,parse_json,BudgetStop
from scripts.counting_worker import MODULES,BUILTINS,ATTRIBUTES,validate
from scripts.offline_loop_protocol import atomic_json,read_json,digest,file_hash,run_lock

ROOT=Path('results/experiment_b0_20261014_v1')
OLD=Path('results/generated_operator_experiment_a_v1')
SOURCE_ID='e4d3bffc7350d308b0da'
FILES=['scripts/experiment_b0.py','scripts/experiment_a_calibration.py','scripts/experiment_a_pilot.py','scripts/compositional_counting.py','scripts/counting_worker.py','scripts/frozen_math_worker.py','scripts/async_llm.py','scripts/offline_loop_protocol.py']


def relevant(p):
    return any(len(s)>=3 for c in p['constraints'] for s in (c.get('patterns',[]) if c['kind']=='forbidden' else [c['pattern']] if c['kind']=='pattern_count' else []))


def subroot(name,cap,requests):
    d=ROOT/name;d.mkdir(parents=True,exist_ok=True)
    m=dict(model_alias='deepseek-v4-flash',api_budget_cny=cap,max_requests=requests,temperature=0,thinking='disabled',retries=0)
    if not (d/'manifest.json').exists():atomic_json(d/'manifest.json',m)
    return d


def prepare():
    if (ROOT/'manifest.json').exists():raise FileExistsError('Already prepared; use run')
    start=time.monotonic();ROOT.mkdir(parents=True,exist_ok=True)
    oldrow=read_json(OLD/'observations'/f'{SOURCE_ID}_reflection.json')
    source_task=next(r for r in read_json(OLD/'inputs/formal_unsealed.json') if r['id']==SOURCE_ID)
    # Preserve complete original trace, extract only callable definitions for replay.
    atomic_json(ROOT/'discovery/original_observation.json',oldrow);atomic_json(ROOT/'discovery/task.json',source_task)
    code=oldrow['events'][2]['arguments']['code'];tree=ast.parse(code)
    callable_source='\n\n'.join(ast.get_source_segment(code,n) for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom,ast.FunctionDef)))+'\n'
    original=ROOT/'discovery/original.py';original.write_text(callable_source)
    assert callable_source.count('newstate = (state + (d,))[-2:]')==1
    # One line only: retain three symbols through matching (discovery and diagnostics use max length3).
    repaired=callable_source.replace('newstate = (state + (d,))[-2:]','newstate = (state + (d,))[-3:]')
    (ROOT/'discovery/causal_repair.py').write_text(repaired)
    (ROOT/'discovery/causal_repair.diff').write_text(''.join(difflib.unified_diff(callable_source.splitlines(True),repaired.splitlines(True))))
    hypothesis=dict(mechanism_id='premature_history_truncation',source_task_id=SOURCE_ID,
        observed_failure=f"Final {oldrow['answer']} != independent truth {oldrow['expected']}; all3 tools successfully returned same wrong value.",
        suspected_cause='Suffix truncated to2 before length3 pattern comparison; matches unreachable.',
        mechanism_hypothesis='Insufficient retained history before contiguous-pattern checking can systematically omit matches. Separate hypothesis: fresh Agent may recreate such local defects under related demands.',
        trigger_conditions=['reachable patterns longer than retained history','positive length3 count requires matches','overlap or concurrent patterns can increase history demands'],
        non_trigger_conditions=['patterns length<=2','pattern unreachable due other constraints','sequence shorter than pattern'],
        falsification_conditions=['original program correct on all reachable trigger probes','single-line history retention fails to repair otherwise supported cases','fresh Agent never exhibits verified local history errors: argues against stable Agent gap'],
        proposed_repair_capability='Reusable exact matching/counting respecting sufficient history, overlap, multiple patterns and public constraints',evidence=['original executed trace and independent truth'],status='hypothesis',assisted=True)
    atomic_json(ROOT/'mechanism_hypothesis.json',hypothesis)
    seen={digest(json.loads(source_task['problem']))}
    # Exclude all historical A tasks by exact semantic input, without reusing them as test evidence.
    for path in [OLD/'inputs/training.json',OLD/'inputs/development.json',OLD/'inputs/boundary.json',OLD/'inputs/formal_unsealed.json']:
        seen.update(digest(json.loads(r['problem'])) for r in read_json(path))
    checks=[]
    def row(p,role,**meta):
        pid=digest(p);assert pid not in seen;seen.add(pid);ref=exact_dp(p)['answer']
        if p['n']<=7:
            brute=brute_force(p);assert brute==ref;checks.append(dict(id=pid[:20],dp=ref,brute=brute))
        return dict(id=pid[:20],problem=json.dumps(p,sort_keys=True),expected=ref,tier=role,scale='small' if p['n']<=7 else 'large',mechanism_label=relevant(p),**meta)
    patterns=['012','010','123','101','230','232','303','021','000','321']
    diagnostics=[]
    for j,pat in enumerate(patterns):
        n=[6,7,8,10,12,14,5,2,7,11][j]
        cs=[]
        if j%3==0:cs.append(dict(kind='adjacent'))
        if j%3==1:cs.append(dict(kind='position',parity='even',forbidden_symbol=(int(pat[0])+1)%4))
        if j==6:cs.append(dict(kind='position',parity='odd',forbidden_symbol=3));cs.append(dict(kind='position_dummy')) if False else None
        # j8: 000 impossible with adjacency, a deliberate falsifying/non-trigger case.
        if j==8:cs.append(dict(kind='adjacent'))
        if j in (1,4,5,7):cs.append(dict(kind='pattern_count',pattern=pat,exact=0 if j==7 else 1))
        else:cs.append(dict(kind='forbidden',patterns=[pat,pat[::-1]] if j==9 else [pat]))
        for variant in ('trigger','control'):
            cp=copy.deepcopy(cs)
            if variant=='control':
                for c in cp:
                    if c['kind']=='forbidden':c['patterns']=[s[:2] for s in c['patterns']]
                    if c['kind']=='pattern_count':c['pattern']=c['pattern'][:2]
            diagnostics.append(row(dict(n=n,alphabet=[0,1,2,3],constraints=cp),'diagnostic',pair=j,variant=variant))
    rng=random.Random(2610143001);dev=[]
    for j in range(16):
        kinds=random.Random(2610143100+j).sample(KINDS,1+j%5)
        while True:
            p=make_instance(rng,kinds,4+j%4)
            if digest(p) not in seen:break
        dev.append(row(p,'operator_development'))
    hidden=[]
    for j in range(12):
        while True:
            pat=''.join(rng.choice('0123') for _ in range(3+j%2));cs=[dict(kind='forbidden',patterns=[pat,pat[::-1]])] if j%2==0 else [dict(kind='pattern_count',pattern=pat,exact=1+j%2)]
            if j%3==0:cs.append(dict(kind='mod',modulus=3+j%5,residue=j%(3+j%5)))
            if j%3==1:cs.append(dict(kind='position',parity='odd',forbidden_symbol=(j+1)%4))
            if j%3==2:cs.append(dict(kind='endpoints'))
            p=dict(n=[6,7,9,12,16,20][j%6],alphabet=[0,1,2,3],constraints=cs)
            if digest(p) not in seen:break
        hidden.append(row(p,'transfer'))
    for j in range(12):
        while True:
            if j%3==0:cs=[dict(kind='forbidden',patterns=[''.join(rng.choice('0123') for _ in range(2))])]
            elif j%3==1:cs=[dict(kind='pattern_count',pattern=str(j%4),exact=2)]
            else:cs=[dict(kind='count',symbol=j%4,exact=2),dict(kind='endpoints')]
            p=dict(n=5+j,alphabet=[0,1,2,3],constraints=cs)
            if digest(p) not in seen:break
        hidden.append(row(p,'control'))
    # Natural distribution fixed independently of outcomes: uniform k1..7, uniform subsets,n5..18.
    for j in range(20):
        while True:
            p=make_instance(rng,rng.sample(KINDS,rng.randint(1,7)),rng.randint(5,18))
            if digest(p) not in seen:break
        hidden.append(row(p,'natural'))
    atomic_json(ROOT/'inputs/diagnostics.json',diagnostics);atomic_json(ROOT/'inputs/operator_development.json',dev)
    atomic_json(ROOT/'sealed/hidden.json',hidden);atomic_json(ROOT/'oracle_crosschecks.json',checks)
    shutil.copyfile('/Users/majiatong/.codex/attachments/67921646-9c17-4867-83d9-d1fe49e9e61a/已粘贴的文本.txt',ROOT/'user_protocol.txt')
    sources={s:file_hash(Path(s)) for s in FILES}
    for s in FILES:
        target=ROOT/'frozen_code'/s;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(s,target)
    m=dict(experiment='B0 exploratory',created='2026-10-14',sources=sources,input_hashes={str(p.relative_to(ROOT)):file_hash(p) for p in (ROOT/'inputs').glob('*.json')},hidden_sha256=file_hash(ROOT/'sealed/hidden.json'),
        discovery_id=SOURCE_ID,diagnostic_pairs=10,development=16,hidden=dict(transfer=12,control=12,natural=20),
        generation=dict(candidates=1,max_repairs=1,output_cap=8192,seed_replicates=1,offline_group_cap_cny=3,selection='first passing development else last available; all attempts retained'),
        budget=dict(diag_cap=2.5,eval_arm_cap=8/3,total_max_cny=14,max_requests=612),
        online=dict(calls=4,output_tokens=8192,tool_wall=10,actual_wall=180,concurrency=3),
        applicability='Target mechanism relevance: any forbidden/counting pattern length>=3. Full solver valid for ALL public-domain inputs; irrelevant does not mean invalid input.',
        design_sha256=file_hash(Path('docs/superpowers/specs/2026-10-14-experiment-b0-design.md')))
    atomic_json(ROOT/'manifest.json',m)
    atomic_json(ROOT/'human_interventions.json',dict(assisted=True,actor='research assistant Codex following user suggested hypothesis',not_autonomous_discovery=True,
        activities=['selected user suggested A failure','proposed falsifiable mechanism','designed counterfactual pairs and splits','one-line causal control, never a generated operator','will translate observed evidence into behavioral requirements, no reference algorithm supplied'],preparation_wall_seconds=time.monotonic()-start,monetary_cost=None,note='Research-assistant model/service cost and human-equivalent time not available; API accounting is external DeepSeek only.'))
    (ROOT/'selected_failure.md').write_text(f'# Selected failure\n\nDiscovery only: {SOURCE_ID}. Full raw observation, task, execution code, resource usage and environment are preserved in discovery/. Final wrong value {oldrow["answer"]}, truth {oldrow["expected"]}. Executed third Python code keeps only2 symbols before comparing3-symbol patterns. This suggests a reusable, independently testable history defect, not proof of a universal Agent limitation. No A historical task enters new hidden evaluation.\n')
    shutil.copyfile(OLD/'environment.json',ROOT/'discovery/environment.json')
    atomic_json(ROOT/'state.json',dict(status='prepared',hidden_evaluated=False))
    print({'prepared':True,'diagnostic_tasks':20,'dev':16,'hidden':44,'brute_crosschecks':len(checks)},flush=True)


def verify():
    m=read_json(ROOT/'manifest.json')
    for f,h in m['sources'].items():assert file_hash(Path(f))==h,f
    for f,h in m['input_hashes'].items():assert file_hash(ROOT/f)==h,f
    assert file_hash(ROOT/'sealed/hidden.json')==m['hidden_sha256']
    return m


async def batch(root,t,rows,arm,code=None):
    sem=asyncio.Semaphore(3);schedule=list(rows);random.Random(2610143009).shuffle(schedule)
    async def one(row):
        async with sem:
            dest=root/'observations'/f"{row['id']}_{arm}.json"
            if dest.exists():return
            begin=time.monotonic()
            try:r=await asyncio.wait_for(solve(root,t,row,arm,SPEC,code),180)
            except BudgetStop:raise
            except Exception as exc:
                r=dict(id=row['id'],arm=arm,tier=row['tier'],scale=row['scale'],expected=row['expected'],answer=None,correct=False,submitted=False,error=type(exc).__name__,events=[],calls=None,tools=None,input_tokens=None,output_tokens=None,unknown_usage=1,tool_seconds=None,budget_violation=False)
            r['actual_latency_seconds']=time.monotonic()-begin;atomic_json(dest,r)
    await asyncio.gather(*(one(r) for r in schedule))


async def diagnose():
    path=ROOT/'diagnosis.json'
    if path.exists():return read_json(path)
    rows=read_json(ROOT/'inputs/diagnostics.json');replays=[]
    src=(ROOT/'discovery/original.py').read_text();repair=(ROOT/'discovery/causal_repair.py').read_text()
    for r in [read_json(ROOT/'discovery/task.json')]+rows:
        a=await execute(src,r['problem'],6);b=await execute(repair,r['problem'],6)
        replays.append(dict(id=r['id'],variant=r.get('variant','discovery'),pair=r.get('pair'),expected=r['expected'],original=a,repair=b,original_correct=a['status']=='success' and a['value']==r['expected'],repair_correct=b['status']=='success' and b['value']==r['expected']))
    atomic_json(ROOT/'program_replays.json',replays)
    d=subroot('diagnostic_agent',2.5,80);t=Transport(d)
    try:await batch(d,t,rows,'reflection')
    finally:atomic_json(d/'usage.json',t.usage());await t.client.close()
    obs={read_json(f)['id']:read_json(f) for f in d.glob('observations/*.json')};signals=[]
    for row in rows:
        r=obs[row['id']];wrong=[e['step'] for e in r['events'] if e.get('tool_result',{}).get('status')=='success' and type(e['tool_result'].get('value')) is int and e['tool_result']['value']!=row['expected']]
        signals.append(dict(id=row['id'],pair=row['pair'],variant=row['variant'],correct=r['correct'],final_failure=not r['correct'],verified_wrong_tool_steps=wrong,recovered_local_failure=bool(wrong) and r['correct'],error=r['error']))
    pairs=[]
    for j in range(10):
        a=next(r for r in signals if r['pair']==j and r['variant']=='trigger');b=next(r for r in signals if r['pair']==j and r['variant']=='control');pairs.append(dict(pair=j,trigger_correct=a['correct'],control_correct=b['correct'],counterfactual_fragility=b['correct'] and not a['correct'],reverse=b['final_failure'] and a['correct']))
    evidence=dict(program={v:dict(n=len(z:=[r for r in replays if r['variant']==v]),original_errors=sum(not r['original_correct'] for r in z),repaired_errors=sum(not r['repair_correct'] for r in z)) for v in ('trigger','control','discovery')},agent_signals=signals,paired_diagnostic=pairs,
        agent_final_errors=sum(s['final_failure'] for s in signals),recovered_local=sum(s['recovered_local_failure'] for s in signals),continue_synthesis=any(not r['original_correct'] and r['repair_correct'] for r in replays if r['variant']=='trigger'),assisted=True,
        note='Synthesis continues to test representational value even if Agent generality unsupported; diagnosis distribution not natural error rate. Exact history mechanism in fresh Agent requires source audit, not inferred from wrong integer.')
    atomic_json(path,evidence)
    (ROOT/'mechanism_diagnosis.md').write_text('# Mechanism diagnosis\n\n'+json.dumps(evidence,ensure_ascii=False,indent=2)+'\n\nProgram-level intervention and fresh Agent results are separate. Short sequence/unreachable pattern non-trigger cases count as counterevidence to an unconditional long-pattern claim. Verified wrong tool integers establish local errors, not their exact cause.\n')
    return evidence


def specification(diag):
    return dict(operator_name='exact_compositional_counter',target_mechanism='premature_history_truncation',preconditions=['valid public CCC input','target mechanism relevance only if a contiguous pattern has length>=3'],input_contract=dict(argument='JSON STRING',fields=['n','alphabet','constraints'],domain='public CCC specification'),output_contract=dict(return_type='nonnegative Python int',arithmetic='exact'),functional_requirements=['satisfy every public constraint, including cases without long patterns','detect complete new patterns before information needed for them is discarded','count every overlapping occurrence','handle concurrent forbidden and count patterns'],invariants=['never compare a length-k pattern to text known shorter than k and treat this as complete checking','preserve sufficient information for all possible future matches','unused constraints must not be assumed'],edge_cases=['pattern longer than sequence','unreachable pattern under adjacency or position restrictions','length1 and2 patterns','overlapping and multiple patterns','no pattern constraint'],non_goals=['dynamic routing','hidden answer caching','complete automatic mechanism discovery'],applicability_boundary=['full solver supports all public valid tasks; target relevance is separate from domain validity'],validation_requirements=['common independent operator development pool only','no hidden task feedback','freeze after at most1 repair'],evidence_summary=diag['program'],assisted=True)


async def generation(group,diag):
    d=ROOT/('generation_'+group)
    if (d/'freeze.json').exists():return read_json(d/'freeze.json')
    overhead=read_json(ROOT/'diagnostic_agent/usage.json')['reserved_or_reported_cost_cny'] if group=='M' else 0
    d=subroot('generation_'+group,max(0,3-overhead),2);t=Transport(d)
    raw=read_json(ROOT/'discovery/original_observation.json');task=read_json(ROOT/'discovery/task.json')
    # SAME raw task, trace, GT and common interface for both; no repaired source or oracle source.
    base='Generate reusable FULL SOLVER solve(problem), JSON STRING input, exact nonnegative integer return. Return JSON {"code":string,"rationale":string} only. Public specification:\n'+SPEC
    base+='\nImports and function definitions ONLY. No files/network/process/classes/globals/dynamic code/private attrs. Allowed imports:'+','.join(sorted(MODULES))+'. Builtins:'+','.join(BUILTINS)+'. Attributes:'+','.join(sorted(ATTRIBUTES))+'. Worker5 CPU seconds,6 wall seconds,256MiB. One candidate and at most one repair.\nDiscovery task and original raw execution:\n'+json.dumps(dict(task=task,observation=raw))
    if group=='M':base+='\nIndependently diagnosed behavioral specification (requirements, not reference implementation):\n'+json.dumps(read_json(ROOT/'operator_specification.json'))
    else:base+='\nAnalyze the raw failure yourself and synthesize a general reusable solver. You may infer and state the failure mechanism in rationale.'
    messages=[dict(role='user',content=base)];attempts=[];old=''
    try:
        for attempt in range(2):
            ad=d/f'attempt_{attempt}';ad.mkdir(exist_ok=True)
            if (ad/'generation.json').exists():g=read_json(ad/'generation.json')
            else:
                try:
                    resp=await t.call(f'generation/{group}/{attempt}',messages,8192);g=parse_json(resp['response']);assert isinstance(g['code'],str)
                except BudgetStop:raise
                except Exception as exc:g=dict(code='',rationale='',error=type(exc).__name__+':'+str(exc))
                atomic_json(ad/'generation.json',g);(ad/'operator.py').write_text(g['code'])
            static=None
            try:validate(g['code'])
            except Exception as exc:static=str(exc)
            checks=[]
            for r in read_json(ROOT/'inputs/operator_development.json'):
                result=await execute(g['code'],r['problem'],6) if static is None else dict(status='error',value=None,error=static)
                checks.append(dict(id=r['id'],expected=r['expected'],problem=r['problem'],result=result,correct=result.get('status')=='success' and result.get('value')==r['expected']))
            v=dict(attempt=attempt,static_error=static,n=len(checks),correct=sum(c['correct'] for c in checks),checks=checks,sha256=file_hash(ad/'operator.py'))
            atomic_json(ad/'validation.json',v);(ad/'change.diff').write_text(''.join(difflib.unified_diff(old.splitlines(True),g['code'].splitlines(True))))
            attempts.append({k:v[k] for k in ('attempt','static_error','n','correct','sha256')})
            print({'group':group,'attempt':attempt,'correct':v['correct'],'n':v['n']},flush=True)
            if v['correct']==v['n']:break
            feedback=[dict(input=json.loads(c['problem']),expected=c['expected'],actual=c['result'].get('value'),error=c['result'].get('error')) for c in checks if not c['correct']][:5]
            messages.extend([dict(role='assistant',content=json.dumps(g)),dict(role='user',content='Return full replacement code/rationale JSON. Common independent validation feedback:\n'+json.dumps(feedback))]);old=g['code']
    finally:atomic_json(d/'usage.json',t.usage());await t.client.close()
    # Freeze even imperfect final program; no selection using hidden outcomes.
    selected=d/f'attempt_{len(attempts)-1}'/'operator.py';shutil.copyfile(selected,d/'operator.py')
    f=dict(group=group,sha256=file_hash(d/'operator.py'),selected_attempt=len(attempts)-1,attempts=attempts,manual_edits=False,diagnostic_api_overhead_reserved_cny=overhead,total_offline_api_reserved_cny=overhead+read_json(d/'usage.json')['reserved_or_reported_cost_cny'],cap_cny=3)
    atomic_json(d/'freeze.json',f);return f


async def run():
    verify();diag=await diagnose()
    if not diag['continue_synthesis']:
        atomic_json(ROOT/'state.json',dict(status='mechanism_not_supported',hidden_evaluated=False));return
    if not (ROOT/'operator_specification.json').exists():atomic_json(ROOT/'operator_specification.json',specification(diag))
    for group in ('F','M'):await generation(group,diag)
    verify()
    for group in ('F','M'):assert file_hash(ROOT/f'generation_{group}/operator.py')==read_json(ROOT/f'generation_{group}/freeze.json')['sha256']
    atomic_json(ROOT/'evaluation_start.json',dict(hidden_seal_sha256=file_hash(ROOT/'sealed/hidden.json'),operators={g:read_json(ROOT/f'generation_{g}/freeze.json')['sha256'] for g in ('F','M')},all_frozen_before_unseal=True))
    hidden=read_json(ROOT/'sealed/hidden.json')
    # Sequential arms, shuffled fixed task order; all arms retain same limits. No result-dependent changes.
    for name,arm,code in [('reflection','reflection',None),('direct','operator',(ROOT/'generation_F/operator.py').read_text()),('mechanism','operator',(ROOT/'generation_M/operator.py').read_text())]:
        d=subroot('evaluation_'+name,8/3,176);t=Transport(d)
        try:await batch(d,t,hidden,arm,code)
        finally:atomic_json(d/'usage.json',t.usage());await t.client.close()
    atomic_json(ROOT/'state.json',dict(status='complete',hidden_evaluated=True));print({'complete':True},flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','run']);p.add_argument('--allow-api',action='store_true');a=p.parse_args()
    if a.command=='prepare':prepare()
    else:
        if not a.allow_api:p.error('--allow-api required')
        with run_lock(ROOT):asyncio.run(run())

if __name__=='__main__':main()
