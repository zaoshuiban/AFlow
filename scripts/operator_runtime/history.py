"""Read-only adapters and task-use ledger; never reruns historical models/tools."""
import csv,json
from pathlib import Path
from collections import Counter
from scripts.offline_loop_protocol import atomic_json,read_json,digest

class TaskRoles:
 ROLES={'DISCOVERY','DEVELOPMENT','VALIDATION','HIDDEN_TEST','POSTHOC_AUDIT','HISTORICAL_TEST'}
 def __init__(self,path):self.path=Path(path);self.rows=read_json(self.path) if self.path.exists() else []
 def add(self,problem,role,run_id,source):
  if role not in self.ROLES:raise ValueError('Explicit task use role required')
  h=digest(json.loads(problem) if isinstance(problem,str) else problem)
  if role=='HIDDEN_TEST' and any(r['task_hash']==h for r in self.rows):raise ValueError('Previously used task cannot be newly unseen hidden evidence')
  self.rows.append(dict(task_hash=h,role=role,run_id=run_id,source=source));atomic_json(self.path,self.rows)


def read_observations(root,experiment):
 root=Path(root);rows=[]
 if experiment=='A':
  for p in root.glob('observations/*.json'):
   r=read_json(p)
   if r.get('stage')=='formal':rows.append({**r,'method':r['arm'],'record_path':str(p)})
 elif experiment=='B0':
  for method in ('reflection','direct','mechanism'):
   for p in (root/f'evaluation_{method}/observations').glob('*.json'):
    r=read_json(p);rows.append({**r,'method':method,'record_path':str(p)})
 else:raise ValueError('Unknown historical schema')
 return rows


def replay(output):
 output=Path(output);output.mkdir(parents=True,exist_ok=True)
 roots={'A':Path('results/generated_operator_experiment_a_v1'),'B0':Path('results/experiment_b0_20261014_v1')}
 details={};diff=[];records=[]
 for exp,root in roots.items():
  rs=read_observations(root,exp);assert len({(r['id'],r['method']) for r in rs})==len(rs)
  old=read_json(root/'analysis.json');cells=[]
  if exp=='A':
   for split in ('ID','Easy','Medium','Hard','OOD','All'):
    for method in ('baseline','reflection','operator'):
     cell=[r for r in rs if r['method']==method and (split=='All' or split=='ID' and r['tier']!='OOD' or r['tier']==split)]
     v=dict(experiment=exp,split=split,method=method,n=len(cell),correct=sum(r['correct'] for r in cell));cells.append(v)
     original=old['results'][split][method]
     if (v['n'],v['correct'])!=(original['n'],original['correct']):diff.append(dict(recomputed=v,original=original))
  else:
   for split in ('transfer','control','natural','all'):
    for method in ('reflection','direct','mechanism'):
     cell=[r for r in rs if r['method']==method and (split=='all' or r['tier']==split)]
     v=dict(experiment=exp,split=split,method=method,n=len(cell),correct=sum(r['correct'] for r in cell));cells.append(v)
     original=old['results'][split][method]
     if (v['n'],v['correct'])!=(original['n'],original['correct']):diff.append(dict(recomputed=v,original=original))
  if exp=='A':
   labels=read_json(root/'failure_classification.json');unknown=sum(x['label']=='unknown' for x in labels)
  else:unknown=sum(x.get('mechanism','').startswith('Unknown') for x in read_json(root/'failure_analysis.json'))
  details[exp]=dict(observations=len(rs),original_denominator_retained=True,cells=cells,unknown_labels_retained=unknown,compatibility='Original correctness facts only; no mechanism inference/no code execution')
  records.extend(cells)
 with (output/'historical_statistics.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=['experiment','split','method','n','correct']);w.writeheader();w.writerows(records)
 atomic_json(output/'historical_compatibility.json',details)
 text='# Historical discrepancy report\n\n'+('No differences in original denominators/correct counts. All old files remain untouched. Unknown labels retained.' if not diff else json.dumps(diff,indent=2))+'\n'
 (output/'historical_discrepancy_report.md').write_text(text)
 atomic_json(output/'historical_discrepancies.json',diff)
 return details,diff
