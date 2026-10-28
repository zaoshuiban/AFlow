"""Evidence-driven admission and immutable, versioned trusted registration."""
import json,re,time,uuid
from dataclasses import dataclass,field,asdict
from pathlib import Path
from datetime import datetime,timezone
from scripts.offline_loop_protocol import atomic_json,read_json,file_hash,digest
from .executor import execute,static_check,Resources
from .events import EventLog,ensure_writable_run

CATEGORIES={'development','boundary','invalid','property','metamorphic','crosscheck','regression','resource'}
@dataclass
class ValidationCase:
 category:str
 problem:object
 expected:object=None
 expect_error:bool=False
 metadata:dict=field(default_factory=dict)
@dataclass
class ValidationProtocol:
 version:str
 dataset_version:str
 cases:list
 wall_seconds:float=2
 resources:Resources=field(default_factory=Resources)
 reference_version:str='independent-configured-expectations-v1'
 def __post_init__(self):
  if not self.version or not self.dataset_version or not CATEGORIES<={c.category for c in self.cases}:raise ValueError('All mandatory validation categories need configured cases')
  if any(c.category not in CATEGORIES or not c.expect_error and (type(c.expected) is not int or c.expected<0) for c in self.cases):raise ValueError('Invalid case contract')
 def serialize(self):return dict(version=self.version,dataset_version=self.dataset_version,reference_version=self.reference_version,wall_seconds=self.wall_seconds,resources=asdict(self.resources),cases=[asdict(c) for c in self.cases])

class Registry:
 def __init__(self,root):ensure_writable_run(root);self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.log=EventLog(self.root)
 def path(self,op,version):
  if not all(re.fullmatch(r'[A-Za-z0-9_-]+',x or '') for x in (op,version)):raise ValueError('Safe explicit operator/version required')
  return self.root/op/version
 def transition(self,d,state,**facts):
  current=[read_json(p)['state'] for p in sorted((d/'lifecycle').glob('*.json'))]
  allowed={None:{'GENERATED'},'GENERATED':{'STATIC_CHECKED','REJECTED'},'STATIC_CHECKED':{'FUNCTIONALLY_VALIDATED','REJECTED'},'FUNCTIONALLY_VALIDATED':{'ACCEPTED'},'ACCEPTED':{'FROZEN'},'FROZEN':{'REGISTERED','QUARANTINED'},'REGISTERED':{'QUARANTINED'},'REJECTED':set(),'QUARANTINED':set()}
  if state not in allowed[current[-1] if current else None]:raise ValueError('Illegal lifecycle transition')
  # Unique append-only event files retain every historical state, including quarantine.
  self.log.emit('lifecycle',state,'FAILED' if state=='REJECTED' else 'SUCCESS',tool_id=d.parent.name+'/'+d.name,**facts)
  events=d/'lifecycle';events.mkdir(exist_ok=True);atomic_json(events/(time.time_ns().__str__()+'_'+uuid.uuid4().hex+'.json'),dict(state=state,timestamp=datetime.now(timezone.utc).isoformat(),**facts))
 async def admit(self,operator_id,version,code,protocol,*,generation=None):
  d=self.path(operator_id,version);d.mkdir(parents=True,exist_ok=False)
  (d/'source.py').write_text(code);atomic_json(d/'protocol.json',protocol.serialize());atomic_json(d/'generation.json',generation or {'method':'unspecified'})
  self.transition(d,'GENERATED');checks=[]
  try:static_check(code)
  except (ValueError,SyntaxError) as exc:
   self.transition(d,'REJECTED',reason=str(exc));atomic_json(d/'validation.json',dict(passed=False,static_error=str(exc),checks=[]));return dict(state='REJECTED',operator_id=operator_id,version=version)
  self.transition(d,'STATIC_CHECKED')
  for case in protocol.cases:
   raw=await execute(code,json.dumps(case.problem),protocol.wall_seconds,resources=protocol.resources,operator_id=operator_id,version=version)
   if case.expect_error:passed=raw['status'] in ('error','declined') and raw.get('error_type') not in ('TOOL_INTEGRATION_ERROR','CODE_TIMEOUT','CODE_RESOURCE_LIMIT')
   else:passed=raw['status']=='success' and type(raw['value']) is int and raw['value']==case.expected
   checks.append(dict(case=asdict(case),execution=raw,passed=passed))
   self.log.emit('validation','CODE_VALIDATION','SUCCESS' if passed else 'FAILED',input=case.problem,output=raw,tool_id=operator_id,error_type=None if passed else raw.get('error_type') or 'CODE_WRONG_RESULT')
  validation=dict(passed=all(c['passed'] for c in checks),checks=checks,coverage=sorted({c.category for c in protocol.cases}),scope_note='All configured tests passed only; no universal correctness claim')
  atomic_json(d/'validation.json',validation)
  if not validation['passed']:
   self.transition(d,'REJECTED');return dict(state='REJECTED',operator_id=operator_id,version=version)
  self.transition(d,'FUNCTIONALLY_VALIDATED');self.transition(d,'ACCEPTED')
  frozen=dict(operator_id=operator_id,version=version,source_sha256=file_hash(d/'source.py'),validation_sha256=file_hash(d/'validation.json'),protocol_sha256=file_hash(d/'protocol.json'),generation_sha256=file_hash(d/'generation.json'),validator_version='operator-runtime-v1',validator_sources={p:file_hash(Path(p)) for p in ('scripts/operator_runtime/lifecycle.py','scripts/operator_runtime/executor.py','scripts/operator_runtime/worker.py','scripts/counting_worker.py','scripts/frozen_math_worker.py')},dataset_version=protocol.dataset_version,interface='solve(problem: JSON string)->nonnegative int or explicit refusal',resource_limits=asdict(protocol.resources),frozen_time=datetime.now(timezone.utc).isoformat(),state='FROZEN',scope_note=validation['scope_note'])
  atomic_json(d/'freeze.json',frozen);self.transition(d,'FROZEN');atomic_json(d/'registration.json',dict(operator_id=operator_id,version=version,freeze_sha256=file_hash(d/'freeze.json')));self.transition(d,'REGISTERED')
  return dict(state='REGISTERED',**{k:v for k,v in frozen.items() if k!='state'})
 def load(self,operator_id,version):
  d=self.path(operator_id,version)
  if not (d/'registration.json').exists():raise ValueError('Unaccepted/unregistered operator')
  states=[read_json(p)['state'] for p in sorted((d/'lifecycle').glob('*.json'))]
  if not states or states[-1]!='REGISTERED':raise ValueError('Operator not registered or quarantined')
  reg=read_json(d/'registration.json')
  if file_hash(d/'freeze.json')!=reg['freeze_sha256']:raise ValueError('Frozen metadata mismatch')
  f=read_json(d/'freeze.json')
  for name,key in [('source.py','source_sha256'),('validation.json','validation_sha256'),('protocol.json','protocol_sha256'),('generation.json','generation_sha256')]:
   if file_hash(d/name)!=f[key]:raise ValueError('Frozen hash mismatch '+name)
  for path,sha in f['validator_sources'].items():
   if file_hash(Path(path))!=sha:raise ValueError('Validator implementation changed; new validation/version required')
  if not read_json(d/'validation.json')['passed']:raise ValueError('Validation not passed')
  return dict(code=(d/'source.py').read_text(),freeze=f,state='REGISTERED')
 def quarantine(self,operator_id,version,reason):
  d=self.path(operator_id,version)
  if not (d/'freeze.json').exists():raise ValueError('Cannot quarantine missing frozen operator')
  self.transition(d,'QUARANTINED',reason=reason)
