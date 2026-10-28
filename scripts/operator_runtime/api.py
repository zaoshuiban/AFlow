"""Durable bounded infrastructure retries, compatible with historical Transport.call."""
import asyncio,email.utils,json,time,uuid,os,fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path
from scripts.offline_loop_protocol import atomic_json,read_json,digest
from .events import EventLog,redact,ensure_writable_run

class InfrastructureInterrupted(Exception):pass
class ResponseFormatError(Exception):pass
class BudgetExceeded(InfrastructureInterrupted):pass
@contextmanager
def budget_lock(root):
 with (Path(root)/'budget.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  try:yield
  finally:fcntl.flock(lock,fcntl.LOCK_UN)
@dataclass(frozen=True)
class RetryPolicy:
 max_retries:int=2
 base_delay:float=1
 max_delay:float=60
@dataclass(frozen=True)
class Limits:
 max_requests:int=100
 max_cost_cny:float=5
 max_input_tokens:int=1000000
 max_output_tokens:int=100000
 max_wall_seconds:float=600


def classify(exc):
 name=type(exc).__name__.lower();status=getattr(exc,'status_code',None)
 if 'timeout' in name:return 'TIMEOUT',True,'UNKNOWN_DELIVERY_STATUS'
 if 'connection' in name:return 'CONNECTION_ERROR',True,'UNKNOWN_DELIVERY_STATUS'
 if status==429:return 'RATE_LIMIT',True,'NOT_CONFIRMED_EXECUTED'
 if status is not None and status>=500:return 'SERVER_ERROR',True,'UNKNOWN_DELIVERY_STATUS'
 if status is not None and 400<=status<500:return 'CLIENT_ERROR',False,'REJECTED'
 return 'UNKNOWN_DELIVERY_STATUS',False,'UNKNOWN_DELIVERY_STATUS'

class RetryController:
 def __init__(self,root,sender,policy=None,limits=None,sleep=asyncio.sleep,input_price=2,output_price=8):
  ensure_writable_run(root)
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);(self.root/'api_attempts').mkdir(exist_ok=True);(self.root/'operations').mkdir(exist_ok=True)
  self.sender=sender;self.policy=policy or RetryPolicy();self.limits=limits or Limits();self.sleep=sleep;self.input_price=input_price;self.output_price=output_price;self.lock=asyncio.Lock();self.log=EventLog(self.root)
  if not 0<=self.policy.max_retries<=2:raise ValueError('At most2 infrastructure retries')
  config=dict(policy=vars(self.policy),limits=vars(self.limits),input_price=input_price,output_price=output_price)
  cp=self.root/'retry_config.json'
  if cp.exists() and read_json(cp)!=config:raise ValueError('Cannot change frozen retry configuration')
  if not cp.exists():atomic_json(cp,config)
 def records(self):return [read_json(p) for p in sorted((self.root/'api_attempts').glob('*.json'))]
 def usage(self):
  rs=self.records();return dict(api_request_attempts=len(rs),requests=len(rs),input_tokens=sum((r.get('usage') or {}).get('input_tokens',0) for r in rs),output_tokens=sum((r.get('usage') or {}).get('output_tokens',0) for r in rs),unknown_usage_requests=sum(r.get('usage') is None for r in rs),reserved_or_reported_cost_cny=sum(r['charged_budget_cny'] for r in rs),known_token_estimate_cny=sum(r.get('known_cost_cny',0) for r in rs),retry_cost_estimate_cny=sum(r.get('known_cost_cny',0) for r in rs if r['retry_index']>0),retry_unknown_usage_requests=sum(r.get('usage') is None and r['retry_index']>0 for r in rs),actual_invoice_cost_cny=None,accounting_note='Hard cap on conservative reservation ledger; actual provider invoice and unknown usage not claimed known.')
 async def request(self,key,request,output_cap,timeout=175,phase='generation'):
  if type(output_cap) is not int or output_cap<=0 or timeout<=0:raise ValueError('Positive request budgets required')
  op=self.root/'operations'/(digest(key)+'.json');rh=digest(dict(request=request,output_cap=output_cap))
  # Reserve logical key before first await: concurrent duplicate requests cannot race.
  if op.exists():
   try:previous=read_json(op)
   except (ValueError,OSError):raise InfrastructureInterrupted('Incomplete logical-operation claim; do not replay')
   if previous['request_hash']!=rh:raise ValueError('Changed logical request on resume')
   if previous['status']=='REQUEST_SUCCESS':return read_json(self.root/previous['record'])['raw_response']
   raise InfrastructureInterrupted('Existing unfinished/failed operation; explicit new protocol/run needed')
  try:
   fd=os.open(op,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  except FileExistsError:raise InfrastructureInterrupted('Concurrent logical operation already claimed')
  with os.fdopen(fd,'w') as stream:json.dump(dict(key=key,request_hash=rh,status='IN_PROGRESS'),stream)
  started=time.monotonic();deadline=started+min(timeout,self.limits.max_wall_seconds)
  for index in range(self.policy.max_retries+1):
   in_reserve=len(json.dumps(request,ensure_ascii=False).encode())+4096
   reserve=(in_reserve*self.input_price+output_cap*self.output_price)/1e6
   async with self.lock, _FileBudgetLock(self.root):
    rs=self.records();u=self.usage()
    scope=key.rsplit('/',1)[0]
    scoped=[r for r in rs if r['phase']=='online' and r['logical_key'].rsplit('/',1)[0]==scope]
    scoped_output=sum(r['output_cap'] if r.get('usage') is None else r['usage'].get('output_tokens',0) for r in scoped)
    if phase=='online' and (len(scoped)>=12 or scoped_output+output_cap>8192):
     atomic_json(op,dict(key=key,request_hash=rh,status='INFRASTRUCTURE_INTERRUPTED',reason='task_retry_output_or_request_budget'));raise BudgetExceeded('Task physical retries/output reservation budget')
    reserved_input=sum(r.get('reserved_input_tokens',0) if r.get('usage') is None else r['usage'].get('input_tokens',0) for r in rs)
    reserved_output=sum(r['output_cap'] if r.get('usage') is None else r['usage'].get('output_tokens',0) for r in rs)
    if len(rs)>=self.limits.max_requests or u['reserved_or_reported_cost_cny']+reserve>self.limits.max_cost_cny or reserved_input+in_reserve>self.limits.max_input_tokens or reserved_output+output_cap>self.limits.max_output_tokens or time.monotonic()>=deadline:
     atomic_json(op,dict(key=key,request_hash=rh,status='INFRASTRUCTURE_INTERRUPTED',reason='budget_or_deadline'));raise BudgetExceeded('Request/token/reservation/wall limit')
    rid=uuid.uuid4().hex;p=self.root/'api_attempts'/(rid+'.json')
    r=dict(request_id=rid,logical_key=key,request_hash=rh,retry_index=index,phase=phase,request=redact(request),status='IN_FLIGHT',delivery_status='UNKNOWN_DELIVERY_STATUS',usage=None,reserved_input_tokens=in_reserve,output_cap=output_cap,charged_budget_cny=reserve,known_cost_cny=0)
    atomic_json(p,r)
   t=time.monotonic();retry=False;delay=0
   try:
    response=await asyncio.wait_for(self.sender(request,rid),max(.001,deadline-time.monotonic()))
    r.update(status='REQUEST_SUCCESS',delivery_status='RESPONSE_RECEIVED',raw_response=redact(response),usage=response.get('usage'))
    if r['usage'] is not None:
     r['known_cost_cny']=(r['usage'].get('input_tokens',0)*self.input_price+r['usage'].get('output_tokens',0)*self.output_price)/1e6;r['charged_budget_cny']=r['known_cost_cny']
   except asyncio.CancelledError:
    r.update(status='UNKNOWN_DELIVERY_STATUS',delivery_status='UNKNOWN_DELIVERY_STATUS');r['duration_ms']=(time.monotonic()-t)*1000;atomic_json(p,r);atomic_json(op,dict(key=key,request_hash=rh,status='INFRASTRUCTURE_INTERRUPTED'));raise
   except Exception as exc:
    status,retry,delivery=classify(exc);r.update(status=status,delivery_status=delivery,error=type(exc).__name__)
    headers=getattr(exc,'headers',{}) or getattr(getattr(exc,'response',None),'headers',{}) or {}
    ra=headers.get('Retry-After',headers.get('retry-after','0'))
    try:delay=float(ra)
    except (ValueError,TypeError):
     try:delay=max(0,(email.utils.parsedate_to_datetime(ra)-datetime.now(timezone.utc)).total_seconds())
     except Exception:delay=0
    delay=max(delay,min(self.policy.max_delay,self.policy.base_delay*2**index))
   r['duration_ms']=(time.monotonic()-t)*1000;atomic_json(p,r)
   self.log.emit('api','API_REQUEST',r['status'],input=request,output=r.get('raw_response'),duration_ms=r['duration_ms'],usage=r.get('usage'),error_type=None if r['status']=='REQUEST_SUCCESS' else 'INFRASTRUCTURE_ERROR',request_id=rid,phase=phase,delivery_status=r['delivery_status'])
   if r['status']=='REQUEST_SUCCESS':
    if self.usage()['reserved_or_reported_cost_cny']>self.limits.max_cost_cny:
     atomic_json(op,dict(key=key,request_hash=rh,status='BUDGET_OVERRUN'));raise BudgetExceeded('Provider reported usage exceeded reservation; stop, preserve evidence')
    atomic_json(op,dict(key=key,request_hash=rh,status='REQUEST_SUCCESS',record=str(p.relative_to(self.root))));return r['raw_response']
   if not retry or index==self.policy.max_retries or time.monotonic()+delay>=deadline:break
   await self.sleep(delay)
  atomic_json(op,dict(key=key,request_hash=rh,status='INFRASTRUCTURE_INTERRUPTED'))
  raise InfrastructureInterrupted(r['status'])

class _FileBudgetLock:
 def __init__(self,root):self.context=budget_lock(root)
 async def __aenter__(self):self.context.__enter__()
 async def __aexit__(self,*args):self.context.__exit__(*args)

class Transport:
 """Legacy call signature, explicit stable manifest and physical-attempt ledger."""
 def __init__(self,root,client=None):
  from scripts.experiment_a_pilot import Transport as LegacyTransport
  self.root=Path(root);m=read_json(self.root/'manifest.json')
  if m.get('runtime_version')!='operator-runtime-v1':raise ValueError('Explicit stabilized runtime_version required; historical manifests immutable')
  if client is None:
   legacy=LegacyTransport(root);self.client=legacy.client
  else:self.client=client
  if getattr(self.client,'max_retries',0)!=0:raise ValueError('SDK automatic retries must be disabled; use the tracked controller')
  p=m.get('retry_policy',{});self.controller=RetryController(root,self._send,RetryPolicy(**p),Limits(max_requests=m['max_requests'],max_cost_cny=m['api_budget_cny'],**m.get('extra_limits',{})))
  self.model=m['model_alias']
 async def _send(self,r,rid):
  opts=r['options']
  response=await self.client.chat.completions.create(model=self.model,messages=r['messages'],temperature=0,max_tokens=r['output_cap'],**opts,extra_body={'thinking':{'type':'disabled'}},timeout=r['timeout'])
  ch=response.choices[0];u=response.usage
  # No idempotency guarantee is assumed: vendor support not established. UUID is local correlation only.
  return dict(response=ch.message.content or '',message=ch.message.model_dump(exclude_none=True),reported_model=response.model,finish_reason=ch.finish_reason,usage=None if u is None else dict(input_tokens=u.prompt_tokens,output_tokens=u.completion_tokens,total_tokens=u.total_tokens))
 async def call(self,key,messages,output_cap,timeout=175,tools=None):
  options={'response_format':{'type':'json_object'}} if tools is None else dict(tools=tools,tool_choice={'type':'function','function':{'name':'operator'}} if key.endswith('/operator/0') else 'required',parallel_tool_calls=False)
  request=dict(messages=messages,output_cap=output_cap,timeout=timeout,options=options)
  start=time.monotonic();response=await self.controller.request(key,request,output_cap,timeout,'online' if tools else 'generation')
  return {**response,'elapsed_seconds':time.monotonic()-start}
 def usage(self):return self.controller.usage()
