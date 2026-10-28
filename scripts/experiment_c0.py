"""C0 development-only operator validation pilot. Never generates or repairs solvers."""
import argparse,asyncio,copy,hashlib,itertools,json,random,time
from pathlib import Path
from scripts.offline_loop_protocol import atomic_json,file_hash
from scripts.experiment_b18 import snapshot as previous_snapshot
from scripts.compositional_counting import KINDS,SPEC,validate_problem,exact_dp,brute_force
from scripts.operator_runtime import execute,Resources
from scripts.operator_runtime.executor import static_check
ROOT=Path('results/experiment_c0_20261015_v1')
SEEDS=[261015001,261015002,261015003]
BUDGET=24

def history():
 d=previous_snapshot()
 for folder in ['results/experiment_b18_20261015_v1']+[str(p) for p in Path('results').glob('experiment_a_pilot*')]:
  for p in Path(folder).rglob('*'):
   if p.is_file() and '__pycache__' not in str(p):d[str(p)]=file_hash(p)
 return d

def canonical(p):return json.dumps(p,sort_keys=True,separators=(',',':'))
def phash(p):return hashlib.sha256(canonical(p).encode()).hexdigest()

def random_problem(rng):
 n=rng.randint(1,7);cs=[]
 for k in rng.sample(list(KINDS),rng.randint(0,7)):
  if k=='count':c=dict(kind=k,symbol=rng.randrange(4),exact=rng.randint(0,n))
  elif k=='mod':
   m=rng.randint(2,13);c=dict(kind=k,modulus=m,residue=rng.randrange(m))
  elif k=='forbidden':c=dict(kind=k,patterns=[''.join(rng.choices('0123',k=rng.randint(1,4))) for _ in range(rng.randint(1,3))])
  elif k=='pattern_count':
   pat=''.join(rng.choices('0123',k=rng.randint(1,4)));c=dict(kind=k,pattern=pat,exact=rng.randint(0,max(0,n-len(pat)+1)))
  elif k=='position':c=dict(kind=k,parity=rng.choice(['even','odd']),forbidden_symbol=rng.randrange(4))
  else:c=dict(kind=k)
  cs.append(c)
 return validate_problem(dict(n=n,alphabet=[0,1,2,3],constraints=sorted(cs,key=lambda c:c['kind'])))

def transform(p,rule,rng):
 """All transforms are legal public-schema transformations; no target source input."""
 p=copy.deepcopy(p);cs={c['kind']:c for c in p['constraints']};n=p['n']
 if rule=='endpoint_dependency':
  p['n']=rng.choice([1,2,3,5,7]);n=p['n'];cs['endpoints']={'kind':'endpoints'}
  # Endpoint interactions are explicit public-spec tests too.
  if rng.random()<.5:cs['position']=dict(kind='position',parity=rng.choice(['even','odd']),forbidden_symbol=rng.randrange(4))
 elif rule=='pattern_history_boundary':
  L=rng.choice([1,2,3,4]);pat=''.join(rng.choices('0123',k=L));p['n']=rng.choice([max(1,L-1),L,min(7,L+2)]);n=p['n']
  cs['forbidden']=dict(kind='forbidden',patterns=[pat,pat[:1]]) if rng.random()<.3 else dict(kind='forbidden',patterns=[pat])
  cs['pattern_count']=dict(kind='pattern_count',pattern=pat,exact=0)
 elif rule=='representation_and_overlap':
  # Bijective symbol renaming preserves pattern equality and overlap structure.
  perm=rng.sample(list('0123'),4);pat=''.join(perm[int(d)] for d in rng.choice(['00','010','0101','012']))
  p['n']=rng.choice([len(pat),min(7,len(pat)+2),7]);n=p['n']
  cs['pattern_count']=dict(kind='pattern_count',pattern=pat,exact=rng.choice([0,1,max(0,n-len(pat)+1)]))
  if rng.random()<.5:cs['forbidden']=dict(kind='forbidden',patterns=[perm[3]])
 for c in cs.values():
  if c['kind']=='count':c['exact']=min(c['exact'],n)
  if c['kind']=='pattern_count':c['exact']=min(c['exact'],max(0,n-len(c['pattern'])+1))
 p['constraints']=sorted(cs.values(),key=lambda c:c['kind']);return validate_problem(p)

