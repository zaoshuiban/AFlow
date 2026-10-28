"""Historical resource-failure replay -> new frozen executable -> design/gate/archive.

No new tasks, calibration, or completed-wrong-only semantic gate. Generation sees
only discovery evidence. Old installed specialist source is never in the prompt.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
import json
import shutil
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

from scripts.offline_loop_protocol import (REPO_ROOT,TEMPLATE_FILES,LoopConfig,RunStore,atomic_json,
    digest,file_hash,read_json,load_rows,code_fingerprints,runtime_environment,run_lock)
from scripts.independent_mechanism_evidence import model_settings,request_hashes,verify_request_hashes
from scripts.mechanism_scope import matches
from scripts.operator_template_isolation import isolate_template
from scripts.operator_library import OperatorLibrary,KNOWN_FEATURES
from scripts.operator_tournament import PromotionPolicy,QualityWeights,calculate_operator_quality
from scripts.offline_loop_runtime import LiveBackend,BudgetExceeded
from scripts.offline_operator_loop import OfflineLoop,candidate_fingerprint
from scripts.reviewed_operator_continuation import validate_pools
from scripts.frozen_math_worker import validate as validate_code

PIPELINE='aflow_operational_replay_v1'
FAILURES={'completed_wrong','output_token_limit','timeout'}


def failure_class(response, raw):
    if not response.get('error'):
        return 'correct' if response['score']==1 else 'completed_wrong'
    errors=[str(r.get('error','')) for r in raw]
    if 'output_token_limit' in errors:return 'output_token_limit'
    if 'TimeoutError' in str(response.get('error')) or any('APITimeoutError' in e for e in errors):return 'timeout'
    return 'infrastructure_error'


def historical_evidence(root):
    """Read an old frozen run without executing its code or requiring current hashes.

    Every frozen input, observation and referenced request remains hash-checked.
    This is an explicit archival import, not permission to resume the old run.
    """
    root=Path(root).resolve();m=read_json(root/'manifest.json')
    if m.get('pipeline')!='aflow_independent_mechanism_evidence_v1':raise ValueError('Expected archived evidence source')
    if read_json(root/'state.json')['manifest_sha256']!=digest(m):raise ValueError('Historical manifest changed')
    for rel,sha in m['input_hashes'].items():
        if file_hash(root/rel)!=sha:raise ValueError('Historical frozen input changed: '+rel)
    summary=read_json(root/'baseline_summary.json');obs=read_json(root/'observations.json');rows=read_json(root/'inputs/samples.json')
    checkpoint=read_json(root/'checkpoints/baseline_complete.json')
    if checkpoint!={'input_sha256':digest(digest(m)),'output_sha256':digest(summary),'output':summary}:
        raise ValueError('Historical baseline completion changed')
    if digest(obs)!=summary['observations_sha256']:raise ValueError('Historical observations changed')
    for rel,sha in summary['request_hashes'].items():
        if file_hash(root/rel)!=sha:raise ValueError('Historical request changed')
    expected={(r['problem_id'],n) for r in rows for n in range(1,m['config']['discovery_runs']+1)}
    if len(obs)!=len(expected) or {(o['problem_id'],o['run']) for o in obs}!=expected:raise ValueError('Incomplete historical observations')
    requests=[read_json(root/p) for p in summary['request_hashes']]
    classified=[]
    for o in obs:
        prefix=f"evidence/baseline/{o['problem_id']}/{o['run']}/"
        raw=[r for r in requests if r['key'].startswith(prefix)]
        if not raw:raise ValueError('Observation lacks original raw request')
        classified.append({**o,'outcome_class':failure_class(o,raw),
                           'raw_failure_excerpt':'\n'.join(str(r.get('response',''))[-2000:] for r in raw if r.get('status')!='completed')})
    return m,rows,classified


def prepare(*,root,evidence_run,design_data,gate_data,regression_data,max_requests=160):
    root=Path(root).resolve();source=Path(evidence_run).resolve()
    if root.exists():raise FileExistsError(root)
    if root.is_relative_to(source):raise ValueError('Output must be outside historical source')
    sm,discovery,observations=historical_evidence(source)
    scope=sm['protocol']['scope'];feature=scope['mechanism_id']
    if feature not in KNOWN_FEATURES:raise ValueError('Operational replay needs an existing routable feature')
    config=replace(LoopConfig(**sm['config']),candidate_count=2,max_requests=max_requests,evaluation_runs=3)
    config.validate()
    if model_settings(config)!=read_json(source/'model_settings.json'):raise ValueError('Model settings differ from historical replay setting')
    paths={'design':Path(design_data),'gate':Path(gate_data),'regression':Path(regression_data)}
    pools={k:load_rows(p) for k,p in paths.items()}
    validate_pools(discovery,pools)
    policy=PromotionPolicy(max_regression_loss_rate=0.0)
    for role in ('design','gate'):
        if sum(matches(scope,r['problem']) for r in pools[role])<policy.min_unique_target_problems:raise ValueError('Too few target problems in '+role)
    for role in ('design','regression'):
        if sum(not matches(scope,r['problem']) for r in pools[role])<config.min_regression_problems:raise ValueError('Too few boundary problems in '+role)
    # Targets execute locally; every boundary can require a fresh two-stage fallback.
    bound=2+config.evaluation_runs*(2*len(pools['design'])+4*sum(not matches(scope,r['problem']) for r in pools['design'])+2*len(pools['gate'])+4*len(pools['regression']))
    if max_requests<bound:raise ValueError(f'Need request budget at least {bound}')
    sources={n:(source/'base_template'/n).read_text() for n in TEMPLATE_FILES}
    sources,isolation=isolate_template(sources)
    module='aflow_operational_'+digest(str(root))[:16]
    root.mkdir(parents=True)
    shutil.copytree(source,root/'historical_evidence',ignore=shutil.ignore_patterns('.lock','__pycache__'))
    copied=historical_evidence(root/'historical_evidence')
    if copied!=(sm,discovery,observations):raise ValueError('Historical source changed during prepare')
    for role,rows in {'discovery':discovery,**pools}.items():atomic_json(root/'inputs'/f'{role}.json',rows)
    atomic_json(root/'inputs/history.json',observations)
    empty=OperatorLibrary._empty_data()
    atomic_json(root/'inputs/base_library.json',empty);atomic_json(root/'work/operator_library/operators.json',empty)
    for name,text in sources.items():
        p=root/'base_template'/name;p.parent.mkdir(exist_ok=True);p.write_text(text.replace(sm['module_name'],module))
    atomic_json(root/'model_settings.json',model_settings(config))
    hashes=code_fingerprints()
    for rel,sha in hashes.items():
        dest=root/'frozen_code'/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((REPO_ROOT/rel).read_bytes())
        if file_hash(dest)!=sha:raise ValueError('Code changed during freeze')
    manifest={'pipeline':PIPELINE,'schema_version':1,'config':asdict(config),'module_name':module,
        'counts':{k:len(v) for k,v in {'discovery':discovery,**pools}.items()},'scope':scope,'routing_feature':feature,
        'source_evidence':{'original_path':str(source),'manifest_sha256':digest(sm),'observation_sha256':digest(observations)},
        'evaluation_sources':{k:{'path':str(p.resolve()),'sha256':file_hash(p)} for k,p in paths.items()},
        'evidence_mode':'historical_replay','evaluation_mode':'historical_replay',
        'template_isolation':isolation,'baseline':'generic_two_stage','generation_backend':'frozen_generated_solve_problem_v1',
        'confirmation_evidence_source':'historical_operational_replay_confirmation',
        'scientific_scope':'Historical constructed-task replay under resource limits; NOT new natural-data or causal evidence',
        'operational_policy':{'accepted_failures':sorted(FAILURES),'network_errors':'excluded; any invalid evaluation pair blocks promotion',
            'min_stable_problems':config.min_stable_problems,'min_failed_repeats':config.min_wrong_repeats,
            'candidate_worker':{'wall_seconds':6,'cpu_seconds':5,'rss_threshold_bytes':256*1024**2,'memory_enforcement':'best-effort RSS watchdog, not a hard address-space cap'},
            'old_code_visible_to_generator':False,'candidate_generation_requests':2,'no_hidden_retry':True,
            'tool_access_confound':'Comparison proves resource-bounded executable pipeline utility, not superiority to a generic programmer control'},
        'request_upper_bound':bound,'promotion_policy':asdict(policy),'quality_weights':asdict(QualityWeights()),
        'code_hashes':hashes,'runtime_environment':runtime_environment(),
        'input_hashes':{str(p.relative_to(root)):file_hash(p) for directory in ('inputs','base_template','historical_evidence','frozen_code')
                        for p in sorted((root/directory).rglob('*')) if p.is_file()}}
    manifest['input_hashes']['model_settings.json']=file_hash(root/'model_settings.json')
    atomic_json(root/'manifest.json',manifest);atomic_json(root/'state.json',{'manifest_sha256':digest(manifest),'status':'prepared'})
    return manifest


class OperationalStore(RunStore):
    def __init__(self,root,progress=None):
        super().__init__(Path(root).resolve(),progress)
        if self.manifest.get('pipeline')!=PIPELINE:raise ValueError('Not an operational replay')
        for p in (self.root/'checkpoints').rglob('*.json'):
            saved=read_json(p);output=saved.get('output')
            if isinstance(output,dict) and '_request_hashes' in output:
                if digest(output)!=saved['output_sha256']:raise ValueError('Checkpoint changed')
                verify_request_hashes(self,output['_request_hashes'])
                if output.get('name') and output.get('fingerprint'):
                    library=OperatorLibrary(self.root/'work/operator_library/operators.json',auto_create=False)
                    if candidate_fingerprint(library.get(output['name']))!=output['fingerprint']:
                        raise ValueError('Generated candidate changed')


class OperationalBackend(LiveBackend):
    async def generate_executable(self,context,index):
        prompt=('Create ONE reusable Python mathematical operator from the operational failure evidence below. '
                'Return JSON with string fields code and rationale. Define solve(problem), parsing the supplied question text, '
                'returning a nonnegative integer, or None for unsupported inputs. No fixed example lookup tables. '
                'The same code will run on unseen parameter values. Do not read files or use network/process/reflection/dynamic code. '
                'Allowed imports: math,itertools,re,collections,functools,fractions. '
                'Standard finite arithmetic, loops, recursion, containers and regular expression parsing are available. '
                'No classes or global/nonlocal statements; no private or dunder attributes. 5 CPU seconds, 256 MiB monitored RSS threshold per input. '
                'Old specialist code and evaluation tasks are intentionally absent. '
                f'Candidate attempt {index+1} of 2; propose an independently justified implementation.\n'+json.dumps(context,ensure_ascii=False))
        llm=self.llm(f'operational_generation/{index}','opt')
        try:
            raw=await llm(prompt)
            if raw.strip().startswith('```'):raw=raw.strip().split('\n',1)[1].rsplit('```',1)[0]
            value=json.loads(raw)
            if set(value)!={'code','rationale'} or any(not isinstance(v,str) for v in value.values()):raise ValueError('Invalid executable response schema')
            validate_code(value['code'])
            return value
        finally:await llm.close()


def wrapper(name,code,scope):
    return f'''class {name}(Operator):
    def __init__(self, llm, name={name!r}):
        super().__init__(llm, name)
    async def __call__(self, problem):
        from scripts.mechanism_scope import matches
        from scripts.frozen_math_execution import run_frozen_math
        if not matches({scope!r}, problem):
            return {{'status':'declined','response':'','observation':'outside_frozen_scope'}}
        return await run_frozen_math({code!r}, problem)
'''


class OperationalLoop(OfflineLoop):
    def split_scope(self,rows,screen):
        scope=self.store.manifest['scope']
        return ([r for r in rows if matches(scope,r['problem'])],[r for r in rows if not matches(scope,r['problem'])])

    def baseline_is_infrastructure(self,row):
        return row['baseline'].get('outcome_class')=='infrastructure_error'

    async def discover(self):
        observations=read_json(self.store.root/'inputs/history.json')
        targets={r['problem_id'] for r in self.store.rows('discovery') if matches(self.store.manifest['scope'],r['problem'])}
        counts=Counter(o['problem_id'] for o in observations if o['problem_id'] in targets and o['outcome_class'] in FAILURES)
        stable=sorted(pid for pid,n in counts.items() if n>=self.config.min_wrong_repeats)
        selected={'features':[self.store.manifest['routing_feature']],'stable_problem_ids':stable} if len(stable)>=self.config.min_stable_problems else None
        screen={'selected':selected,'stable_problem_count':len(stable),'outcome_counts':dict(Counter(o['outcome_class'] for o in observations)),
                'claim':'observed resource-bounded failure; not a semantic cause proof'}
        return screen,observations

    async def generate(self,screen,observations):
        ids=set(screen['selected']['stable_problem_ids'])
        context={'scope':self.store.manifest['scope'],'claim':screen['claim'],'cases':[
            {'problem':r['problem'],'reference_solution':r['solution'],'failures':[
                {'run':o['run'],'outcome_class':o['outcome_class'],'prediction_excerpt':o.get('prediction','')[-2000:],
                 'resource_failure_excerpt':o.get('raw_failure_excerpt','')}
                for o in observations if o['problem_id']==r['problem_id'] and o['outcome_class'] in FAILURES]}
            for r in self.store.rows('discovery') if r['problem_id'] in ids]}
        # All accepted implementations are frozen before any development evaluation.
        names=[]
        for i in range(self.config.candidate_count):
            key=f'operational_candidate/{i}';inputs={'context':context,'index':i}
            saved=self.store.get(key,inputs)
            if saved is None:
                try:
                    proposal=await self.backend.generate_executable(context,i)
                    validate_code(proposal['code'])
                    name=f'OperationalGeneratedCandidate{i+1}'
                    record=self.library.register_evolved_candidate(name=name,description='Generated reusable exact computation for '+self.store.manifest['routing_feature'],
                        module=self.store.manifest['module_name']+'.operator',skeleton_type='frozen_generated_python',
                        applicability_features=self.store.manifest['routing_feature'],failure_features=[],
                        provenance={'kind':'model_generated_frozen_python','operational_context_sha256':digest(context),
                                    'rationale':proposal['rationale'],'source_manifest_sha256':digest(self.store.manifest),'old_specialist_copied':False},
                        class_code=wrapper(name,proposal['code'],self.store.manifest['scope']),prompt_code='')
                    saved={'name':name,'fingerprint':candidate_fingerprint(record),'proposal':proposal}
                except BudgetExceeded:raise
                except (ValueError,SyntaxError,RuntimeError) as exc:
                    saved={'rejected':True,'reason':str(exc)}
                saved['_request_hashes']=request_hashes(self.store,f'operational_generation/{i}/')
                self.store.put(key,inputs,saved)
            verify_request_hashes(self.store,saved['_request_hashes'])
            if saved.get('rejected'):continue
            if candidate_fingerprint(self.library.get(saved['name']))!=saved['fingerprint']:raise ValueError('Generated candidate changed')
            names.append(saved['name'])
        if not names:raise ValueError('No valid generated executable candidates')
        return names

    async def execute(self,*,item,stage,repeat,operator=None,regression=False):
        key=f"{stage}/{operator or 'baseline'}/{item['problem_id']}/{repeat}"
        inputs={'item':item,'operator':operator,'regression':regression,'fingerprint':candidate_fingerprint(self.library.get(operator)) if operator else None}
        result=self.store.get(key,inputs)
        if result is None:
            result=await self.backend.execute(item=item,operator=operator,regression=regression,key=key)
            hashes=request_hashes(self.store,key+'/');raw=[read_json(self.store.root/p) for p in hashes]
            outcome=failure_class(result,raw)
            if operator and result.get('error') and not result.get('used_fallback'):
                outcome='candidate_execution_error'
            result={**result,'outcome_class':outcome,'_request_hashes':hashes}
            self.store.put(key,inputs,result)
        verify_request_hashes(self.store,result['_request_hashes'])
        return {**result,'problem_id':item['problem_id'],'run':repeat}

    async def compare(self,name,targets,regressions,stage):
        result=await super().compare(name,targets,regressions,stage)
        invalid=[r for r in result['rows'] if any(r[arm].get('outcome_class')=='infrastructure_error' for arm in ('baseline','candidate'))]
        if invalid:
            # The pair is not evidence of an operational win. Recalculate only
            # valid pairs, then fail closed for incomplete planned comparisons.
            valid=[r for r in result['rows'] if r not in invalid]
            if any(r['split']=='target' for r in valid):
                metrics=calculate_operator_quality([r for r in valid if r['split']=='target'],[r for r in valid if r['split']=='regression'],weights=self.weights,policy=self.policy)
            else:
                metrics={'quality_score':-1.0,'passed':False,'wins':0,'losses':0,'net_wins':0,
                         'gate_failures':['no valid target pairs']}
            metrics['gate_failures'].append('infrastructure errors in planned pairs; no promotion')
            metrics['passed']=False
            result={**result,'metrics':metrics,'invalid_pair_count':len(invalid)}
        else:result={**result,'invalid_pair_count':0}
        inputs={'operator':name,'fingerprint':candidate_fingerprint(self.library.get(name)),'targets':targets,'regressions':regressions}
        return self.store.put(f'{stage}_metrics/{name}',inputs,result)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare');q.add_argument('--output-dir',type=Path,required=True);q.add_argument('--evidence-run',type=Path,required=True)
    for role in ('design','gate','regression'):q.add_argument('--'+role+'-data',type=Path,required=True)
    q.add_argument('--max-requests',type=int,default=160)
    for name in ('run','status'):
        q=sub.add_parser(name);q.add_argument('--output-dir',type=Path,required=True)
        if name=='run':q.add_argument('--allow-api',action='store_true')
    a=p.parse_args()
    if a.command=='prepare':
        with run_lock(a.evidence_run):m=prepare(root=a.output_dir,evidence_run=a.evidence_run,design_data=a.design_data,gate_data=a.gate_data,regression_data=a.regression_data,max_requests=a.max_requests)
        print(json.dumps({'status':'prepared','request_upper_bound':m['request_upper_bound'],'model_calls':0}))
    elif a.command=='status':
        store=OperationalStore(a.output_dir);screen,_=asyncio.run(OperationalLoop(store,None).discover())
        print(json.dumps({'state':read_json(store.root/'state.json'),'screen':screen},indent=2))
    else:
        if not a.allow_api:raise SystemExit('run requires --allow-api')
        with run_lock(a.output_dir):
            store=OperationalStore(a.output_dir,progress=print)
            try:print(json.dumps(asyncio.run(OperationalLoop(store,OperationalBackend(store)).run()),indent=2))
            except BudgetExceeded:store.status('budget_exhausted');raise

if __name__=='__main__':main()
