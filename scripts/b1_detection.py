"""Conservative local-error detector refinement; no mechanism inference by name."""
from scripts.experiment_b1 import detect_local

def detect(task,observation,**kwargs):
 rows=detect_local(task,observation,**kwargs)
 for r in rows:
  e=observation['events'][r['step_id']];error=r['output']['error'] or '';r['failure_family']='Other / Unknown'
  if e.get('declared_scope') in ('subproblem','exploration','approximation'):
   r.update(local_failure=False,recovery_status=None,local_status='EXPECTED_EXPLORATION',validation_status='NOT_CHECKED');r['evidence'].append('Explicit objective is not full exact task')
  elif error.startswith('AssertionError'):
   r.update(local_failure=False,recovery_status=None,local_status='SUSPECTED_LOCAL_FAILURE',validation_status='UNKNOWN');r['evidence'].append('An assertion may be an intentional hypothesis probe; independent defect evidence required')
  elif r['error_type'] in ('CODE_CONTRACT_ERROR','CODE_IMPORT_ERROR'):
   r['failure_family']='Tool Integration'
  elif r['error_type']=='CODE_RUNTIME_ERROR':r['failure_family']='Runtime Error'
 return rows