def features(p):
 cs={c['kind']:c for c in p['constraints']};k=sorted(cs);f={'n:'+str(p['n']),'complexity:'+str(len(k))}
 f.update('kind:'+x for x in k);f.update('pair:'+a+'+'+b for a,b in itertools.combinations(k,2))
 for c in cs.values():
  if c['kind']=='count':f.add('count_boundary:'+('zero' if c['exact']==0 else 'all' if c['exact']==p['n'] else 'interior'))
  if c['kind']=='position':f.add('position:'+c['parity'])
  if c['kind']=='mod':f.add('modulus:'+str(c['modulus']))
  pats=c.get('patterns',[]) if c['kind']=='forbidden' else [c['pattern']] if c['kind']=='pattern_count' else []
  for s in pats:
   f.add('pattern_length:'+str(len(s)));f.add('n_vs_pattern:'+('below' if p['n']<len(s) else 'equal' if p['n']==len(s) else 'above'))
   f.add('overlap:'+str(any(s[i:]==s[:-i] for i in range(1,len(s)))))
  if c['kind']=='forbidden':f.add('forbidden_count:'+str(len(pats)));f.add('mixed_lengths:'+str(len(set(map(len,pats)))>1))
  if c['kind']=='pattern_count':f.add('occ_boundary:'+('zero' if c['exact']==0 else 'max' if c['exact']==max(0,p['n']-len(c['pattern'])+1) else 'interior'))
 return f

def failure_priority(p):
 cs={c['kind']:c for c in p['constraints']};score=0
 # Historical evidence sets priority only; no target label or source is accepted.
 if 'endpoints' in cs:score+=2+(p['n']>1)+('count' in cs)+('position' in cs)
 pats=cs.get('forbidden',{}).get('patterns',[])+([cs['pattern_count']['pattern']] if 'pattern_count' in cs else [])
 if pats:score+=2
 if any(len(s)>=3 and p['n']>=len(s) for s in pats):score+=3
 if 'pattern_count' in cs and p['n']>=len(cs['pattern_count']['pattern']):score+=2
 return score

