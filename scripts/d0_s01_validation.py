"""Bounded engineering reference validation and original18 admission; no API."""
import argparse,asyncio,copy,hashlib,itertools,json,math,random,time
from pathlib import Path
import pyarrow.parquet as pq
from scripts.experiment_d0_s01 import ROOT,OLD,verify
from scripts.d0_s01_runtime import execute_case,admit
from scripts.d0_s01_runtime.evaluation import evaluate_task
from scripts.d0_s01_runtime.contracts import taco_task,lcb_task
from scripts.d0_s01_references import REFERENCES
from scripts.offline_loop_protocol import atomic_json,file_hash

def fixed_audit():
 verify();raw=pq.read_table(OLD/'data/taco_ALL_test-00000-of-00001.parquet',columns=['question','input_output','solutions','url']).to_pylist()[11];io=json.loads(raw['input_output']);old=json.loads((OLD/'taco_parsed_admitted.json').read_text())['taco-test-11'];assert io==old['io'] and raw['question']==old['question']
 previous=json.loads((OLD/'data_quality_conflict.json').read_text());proof=[]
 batch=io['inputs'][3].splitlines();assert int(batch[0])==len(batch[1:])==len(io['outputs'][3].split())==100
 for e in previous['counterexamples']:
  s=batch[e['subcase_index']+1];expected=io['outputs'][3].split()[e['subcase_index']];splits=[(s[:a],s[a:b],s[b:c],s[c:]) for a,b,c in itertools.combinations(range(1,len(s)),3)];valid=[x for x in splits if len(set(x))==4];answer='YES' if valid else 'NO';assert s==e['string'] and expected==e['dataset_expected'] and answer==e['exhaustive_answer'] and expected!=answer
  proof.append(dict(**e,rechecked=True))
 atomic_json(ROOT/'taco11_final_audit.json',dict(id='taco-test-11',status='DATA_CONTRACT_UNRESOLVED',quarantined=True,original_revision=json.loads((OLD/'data/metadata_summary.json').read_text())['taco']['sha'],url=raw['url'],raw_io_equals_prior_parse=True,raw_parquet_sha256=file_hash(OLD/'data/taco_ALL_test-00000-of-00001.parquet'),test_index=3,batch_size=100,mapping='subcase i maps to input line i+1 and expected token i; no blank strings or wrapper transformation',proof=proof,reference_language='Original reference0 compiles as Python3 but uses Python2 integer division for string slicing. It is UNSUPPORTED_REFERENCE under current interpreter; eval references remain unsupported. Separate from the independently proven data conflict.',source_claim='Only this fixed task/version; not all TACO',expected_unchanged=True))
 print('TACO11 raw/parsing/wrapper audit: all13 conflicts independently reproduced',flush=True)

def lcs(a,b):
 row=[0]*(len(a)+1)
 for ch in b:
  new=[0]
  for i,x in enumerate(a):new.append(max(row[i+1],new[-1],row[i]+(x==ch)))
  row=new
 return row[-1]

