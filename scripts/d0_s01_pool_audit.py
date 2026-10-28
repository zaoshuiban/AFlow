"""Full release_v6 data identity and deterministic cross-source duplicate grouping."""
import ast,collections,hashlib,itertools,json,re,time,urllib.parse
from pathlib import Path
import pyarrow.parquet as pq
from scripts.experiment_d0_s01 import ROOT,OLD,verify
from scripts.d0_dataset_audit import private_cases
from scripts.offline_loop_protocol import atomic_json,file_hash

def canonical_url(url):
 url=(url or '').lower().rstrip('/')
 m=re.search(r'codeforces\.com/(?:problemset/problem/|contest/|gym/)(\d+)/(?:problem/)?([a-z]\d?)',url)
 if m:return 'codeforces:'+m[1]+'_'+m[2]
 m=re.search(r'atcoder\.jp/contests/[^/]+/tasks/([\w]+)',url)
 if m:return 'atcoder:'+m[1]
 m=re.search(r'leetcode\.(?:com|cn)/problems/([^/?#]+)',url)
 if m:return 'leetcode:'+m[1]
 return url.split('?')[0] if url else ''
def text_tokens(text):return re.findall(r'[a-z0-9]+',text.lower())
def grams(text):
 tok=text_tokens(text);return {tuple(tok[i:i+5]) for i in range(len(tok)-4)}
