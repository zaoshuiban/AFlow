"""Phase facts and evaluator-only correctness; mechanism hypotheses stay separate."""
import copy,hashlib,json,re,uuid
from datetime import datetime,timezone
from pathlib import Path

ERRORS=set('INFRASTRUCTURE_ERROR MODEL_RESPONSE_ERROR CODE_SYNTAX_ERROR CODE_IMPORT_ERROR CODE_CONTRACT_ERROR CODE_RUNTIME_ERROR CODE_WRONG_RESULT CODE_TIMEOUT CODE_RESOURCE_LIMIT TOOL_ARGUMENT_ERROR TOOL_INTEGRATION_ERROR FINAL_SUBMISSION_ERROR FINAL_WRONG_ANSWER UNKNOWN'.split())

def hash_value(v):return hashlib.sha256(json.dumps(v,sort_keys=True,default=str).encode()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def ensure_writable_run(root):
 """Never place new runtime records inside an archived experiment tree."""
 path=Path(root).resolve()
 for parent in (path,*path.parents):
  manifest=parent/'manifest.json'
  if manifest.is_file():
   try:data=json.loads(manifest.read_text())
   except (ValueError,OSError):raise ValueError('Unreadable run manifest; refuse mutation')
   if ('model_alias' in data or 'experiment' in data) and data.get('runtime_version')!='operator-runtime-v1':raise ValueError('Historical/frozen run: use a new stabilized run directory')
def redact(v):
 if isinstance(v,dict):return {k:('[REDACTED]' if re.search(r'api.?key|authorization|password|secret|access.?token',k,re.I) else redact(x)) for k,x in v.items()}
 if isinstance(v,list):return [redact(x) for x in v]
 if isinstance(v,str):
  v=re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[REDACTED]',v)
  return re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._-]+',r'\1[REDACTED]',v)
 return v

class EventLog:
 def __init__(self,root,run_id=None):ensure_writable_run(root);self.root=Path(root);self.run_id=run_id or self.root.name;(self.root/'events').mkdir(parents=True,exist_ok=True)
 def emit(self,stage,event_type,status,task_id=None,input=None,output=None,tool_id=None,error_type=None,duration_ms=0,usage=None,**facts):
  if error_type is not None and error_type not in ERRORS:raise ValueError('Unknown error classification')
  event=dict(task_id=task_id,run_id=self.run_id,stage=stage,event_type=event_type,status=status,input_hash=hash_value(input),output_hash=hash_value(output),tool_id=tool_id,error_type=error_type,timestamp=now(),duration_ms=duration_ms,usage=usage or {},facts=redact(facts))
  p=self.root/'events'/(uuid.uuid4().hex+'.json');p.write_text(json.dumps(event,indent=2));return event

class Provenance:
 def __init__(self,task_id,run_id):self.task_id=task_id;self.run_id=run_id;self.events=[];self.answer=None;self.submitted=False
 def tool(self,name,result):self.events.append(dict(stage='tool',tool=name,result=copy.deepcopy(result),timestamp=now(),error_type=result.get('error_type')))
 def failure(self,stage,error_type):
  if error_type not in ERRORS:raise ValueError(error_type)
  self.events.append(dict(stage=stage,error_type=error_type,timestamp=now()))
 def submit(self,value):
  if value is not None and (type(value) is not int or value<0):self.failure('submission','FINAL_SUBMISSION_ERROR');return
  self.answer=value;self.submitted=True;self.events.append(dict(stage='submission',answer=value,timestamp=now()))
 def record(self):return dict(task_id=self.task_id,run_id=self.run_id,answer=self.answer,submitted=self.submitted,events=copy.deepcopy(self.events))

def evaluate(record,expected):
 """Only this offline function receives reference answers. Never feeds Agent tools."""
 r=copy.deepcopy(record);r['correct']=r['submitted'] and type(r['answer']) is int and r['answer']==expected
 ops=[e['result'] for e in r['events'] if e.get('tool')=='operator'];py=[e['result'] for e in r['events'] if e.get('tool')=='python']
 good=lambda x:x.get('execution_status')=='SUCCESS' and type(x.get('result')) is int
 r['operator_result_overridden']=any(good(t) and t['result']!=r['answer'] for t in ops) and r['submitted']
 r['direct_operator_adoption']=any(good(t) and t['result']==r['answer'] for t in ops) and not any(good(t) for t in py) and r['submitted']
 r['python_recomputed']=any(good(t) for t in py)
 r['recovered_wrong_operator']=r['correct'] and any(good(t) and t['result']!=expected for t in ops) and any(good(t) and t['result']==expected for t in py)
 for e in r['events']:
  if e.get('tool') and good(e['result']):
   e['functional_correctness']=e['result']['result']==expected
   if not e['functional_correctness']:e['error_type']='CODE_WRONG_RESULT'
 r['events'].append(dict(stage='evaluation',correct=r['correct'],error_type=None if r['correct'] else 'FINAL_WRONG_ANSWER' if r['submitted'] else 'FINAL_SUBMISSION_ERROR',timestamp=now()))
 return r
