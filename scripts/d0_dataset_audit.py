"""Read frozen public data only. No generated-program execution or model calls."""
import ast,base64,collections,hashlib,io,json,pickle,random,re,zlib
from pathlib import Path
import pyarrow.parquet as pq
from scripts.offline_loop_protocol import atomic_json,file_hash
from scripts.compositional_counting import make_instance,exact_dp,brute_force,KINDS
ROOT=Path('results/experiment_d0_20261019_s0_v1');D=ROOT/'data'
class DataOnlyUnpickler(pickle.Unpickler):
 def find_class(self,module,name):raise ValueError('Pickle class forbidden')
 def persistent_load(self,pid):raise ValueError('Pickle persistent ID forbidden')
def private_cases(text):
 try:return json.loads(text)
 except json.JSONDecodeError:
  dec=zlib.decompressobj();buf=dec.decompress(base64.b64decode(text),32*1024**2)
  if dec.unconsumed_tail or not dec.eof:raise ValueError('Decoded case payload exceeds cap or truncated')
  obj=DataOnlyUnpickler(io.BytesIO(buf)).load()
  if not isinstance(obj,(str,bytes)):raise ValueError('Expected JSON string')
  return json.loads(obj)
def main():
 taco=pq.read_table(D/'taco_ALL_test-00000-of-00001.parquet').to_pylist();spj=[json.loads(s) for s in open(D/'taco_framework_output_spj.jsonl')];spj={x['idx']:x for x in spj}
 assert len(taco)==len(spj)==1000 and set(spj)==set(range(1000))
 rows=[];parsed={};references={};taco_ids={};exclusions=collections.Counter()
 for i,t in enumerate(taco):
  reasons=[];io_data={};solutions=[]
  try:
   io_data=json.loads(t['input_output']);solutions=json.loads(t['solutions']);assert len(io_data['inputs'])==len(io_data['outputs']) and len(io_data['inputs'])>0
  except (ValueError,TypeError,KeyError,AssertionError):reasons.append('MISSING_OR_INVALID_TESTS_REFERENCE')
  if spj[i]['special_judge']:reasons.append('OFFICIAL_SPJ_EXCLUDED')
  if str(t.get('picture_num') or '0') not in ['0','0.0']:reasons.append('IMAGE_DEPENDENT_STATEMENT')
  if not solutions:reasons.append('NO_REFERENCE')
  mode='class' if io_data.get('fn_name') and 'class Solution' in (t.get('starter_code') or '') else 'function' if io_data.get('fn_name') else 'stdin'
  tid='taco-test-'+str(i);url=(t.get('url') or '').strip().rstrip('/');taco_ids[url]=tid
  difficulty={'EASY':'Low','MEDIUM':'Medium','MEDIUM_HARD':'Medium','HARD':'High','VERY_HARD':'High'}.get(t['difficulty'],'Unknown')
  row=dict(id=tid,source='TACO',difficulty=difficulty,original_difficulty=t['difficulty'],original_url=url,title=t.get('name'),interface=mode,tests=len(io_data.get('inputs',[])),admitted_metadata=not reasons,exclusion_reasons=reasons,official_spj_case=spj[i]['case'])
  rows.append(row);exclusions.update(reasons)
  if not reasons:parsed[tid]={'question':t['question'],'starter_code':t.get('starter_code'),'io':io_data,'difficulty':difficulty,'interface':mode,'fn_name':io_data.get('fn_name'),'url':url};references[tid]=solutions
 lcb=[json.loads(s) for s in open(D/'lcb_test6.jsonl')];lcb_private={};dedup=[];lcb_public={}
 errata=(D/'lcb_framework_ERRATA.md').read_text();errata_ids=set(re.findall(r'\d+\. ([\w-]+) -',errata))
 for t in lcb:
  reasons=[];metadata=json.loads(t['metadata'] or '{}');pub=json.loads(t['public_test_cases']);tests=[]
  try:
   tests=private_cases(t['private_test_cases']);assert isinstance(tests,list) and tests
  except Exception as e:reasons.append('PRIVATE_TEST_DECODE_OR_SIZE_UNSUPPORTED')
  tid='lcb-v6-'+t['question_id'];mode='class' if metadata.get('func_name') and 'class Solution' in t['starter_code'] else 'function' if metadata.get('func_name') else 'stdin'
  if t['question_id'] in errata_ids:reasons.append('OFFICIAL_ERRATA')
  if any(c.get('testtype') not in ['stdin','functional'] for c in pub+tests):reasons.append('UNSUPPORTED_TEST_INTERFACE')
  url='https://atcoder.jp/contests/'+t['contest_id']+'/tasks/'+t['question_id'] if t['platform']=='atcoder' else ''
  if url in taco_ids and url:reasons.append('EXACT_ORIGINAL_URL_DUPLICATE');dedup.append(dict(TACO=taco_ids[url],LiveCodeBench=tid,url=url))
  row=dict(id=tid,source='LiveCodeBench-v6-increment',difficulty={'easy':'Low','medium':'Medium','hard':'High'}.get(t['difficulty'],'Unknown'),original_difficulty=t['difficulty'],original_url=url,title=t['question_title'],interface=mode,tests=len(tests),public_tests=len(pub),admitted_metadata=not reasons,exclusion_reasons=reasons,question_id=t['question_id'],contest_date=t['contest_date']);rows.append(row);exclusions.update(reasons)
  lcb_private[tid]=tests;lcb_public[tid]=dict(id=tid,source='LiveCodeBench',prompt=t['question_content'],starter_code=t['starter_code'],metadata=metadata,public_examples=pub,interface=mode,difficulty=row['difficulty'])
 # Smoke IDs selected solely from metadata, not model outcomes. No independent confirmation role.
 rng=random.Random(261019001);smoke=[]
 for source in ['TACO','LiveCodeBench-v6-increment']:
  for level in ['Low','Medium','High']:
   available=sorted([x for x in rows if x['source']==source and x['difficulty']==level and x['admitted_metadata']],key=lambda x:x['id']);rng.shuffle(available)
   smoke.extend(dict(**x,role='ENGINEERING_DEVELOPMENT_ONLY',selection_seed=261019001) for x in available[:2])
 A=[]
 for level,kinds in [('Low',('adjacent','count')),('Medium',('adjacent','count','mod','pattern_count')),('High',KINDS)]:
  for j in range(2):
   p=make_instance(rng,kinds,5+j);a=exact_dp(p)['answer'];b=brute_force(p);assert a==b
   h=hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest();tid='d0-count-v2-'+h[:16]
   A.append(dict(id=tid,source='Dataset A v2 task pool',difficulty=level,problem=p,expected=a,role='ENGINEERING_DEVELOPMENT_ONLY',generator='existing public semantics, new seed 261019001, complexity by constraint interactions; not new semantics'))
   smoke.append(dict(id=tid,source='Dataset A v2 task pool',difficulty=level,role='ENGINEERING_DEVELOPMENT_ONLY'))
 atomic_json(ROOT/'dataset_inventory.json',rows);atomic_json(ROOT/'smoke_task_ids.json',smoke);atomic_json(ROOT/'dataset_A_smoke.json',A)
 atomic_json(ROOT/'taco_parsed_admitted.json',parsed);atomic_json(ROOT/'taco_references.json',references);atomic_json(ROOT/'lcb_private_cases.json',lcb_private);atomic_json(ROOT/'lcb_public_tasks.json',lcb_public);atomic_json(ROOT/'exact_cross_source_duplicates.json',dedup)
 counts={}
 for source in ['TACO','LiveCodeBench-v6-increment']:
  counts[source]={level:dict(raw=sum(x['source']==source and x['difficulty']==level for x in rows),metadata_admitted=sum(x['source']==source and x['difficulty']==level and x['admitted_metadata'] for x in rows)) for level in ['Low','Medium','High']}
 atomic_json(ROOT/'dataset_audit.json',dict(counts=counts,exclusions=dict(exclusions),cross_source_exact_duplicates=len(dedup),cross_source_near_duplicate_review='NOT_COMPLETED; URL coverage incomplete especially Codeforces/LeetCode',taco_spj_alignment='1000 indices match 1000 frozen test rows; official SPJ file has no dataset content hash binding, so positional alignment has residual version uncertainty',lcb_scope='Only v6 increment/test6.jsonl (175 tasks), NOT complete release_v6/test1..6 (1055 tasks). Original exact release mapping preserved.',formal_540_readiness=False,reason='LCB increment Low/Medium raw counts below60; full release not downloaded/admitted. Metadata admission is not executable reference validation. No near-duplicate family split completed.',API_calls=0,script_sha256=file_hash(Path(__file__))))
 print(json.dumps(counts));print('smoke IDs',len(smoke),'all engineering development only')
if __name__=='__main__':main()