def small_cases():
 rng=random.Random(261019102);out=[]
 def add(tid,interface,case,judge=None):out.append(dict(id=tid,interface=interface,case=case,judge=judge or {'mode':'exact'}))
 for _ in range(32):
  s=''.join(str(rng.randrange(10)) for _ in range(rng.randint(3,8)));n=len(s)-2;left=sum(math.comb(n,i)*int(s[i]) for i in range(n+1))%10;right=sum(math.comb(n,i)*int(s[i+1]) for i in range(n+1))%10
  add('lcb-v6-3768',{'mode':'class','class':'Solution','method':'hasSameDigits','context':'lcb-safe-v1'},{'args':[s],'expected':left==right})
 for x in range(1,82):
  answer=2025-x*sum(x%i==0 and 1<=x//i<=9 for i in range(1,10));add('lcb-v6-abc387_b',{'mode':'stdin'},{'stdin':str(x),'expected':str(answer)}, {'mode':'tokens'})
 for _ in range(32):
  s=''.join(rng.choices('WAX',k=rng.randint(1,15)));t=s
  while 'WA' in t:t=t.replace('WA','AC',1)
  add('lcb-v6-abc394_c',{'mode':'stdin'},{'stdin':s,'expected':t},{'mode':'tokens'})
 for _ in range(32):
  nums=[rng.randrange(10) for _ in range(rng.randint(1,7))];k=rng.randint(1,len(nums));answer=sum(min(s)+max(s) for j in range(1,k+1) for s in itertools.combinations(nums,j))%1000000007
  add('lcb-v6-3714',{'mode':'class','class':'Solution','method':'minMaxSums','context':'lcb-safe-v1'},{'args':[nums,k],'expected':answer})
 for S in ['a','aa','ab','aba','abc']:
  for M in [1,2]:
   answer=[0]*(len(S)+1)
   for letters in itertools.product('abcdefghijklmnopqrstuvwxyz',repeat=M):answer[lcs(S,letters)]+=1
   add('lcb-v6-abc391_g',{'mode':'stdin'},{'stdin':f'{len(S)} {M}\n{S}\n','expected':' '.join(map(str,answer))},{'mode':'tokens'})
 for _ in range(48):
  words=[''.join(rng.choices('ab',k=rng.randint(1,4))) for _ in range(rng.randint(1,7))];k=rng.randint(1,len(words));answer=[]
  for i in range(len(words)):
   remaining=words[:i]+words[i+1:];best=0
   for selected in itertools.combinations(remaining,k):
    d=0
    for chars in zip(*selected):
     if len(set(chars))>1:break
     d+=1
    best=max(best,d)
   answer.append(best)
  add('lcb-v6-3784',{'mode':'class','class':'Solution','method':'longestCommonPrefix','context':'lcb-safe-v1'},{'args':[words,k],'expected':answer})
 return out

async def small():
 verify()
 if (ROOT/'reference_small_results.json').exists():raise FileExistsError('No rerun')
 cases=small_cases();atomic_json(ROOT/'reference_small_cases.json',cases);atomic_json(ROOT/'reference_small_protocol.json',dict(seed=261019102,source_sha256=file_hash(Path(__file__)),reference_sha256=file_hash(Path('scripts/d0_s01_references.py')),units=len(cases),input_hash=file_hash(ROOT/'reference_small_cases.json'),API=0))
 out=[]
 for x in cases:
  r=await execute_case(REFERENCES[x['id']],x['interface'],x['case'],x['judge'],role='diagnostic',log_dir=ROOT/'small_logs');out.append(dict(id=x['id'],passed=r['verdict']=='PASS',result=r))
 atomic_json(ROOT/'reference_small_results.json',out);print('independent exact checks',sum(x['passed'] for x in out),'/',len(out),flush=True)

async def candidates():
 verify()
 if (ROOT/'candidate_admission.json').exists():raise FileExistsError('No rerun')
 original=json.loads((ROOT/'original_smoke_ids.json').read_text());taco=json.loads((OLD/'taco_parsed_admitted.json').read_text());refs=json.loads((OLD/'taco_references.json').read_text());pub=json.loads((OLD/'lcb_public_tasks.json').read_text());private=json.loads((OLD/'lcb_private_cases.json').read_text());A={x['id']:x for x in json.loads((OLD/'dataset_A_smoke.json').read_text())};smallresults=json.loads((ROOT/'reference_small_results.json').read_text())
 atomic_json(ROOT/'candidate_validation_protocol.json',dict(original_ids=[x['id'] for x in original],TACO_reference_indices=[0,1,2],time_cap_per_reference_seconds=75,wrong_programs='One deliberately incorrect constant per admitted task, engineering fixture only; not Agent data.',task_judge='Full source cases with fixed wrappers; no answer edits',source_sha256=file_hash(Path(__file__)),API=0))
 out=[];normalized={};trusted={}
 for item in original:
  tid=item['id'];entry=dict(id=tid,source=item['source'],difficulty=item['difficulty'],reference_attempts=[],status='EXCLUDED',reasons=[],API=0);task=None
  if tid in A:
   from scripts.compositional_counting import exact_dp,brute_force
   a=A[tid];dp=exact_dp(a['problem'])['answer'];brute=brute_force(a['problem']);assert dp==brute==a['expected'];entry.update(status='ADMITTED',reference_evidence='Existing independent exact_dp and exhaustive full sequences agree for this n<=6 input',expected_sha256=hashlib.sha256(str(dp).encode()).hexdigest(),test_cases=1)
   task=dict(id=tid,source='Dataset A',prompt='Public compositional counting specification; '+json.dumps(a['problem']),interface={'mode':'function','function':'solve'},judge={'mode':'exact'},cases=[{'args':[json.dumps(a['problem'])],'expected':dp}],case_wall=6,limits=dict(cpu_seconds=5))
   # Reuse existing independent enumeration source, not a newly synthesized solver.
   import inspect,scripts.compositional_counting as cc
   code='import json,itertools\nKINDS='+repr(cc.KINDS)+'\n'+inspect.getsource(cc.validate_problem)+'\n'+inspect.getsource(cc.brute_force)+'\ndef solve(problem):return brute_force(json.loads(problem))\n';trusted[tid]=code
   full=await evaluate_task(code,task,log_dir=ROOT/'candidate_logs',task_wall=75);entry['reference_check']=full
   if full['verdict']!='PASS':entry.update(status='EXCLUDED',reasons=['REFERENCE_EXECUTION_FAILED'])
  elif tid.startswith('taco-'):
   try:task=taco_task(tid,taco[tid]);ad=admit(task);assert ad['admitted'],ad
   except Exception as e:entry['reasons'].append('UNSUPPORTED_OR_AMBIGUOUS_CONTRACT:'+str(e));task=None
   if task:
    for i in [0,1,2]:
     source=refs[tid][i];r=await evaluate_task(source,task,role='hidden',log_dir=ROOT/'candidate_logs',task_wall=75)
     kind='VALIDATED_REFERENCE' if r['verdict']=='PASS' else 'UNSUPPORTED_REFERENCE' if r['verdict'] in ['POLICY_VIOLATION','INTERFACE_REJECTION','SYNTAX_ERROR','RUNTIME_ERROR'] else 'REFERENCE_RESOURCE_UNVERIFIED' if r['verdict'] in ['TIME_LIMIT','MEMORY_LIMIT','RESOURCE_LIMIT','EVALUATOR_BUDGET_EXHAUSTED'] else 'REFERENCE_TEST_DISAGREEMENT'
     entry['reference_attempts'].append(dict(index=i,source_sha256=hashlib.sha256(source.encode()).hexdigest(),classification=kind,evaluation=r))
     if r['verdict']=='PASS' and tid not in trusted:trusted[tid]=source;entry.update(status='ADMITTED',selected_reference=i)
     print(tid,'ref',i,r['verdict'],r['executed_cases'],flush=True)
    if entry['status']!='ADMITTED':entry['reasons'].append('NO_PREDECLARED_REFERENCE_PASSED_ALL_TESTS; not classified as dataset error without independent proof')
  else:
   task=lcb_task(tid,pub[tid],private[tid]);checks=[x for x in smallresults if x['id']==tid]
   if not checks or not all(x['passed'] for x in checks):entry['reasons'].append('INDEPENDENT_REFERENCE_CHECK_FAILED')
   else:
    source=REFERENCES[tid];r=await evaluate_task(source,task,log_dir=ROOT/'candidate_logs',task_wall=75);entry['reference_check']=r;entry['independent_small_checks']=len(checks)
    if r['verdict']=='PASS':entry.update(status='ADMITTED',reference_evidence='Frozen trusted task-specific engineering reference plus independent exact small checks; all private tests pass');trusted[tid]=source
    else:entry['reasons'].append('FROZEN_REFERENCE_DID_NOT_PASS_ALL_TESTS; no algorithm patch or replacement')
  if task:
   entry['test_cases']=len(task['cases']);entry['interface']=task['interface'];entry['judge']=task['judge'];entry['case_wall']=task.get('case_wall',6);entry['reference_task_cap_seconds']=75;normalized[tid]=task
   if entry['status']=='ADMITTED':
    mode=task['interface']['mode']
    if mode=='stdin':wrong='print("D0_WRONG_SENTINEL")'
    elif mode=='function':wrong='def '+task['interface']['function']+'(*args,**kwargs):return "D0_WRONG_SENTINEL"'
    else:wrong='class Solution:\n def '+task['interface']['method']+'(self,*args,**kwargs):return "D0_WRONG_SENTINEL"'
    w=await evaluate_task(wrong,task,log_dir=ROOT/'candidate_logs',task_wall=75);entry['wrong_program_check']=w
    if w['verdict']!='WRONG_ANSWER':entry.update(status='EXCLUDED');entry['reasons'].append('WRONG_PROGRAM_NOT_RELIABLY_REJECTED')
  out.append(entry);atomic_json(ROOT/'candidate_admission_progress.json',out);print('candidate',tid,entry['status'],flush=True)
 atomic_json(ROOT/'candidate_admission.json',out);atomic_json(ROOT/'normalized_candidate_tasks.json',normalized);atomic_json(ROOT/'trusted_engineering_references.json',trusted)
 print('admitted',sum(x['status']=='ADMITTED' for x in out),'/',len(out),flush=True)
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('command',choices=['fixed','small','candidates']);x=a.parse_args()
 if x.command=='fixed':fixed_audit()
 elif x.command=='small':asyncio.run(small())
 else:asyncio.run(candidates())
