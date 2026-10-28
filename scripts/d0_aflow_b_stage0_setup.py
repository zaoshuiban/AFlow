"""Freeze official artifacts and provenance. Never runs downloaded programs."""
import hashlib,json,tarfile,subprocess
from pathlib import Path
P=Path('results/experiment_d0_aflow_b_stage0_20261019_v1')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def main():
 archive=P/'official_results.tar.gz'
 wanted=['results/MBPP/graphs_test/round_14/graph.py','results/MBPP/graphs_test/round_14/prompt.py','results/MBPP/graphs_test/round_14/experience.json','results/MBPP/graphs/results.json','results/MBPP/graphs/round_14/graph.py','results/MBPP/graphs/template/operator.py','results/MBPP/graphs/template/operator_an.py','results/MBPP/graphs/template/op_prompt.py','results/HumanEval/graphs/template/operator_an.py','results/HumanEval/graphs/template/op_prompt.py']
 with tarfile.open(archive) as t:
  for name in wanted:
   m=t.getmember(name)
   if not m.isfile() or m.size>2*1024**2:raise ValueError(name)
   dst=P/'frozen_official'/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(t.extractfile(m).read())
 manifest={'run_id':P.name,'stage':'STAGE_0_OFFLINE_ONLY','model_API_budget_CNY':0,'workflow_status':'ADAPTED_AFLOW_UNDER_TEST','local_git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'official_main_commit':'3f457218fc716093fe53f6df8a5d5e6379d66346','official_readme':'https://github.com/FoundationAgents/AFlow/blob/3f457218fc716093fe53f6df8a5d5e6379d66346/README.md','official_archive_url':'https://drive.google.com/uc?export=download&id=1Sr5wjgKf3bN8OC7G6cO3ynzJqD4w6_Dv','archive_sha256':sha(archive),'archive_bytes':archive.stat().st_size,'selection':'Official archive explicitly separates graphs_test/round_14 for MBPP; choose this designated evaluated workflow before any D0 model outcomes. Not the absent local MBPP round_8; not D0 reoptimization.','original_target':'MBPP','round':14,'graph':'frozen_official/results/MBPP/graphs_test/round_14/graph.py','source_hashes':{str(p.relative_to(P)):sha(p) for p in (P/'frozen_official').rglob('*') if p.is_file()},'provenance_limit':'Official release URL provenance and local SHA established; no publisher signed digest or exact original optimization code commit in archive, not asserted.'}
 save(P/'provenance.json',manifest)
 # Preserve every pre-existing experimental result and tracked source file; no secret configuration is read.
 roots=[Path('results'),Path('workspace'),Path('engineering_stabilization')];files={}
 for root in roots:
  for p in root.rglob('*'):
   if p.is_file() and P not in p.parents and '__pycache__' not in p.parts and p.suffix!='.pyc':files[str(p)]=sha(p)
 save(P/'history_before.json',files)
 public=json.loads(Path('results/experiment_d0_s01_20261019_v1/s1_public_tasks.json').read_text());cases={}
 for tid,task in public.items():
  cs=[]
  if task['source']=='A' or tid.startswith('d0-count-'):
   for n,cons,ans in [(1,[],4),(1,[{'kind':'endpoints'}],0),(2,[{'kind':'adjacent'}],12)]:cs.append({'args':[json.dumps({'n':n,'alphabet':[0,1,2,3],'constraints':cons})],'expected':ans})
   source='公开规格可手算的小例子，新增公开展示，不读取任务隐藏答案；不是历史M1–M3定向测试'
  elif tid=='taco-test-67':
   cs=[{'stdin':'4\n1 3\n4 3\n4 2\n1 2\n','expected':'0 0 0 0'},{'stdin':'6\n1 2\n3 4\n6 4\n2 3\n1 3\n3 5\n','expected':'0 0 0 1 1 2'}];source='原始题面Examples，逐字输入输出'
  else:
   for e in task.get('public_examples',[]):
    cs.append({'stdin':e['input'],'expected':e['output']} if task['interface']['mode']=='stdin' else {'args':[json.loads(line) for line in e['input'].splitlines()],'expected':json.loads(e['output'])})
   source='LCB原始public_examples，按已声明函数参数顺序逐行JSON解析'
  cases[tid]={'cases':cs,'source':source,'judge':{'mode':'tokens' if task['interface']['mode']=='stdin' else 'exact'},'interface':task['interface']}
 save(P/'public_test_manifest.json',cases)
 print('Frozen official sources',len(wanted),'protected historical files',len(files))
if __name__=='__main__':main()