def run():
 verify()
 if (ROOT/'full_pool_audit.json').exists():raise FileExistsError('No rerun')
 atomic_json(ROOT/'pool_audit_protocol.json',dict(files=['test.jsonl','test2.jsonl','test3.jsonl','test4.jsonl','test5.jsonl','test6.jsonl'],revision=json.loads((OLD/'data/lcb_metadata.json').read_text())['sha'],split='release_v6/test',dedup='Union exact original canonical URL, normalized full question, identical nonempty title with Jaccard>=.5; 5-word-shingle Jaccard>=.5 treated as possible duplicate. Inverted-index candidate generation using max30 rare noncommon shingles per task.',thresholds=dict(ambiguous_min_jaccard=.5,strong_min_jaccard=.75),semantic_manual_review='Not a claim to detect every paraphrase/translation or algorithm-family relationship; unresolved groups kept together before any future split.',script_sha256=file_hash(Path(__file__)),API=0))
 ledger=[json.loads(s) for s in open(ROOT/'data/download_ledger.jsonl')];assert len(ledger)==6 and all(x.get('matched') for x in ledger)
 for x in ledger:assert file_hash(Path(x['path']))==x['expected_sha256']
 quarantined={x['id'] for x in json.loads((ROOT/'candidate_contract_review.json').read_text()) if x['invalid_input_cases']}|{'taco-test-11'}
 rows=[];public={};errata_ids=set(re.findall(r'\d+\. ([\w-]+) -',(OLD/'data/lcb_framework_ERRATA.md').read_text()))
 oldinv={x['id']:x for x in json.loads((OLD/'dataset_inventory.json').read_text())}
 taco=pq.read_table(OLD/'data/taco_ALL_test-00000-of-00001.parquet',columns=['question','url','name','difficulty','tags','skill_types']).to_pylist()
 for i,t in enumerate(taco):
  tid='taco-test-'+str(i);row=oldinv[tid].copy();row['canonical_origin']=canonical_url(t['url']);row['algorithm_metadata']=dict(tags=t['tags'],skills=t['skill_types']);row['admitted_metadata']=row['admitted_metadata'] and tid not in quarantined
  if tid in quarantined:row['exclusion_reasons']=row['exclusion_reasons']+['DATA_CONTRACT_UNRESOLVED']
  rows.append(row);public[tid]={'title':t.get('name') or '', 'text':t['question']}
 lcb_ids=set();parses=[]
 for filename in ['test.jsonl','test2.jsonl','test3.jsonl','test4.jsonl','test5.jsonl','test6.jsonl']:
  count=0
  for line in open(ROOT/'data'/('lcb_'+filename)):
   t=json.loads(line);qid=t['question_id'];tid='lcb-release-v6-'+t['platform']+'-'+qid;assert tid not in lcb_ids; lcb_ids.add(tid);count+=1;reasons=[]
   metadata=json.loads(t['metadata'] or '{}');mode='class' if metadata.get('func_name') and 'class Solution' in t['starter_code'] else 'function' if metadata.get('func_name') else 'stdin'
   if t['platform']=='atcoder':origin='atcoder:'+qid.lower()
   elif t['platform']=='codeforces':origin='codeforces:'+qid.lower()
   elif t['platform']=='leetcode':origin='leetcode:'+t['question_title'].lower()
   else:origin=''
   if qid in errata_ids:reasons.append('OFFICIAL_ERRATA')
   try:
    cases=private_cases(t['private_test_cases']);assert isinstance(cases,list) and cases;assert all(set(['input','output','testtype'])<=set(x) for x in cases)
    if any(x['testtype'] not in ['stdin','functional'] for x in cases):reasons.append('UNSUPPORTED_TEST_INTERFACE')
    tests=len(cases);del cases
   except Exception as e:reasons.append('PRIVATE_TEST_DECODE_UNSUPPORTED');tests=None
   row=dict(id=tid,source='LCB-release-v6',source_partition=filename,question_id=qid,platform=t['platform'],title=t['question_title'],canonical_origin=origin,difficulty={'easy':'Low','medium':'Medium','hard':'High'}[t['difficulty']],original_difficulty=t['difficulty'],date=t['contest_date'],interface=mode,tests=tests,private_payload_sha256=hashlib.sha256(t['private_test_cases'].encode()).hexdigest(),admitted_metadata=not reasons,exclusion_reasons=reasons)
   rows.append(row);public[tid]={'title':t['question_title'],'text':t['question_content']}
  parses.append(dict(partition=filename,rows=count));print(filename,count,flush=True)
 assert len(lcb_ids)==1055
 # Candidate generation fixed before similarity outcomes; task grouping never uses correctness.
 parent={x['id']:x['id'] for x in rows};edges=[]
 def find(x):
  while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
  return x
 def merge(a,b,why,score=None):
  aa,bb=find(a),find(b)
  if aa!=bb:parent[max(aa,bb)]=min(aa,bb)
  edges.append(dict(a=a,b=b,reason=why,score=score))
 for key in ['canonical_origin']:
  groups=collections.defaultdict(list)
  for row in rows:
   if row[key]:groups[row[key]].append(row['id'])
  for ids in groups.values():
   for a,b in zip(ids,ids[1:]):merge(a,b,'exact_original_origin')
 exact=collections.defaultdict(list);titles=collections.defaultdict(list);gs={}
 for tid,p in public.items():
  tokens=text_tokens(p['text']);exact[hashlib.sha256(' '.join(tokens).encode()).hexdigest()].append(tid);gs[tid]=grams(p['text'])
  title=re.sub(r'^[a-z]\.\s*','',p['title'].lower()).replace('-',' ').strip()
  if title:titles[title].append(tid)
 for ids in exact.values():
  for a,b in zip(ids,ids[1:]):merge(a,b,'exact_normalized_statement')
 candidates=set()
 for ids in titles.values():candidates.update(itertools.combinations(sorted(ids),2))
 inv=collections.defaultdict(list)
 for tid,g in gs.items():
  for s in g:inv[s].append(tid)
 for tid,g in gs.items():
  chosen=sorted((s for s in g if 1<len(inv[s])<=50),key=lambda s:(len(inv[s]),s))[:30]
  for s in chosen:
   for other in inv[s]:
    if other!=tid:candidates.add(tuple(sorted([tid,other])))
 for a,b in sorted(candidates):
  inter=len(gs[a]&gs[b]);den=len(gs[a]|gs[b]);score=inter/den if den else 0
  if score>=.5:merge(a,b,'strong_near_duplicate' if score>=.75 else 'possible_duplicate_conservative_group',score)
 groups=collections.defaultdict(list)
 for row in rows:row['duplicate_group']=find(row['id']);groups[row['duplicate_group']].append(row['id'])
 byid={r['id']:r for r in rows};cross=[v for v in groups.values() if len({byid[x]['source'] for x in v})>1]
 counts={}
 for source in ['TACO','LCB-release-v6']:
  counts[source]={level:dict(raw=sum(x['source']==source and x['difficulty']==level for x in rows),metadata_admitted=sum(x['source']==source and x['difficulty']==level and x['admitted_metadata'] for x in rows),unique_groups=len({x['duplicate_group'] for x in rows if x['source']==source and x['difficulty']==level and x['admitted_metadata']})) for level in ['Low','Medium','High']}
 atomic_json(ROOT/'full_pool_inventory.json',rows);atomic_json(ROOT/'duplicate_edges.json',edges);atomic_json(ROOT/'duplicate_groups.json',dict(groups));atomic_json(ROOT/'full_pool_audit.json',dict(revision=json.loads((OLD/'data/lcb_metadata.json').read_text())['sha'],release='release_v6',partitions=parses,LCB_rows=1055,TACO_rows=1000,counts=counts,cross_source_duplicate_groups=cross,cross_source_group_count=len(cross),all6_content_hashes_verified=True,near_duplicate_candidates=len(candidates),dedup_edges=len(edges),formal_split_created=False,formal_540_ready=False,limitations='Deterministic dedup preparation completed; manual semantic/translated-family review, all-task reference validation, source SPJ hash alignment and formal group split still required. Counts are metadata eligibility, not trusted scoring acceptance.',API=0))
 print(json.dumps(counts),flush=True)
if __name__=='__main__':run()