def strategies(seed):
 rng=random.Random(seed);pool={}
 for _ in range(160):
  p=random_problem(rng);pool[phash(p)]=dict(problem=p,origin='public_random_pool')
 for rule in ['endpoint_dependency','pattern_history_boundary','representation_and_overlap']:
  for _ in range(24):
   p=transform(random_problem(rng),rule,rng);pool[phash(p)]=dict(problem=p,origin=rule)
 rows=list(pool.values());rng.shuffle(rows)
 # Strong G sees entire same pool, including all historical-rule generated inputs.
 def greedy(candidates,chosen,covered,priority=False):
  return max(candidates,key=lambda x:((failure_priority(x['problem']) if priority else 0),len(features(x['problem'])-covered)))
 remaining=rows[:];g=[];covered=set()
 while len(g)<BUDGET:
  x=greedy(remaining,g,covered);g.append(x);covered|=features(x['problem']);remaining.remove(x)
 # F spends first half on exactly G's public coverage then ranks supplemental tests.
 f=g[:BUDGET//2];covered=set().union(*(features(x['problem']) for x in f));remaining=[x for x in rows if x not in f]
 while len(f)<BUDGET:
  x=greedy(remaining,f,covered,True);f.append(x);covered|=features(x['problem']);remaining.remove(x)
 rr=[];used=set();rng=random.Random(seed+999)
 while len(rr)<BUDGET:
  p=random_problem(rng);h=phash(p)
  if h not in used:rr.append(dict(problem=p,origin='uniform_legal_schema_random'));used.add(h)
 return dict(R=rr,G=g,F=f),rows

def prepare():
 if ROOT.exists():raise FileExistsError('No overwrite')
 ROOT.mkdir(parents=True);atomic_json(ROOT/'history_before.json',history());(ROOT/'sources').mkdir()
 paths=sorted(Path('results').glob('experiment_a_pilot*/candidates/*.py'))+sorted(Path('results/generated_operator_experiment_a_v1/candidates').glob('*/*/operator.py'))+sorted(Path('results/experiment_b0_20261014_v1').glob('generation_*/*/operator.py'))
 candidates={}
 for p in paths:
  code=p.read_text();h=file_hash(p)
  if h in candidates:candidates[h]['aliases'].append(str(p));continue
  try:static_check(code);error=None
  except (ValueError,SyntaxError) as e:error=str(e)
  cid='c'+str(len(candidates)+1).zfill(2);dest=ROOT/'sources'/f'{cid}.py';dest.write_text(code)
  family=str(p.parent) if '/candidates/candidate_' in str(p) and '/attempt_' not in str(p) else str(p.parent.parent)
  candidates[h]=dict(id=cid,sha256=h,source=str(dest),aliases=[str(p)],lineage=family,data_role='DEVELOPMENT_PREVIOUSLY_VALIDATED_OR_REVIEWED',eligible=error is None,static_error=error,origin='LLM historical generation; no manual source edits')
 atomic_json(ROOT/'candidate_inventory.json',list(candidates.values()))
 rules=[dict(id='endpoint_dependency',evidence='B1 M1_original/M1_repaired; B1 strict first-state interventions',transform='Add endpoint constraint and vary n, count/position context.',ordinary_spec_equivalent=True,novel_contract=False),dict(id='pattern_history_boundary',evidence='B1 M3_original/M3_repaired; B0 compound diagnosis as non-independent supporting context',transform='Vary pattern L=1..4 and n below/equal/above L; mixed forbidden lengths; exact-zero occurrences.',ordinary_spec_equivalent=True,novel_contract=False),dict(id='representation_and_overlap',evidence='B1 posthoc M2 minimal representation interventions and B1.5/B1.6 causal source audits',transform='Rename digit alphabet in pattern templates; vary overlap and occurrence boundaries.',ordinary_spec_equivalent=True,novel_contract=False)]
 atomic_json(ROOT/'failure_rule_manifest.json',rules)
 alltests=[];pools=[];start=time.monotonic()
 for seed in SEEDS:
  plans,pool=strategies(seed);pools.append(dict(seed=seed,cases=pool))
  for method,rows in plans.items():
   for i,row in enumerate(rows):alltests.append(dict(seed=seed,method=method,index=i+1,**row,problem_sha256=phash(row['problem'])))
 atomic_json(ROOT/'test_plans.json',alltests);atomic_json(ROOT/'shared_public_pools.json',pools)
 atomic_json(ROOT/'construction_accounting.json',dict(plan_construction_seconds=time.monotonic()-start,new_API_requests=0,API_cny=0,human_assistant_design_cost='UNPRICED_NOT_ZERO',historical_diagnosis_build_cost='REUSED_NOT_FREE',cost_ablation='No blinded rule synthesis; construction cost savings unmeasured'))
 sources={str(p):file_hash(p) for p in (ROOT/'sources').glob('*.py')}
 for p in [Path(__file__),Path('scripts/compositional_counting.py')]+list(Path('scripts/operator_runtime').glob('*.py')):sources[str(p)]=file_hash(p)
 atomic_json(ROOT/'protocol.json',dict(experiment='C0',design='DEVELOPMENT_ONLY_PILOT; no eligible independent confirmation operators',seeds=SEEDS,calls_per_candidate_strategy_seed=BUDGET,prefix_budgets=[4,8,16,24],same_candidate_same_oracle=True,F_no_target_source_access=True,G_has_all_F_pool_inputs=True,selection='G greedy coverage; F identical first12 G then frozen history priority; R random legal schema, not uniform over all valid instances',resources=dict(wall_seconds=1,CPU_seconds=3,memory_MiB=256),reference='exact_dp crosschecked against independent brute for every unique n<=7 input; no oracle use in selection',cost_budget='Equal validation call opportunity; all24 calls executed even after first failure. Actual time and construction cost separately reported; no equal-total-time claim.',repetitions='3 seed orders; not independent operator samples',lineage='Multiple repair versions are correlated, report source and lineage levels',gate='Continue only stable F-only genuine functional defect on independent candidates at matched budget and acceptable total cost. No independent candidates => gate cannot pass.',stop='No increment over G => stop F strategy; known-neighbor-only increment => regression-only; no new API under this protocol',API_cap_cny=0,synthetic_faults=False,coverage_guided='No existing mature operator-input feedback coverage implementation reused; G uses input structural coverage, not code coverage',frozen={**sources,**{str(ROOT/f):file_hash(ROOT/f) for f in ['candidate_inventory.json','failure_rule_manifest.json','test_plans.json','shared_public_pools.json']}}))
 print(dict(candidates=len(candidates),eligible=sum(x['eligible'] for x in candidates.values()),tests=len(alltests)),flush=True)

def verify():
 p=json.loads((ROOT/'protocol.json').read_text())
 for f,h in p['frozen'].items():assert file_hash(Path(f))==h,f
 assert history()==json.loads((ROOT/'history_before.json').read_text()),'Historical mutation'
 return p

async def run():
 protocol=verify()
 if (ROOT/'run_started.json').exists():raise FileExistsError('No silent rerun')
 atomic_json(ROOT/'run_started.json',dict(time=time.time(),protocol_sha256=file_hash(ROOT/'protocol.json')))
 plans=json.loads((ROOT/'test_plans.json').read_text());truth={};start=time.monotonic()
 for x in plans:
  h=x['problem_sha256']
  if h in truth:continue
  p=x['problem'];t=time.monotonic();dp=exact_dp(p);dpwall=time.monotonic()-t;t=time.monotonic();b=brute_force(p);bw=time.monotonic()-t
  assert dp['answer']==b
  truth[h]=dict(problem=p,answer=b,dp=dp,dp_wall_seconds=dpwall,brute_wall_seconds=bw)
 atomic_json(ROOT/'oracle_outputs.json',truth);print(dict(oracle_cases=len(truth),seconds=time.monotonic()-start),flush=True)
 candidates=[x for x in json.loads((ROOT/'candidate_inventory.json').read_text()) if x['eligible']]
 # Interleave methods/seed/index in a result-independent frozen RNG order.
 rng=random.Random(261015991);rng.shuffle(candidates)
 with (ROOT/'executions.jsonl').open('x') as log:
  for c in candidates:
   code=Path(c['source']).read_text();schedule=plans[:];rng.shuffle(schedule)
   for x in schedule:
    st=time.monotonic();out=await execute(code,canonical(x['problem']),1,resources=Resources(cpu_seconds=3));elapsed=time.monotonic()-st
    expected=truth[x['problem_sha256']]['answer'];functional=out.get('status')=='success' and out.get('value')!=expected
    row=dict(candidate=c['id'],candidate_sha256=c['sha256'],seed=x['seed'],method=x['method'],index=x['index'],problem_sha256=x['problem_sha256'],expected=expected,execution=out,wall_seconds=elapsed,functional_failure=functional,classification='FUNCTIONAL_MISMATCH' if functional else 'CORRECT' if out.get('status')=='success' else out.get('error_type','UNKNOWN'))
    log.write(json.dumps(row)+'\n');log.flush()
   print(dict(completed=c['id'],units=len(schedule)),flush=True)
 atomic_json(ROOT/'run_complete.json',dict(time=time.time(),history_unchanged=history()==json.loads((ROOT/'history_before.json').read_text())))

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('command',choices=['prepare','run']);x=a.parse_args()
 if x.command=='prepare':prepare()
 else:asyncio.run(run())
