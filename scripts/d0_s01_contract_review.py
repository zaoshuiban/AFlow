"""Independent input-contract review; never changes tests/answers or model code."""
import json,re,time
from pathlib import Path
from scripts.experiment_d0_s01 import ROOT,OLD,verify
from scripts.offline_loop_protocol import atomic_json,file_hash

def check_taco(tid,case):
 if tid=='taco-test-40':
  assert len(case['args'])==1 and type(case['args'][0]) is int and case['args'][0]>0;return
 vals=list(map(int,case['stdin'].split()));it=iter(vals)
 def take():return next(it)
 if tid=='taco-test-224':
  t=take();assert 1<=t<=200
  for _ in range(t):
   n,r,m=take(),take(),take();assert 1<=r<=n<=10**9 and 1<=m<=10**9
   for p in [2,3,5,7,11,13,17,19,23,29,31,37,41,43,47]:
    if m%p==0:m//=p;assert m%p!=0
   assert m==1
 elif tid=='taco-test-715':
  t=take();assert 1<=t<=10000;ns=ks=0
  for _ in range(t):
   n,k=take(),take();assert 2<=n<=200000 and n%2==0 and 1<=k<=200000;ns+=n;ks+=k
   for _ in range(n):assert 1<=take()<=k
  assert ns<=200000 and ks<=200000
 elif tid=='taco-test-67':
  n=take();assert 3<=n<=3000;edges=set();adj=[[] for _ in range(n)]
  for _ in range(n):
   a,b=take()-1,take()-1;assert 0<=a<n and 0<=b<n and a!=b;edge=tuple(sorted([a,b]));assert edge not in edges;edges.add(edge);adj[a].append(b);adj[b].append(a)
  seen={0};stack=[0]
  while stack:
   a=stack.pop()
   for b in adj[a]:
    if b not in seen:seen.add(b);stack.append(b)
  assert len(seen)==n
 elif tid=='taco-test-110':
  t=take();assert 1<=t<=100000
  for _ in range(t):
   for _ in range(4):assert 1<=take()<=10**6
 elif tid=='taco-test-237':
  m,n=take(),take();assert 1<=m<=100000 and 1<=n<=100000
  for _ in range(m+n):assert 1<=take()<=10**9
  total=0
  for _ in range(m):
   size=take();assert 1<=size<=n;total+=size;items=[take() for _ in range(size)];assert len(set(items))==size and all(1<=x<=n for x in items)
  assert total<=200000
 else:raise ValueError('Unknown contract')
 try:next(it);raise AssertionError('Trailing input tokens')
 except StopIteration:pass

def check_lcb(tid,c):
 if tid=='lcb-v6-3768':
  s,=c['args'];assert type(s) is str and 3<=len(s)<=100 and re.fullmatch('[0-9]+',s)
 elif tid=='lcb-v6-abc387_b':
  v=c['stdin'].split();assert len(v)==1 and 1<=int(v[0])<=81
 elif tid=='lcb-v6-abc394_c':
  s=c['stdin'].strip();assert 1<=len(s)<=300000 and re.fullmatch('[A-Z]+',s)
 elif tid=='lcb-v6-3714':
  a,k=c['args'];assert type(a) is list and 1<=len(a)<=100000 and type(k) is int and 1<=k<=min(70,len(a)) and all(type(x) is int and 0<=x<=10**9 for x in a)
 elif tid=='lcb-v6-abc391_g':
  n,m,s=c['stdin'].split();n=int(n);m=int(m);assert 1<=n<=10 and 1<=m<=100 and len(s)==n and re.fullmatch('[a-z]+',s)
 elif tid=='lcb-v6-3784':
  a,k=c['args'];assert 1<=k<=len(a)<=100000 and all(type(s) is str and 1<=len(s)<=10000 and re.fullmatch('[a-z]+',s) for s in a) and sum(map(len,a))<=100000
 else:raise ValueError('Unknown contract')

def main():
 verify()
 if (ROOT/'candidate_contract_review.json').exists():raise FileExistsError('No rerun')
 atomic_json(ROOT/'contract_review_protocol.json',dict(input_checks='All original normalized cases checked against public input schema and numeric bounds. Whitespace-insensitive token parsing, exact arity/length/range, graph and set invariants. No output changes.',status='Data conflicts isolate task; reference runtime errors alone do not prove unsupported language or data error.',source_sha256=file_hash(Path(__file__)),API=0))
 tasks=json.loads((ROOT/'normalized_candidate_tasks.json').read_text());entries=json.loads((ROOT/'candidate_admission.json').read_text());review=[];final=[]
 for e in entries:
  tid=e['id'];bad=[];t=tasks.get(tid)
  if t and not tid.startswith('d0-count'):
   f=check_taco if tid.startswith('taco-') else check_lcb
   for i,c in enumerate(t['cases']):
    try:f(tid,c)
    except (AssertionError,StopIteration,ValueError,TypeError,IndexError) as exc:
     import hashlib
     bad.append(dict(test_index=i,error=type(exc).__name__+': '+str(exc),input_sha256=hashlib.sha256(json.dumps({k:v for k,v in c.items() if k!='expected'},sort_keys=True).encode()).hexdigest(),input_preview=str({k:v for k,v in c.items() if k!='expected'})[:200]))
  r=dict(id=tid,reviewed_cases=len(t['cases']) if t else 0,invalid_input_cases=bad,status='DATA_CONTRACT_UNRESOLVED' if bad else 'NO_INPUT_CONFLICT_DETECTED')
  review.append(r);z=e.copy();z['initial_reference_admission']=e['status'];z['contract_review']=r
  if bad:z['status']='EXCLUDED';z['reasons']=z['reasons']+['DATA_CONTRACT_UNRESOLVED: public input constraints violated; do not count as Agent failure']
  z['reference_classification_review']=[]
  for a in e['reference_attempts']:
   if a['evaluation']['verdict']=='RUNTIME_ERROR':z['reference_classification_review'].append(dict(index=a['index'],original_label=a['classification'],corrected_label='REFERENCE_RUNTIME_UNVERIFIED',reason='Runtime error alone does not establish unsupported language. Input contract evidence reviewed separately.'))
  final.append(z)
 atomic_json(ROOT/'candidate_contract_review.json',review);atomic_json(ROOT/'candidate_admission_final.json',final);print([(x['id'],len(x['invalid_input_cases'])) for x in review if x['invalid_input_cases']],flush=True);print('final admitted',sum(x['status']=='ADMITTED' for x in final),flush=True)
if __name__=='__main__':main()
