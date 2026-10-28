"""Compatible native-tool Agent runner with mandatory registry and provenance."""
import asyncio,json,time
from pathlib import Path
from scripts.experiment_a_calibration import prompt,tools_for
from scripts.experiment_a_pilot import parse_json
from scripts.offline_loop_protocol import atomic_json,read_json
from .executor import execute,envelope
from .events import EventLog,Provenance,evaluate
from .api import InfrastructureInterrupted

async def solve(root,transport,row,arm,semantics,operator_code=None,*,registry=None,operator_id=None,version=None,executor=execute,unverified_policy='allow_with_disclosure'):
 if unverified_policy not in ('allow_with_disclosure','require_recompute'):raise ValueError('Explicit unverified-output policy required')
 root=Path(root);dest=root/'observations'/f"{row['id']}_{arm}.json"
 if dest.exists():raise FileExistsError('New execution must use new run identity; historical observations immutable')
 if arm=='operator':
  if registry is None or operator_id is None or version is None:raise ValueError('Registered accepted operator required')
  admitted=registry.load(operator_id,version)
  if operator_code is not None and operator_code!=admitted['code']:raise ValueError('Operator source differs from frozen registration')
  operator_code=admitted['code']
 p=Provenance(row['id'],root.name);events=EventLog(root);started=time.monotonic();wall_used=0;output_used=0;input_used=0;calls=0;tools_used=0;draft=False
 messages=[dict(role='system',content=prompt(semantics,arm)+' Tool execution SUCCESS does not prove correctness. NOT_CHECKED is unverified; follow the common protocol. Explicit failures permit generic Python fallback. No hidden reference is available.'),dict(role='user',content='Count exactly for this JSON input:\n'+row['problem'])]
 error='';raw_events=[]
 while calls<4:
  remaining=180-(time.monotonic()-started)
  if remaining<=0 or output_used>=8192:error='budget_exhausted';break
  final=calls==3 or output_used>=7680 or wall_used>=9.8
  allowed=tools_for(arm,final)
  if arm=='operator' and calls==0:allowed=[t for t in allowed if t['function']['name']=='operator']
  if final:messages.append(dict(role='user',content='FINAL DELIVERY. Submit exact integer or null; no tools remain.'))
  cap=8192-output_used-(0 if final else 512);callkey=f"pilot/{row['id']}/{arm}/{calls}"
  calls+=1
  try:
   response=await asyncio.wait_for(transport.call(callkey,messages,cap,timeout=min(175,remaining),tools=allowed),remaining)
  except (InfrastructureInterrupted,asyncio.TimeoutError):p.failure('api','INFRASTRUCTURE_ERROR');error='infrastructure_interrupted';break
  except Exception:p.failure('api','MODEL_RESPONSE_ERROR');error='model_response_error';break
  usage=response.get('usage');output_used+=(usage or {}).get('output_tokens',cap);input_used+=(usage or {}).get('input_tokens',0)
  msg=response.get('message',{});tc=msg.get('tool_calls',[])
  if len(tc)!=1:p.failure('response','MODEL_RESPONSE_ERROR');error='invalid_tool_response';break
  try:
   fn=tc[0]['function'];name=fn['name'];args=parse_json(fn['arguments'])
   if name not in {t['function']['name'] for t in allowed}:raise ValueError('Unavailable tool')
   if name=='submit':
    if set(args)!={'value'} or args['value'] is not None and (type(args['value']) is not int or args['value']<0):raise ValueError('Invalid final answer')
   elif name=='python':
    if set(args)!={'code'} or not isinstance(args['code'],str):raise ValueError('Invalid Python arguments')
   elif args:raise ValueError('Operator takes no arguments')
  except (ValueError,KeyError,TypeError):p.failure('tool_arguments','TOOL_ARGUMENT_ERROR');error='tool_argument_error';break
  messages.append(dict(role='assistant',content=msg.get('content'),tool_calls=tc))
  if name=='submit':
   if arm=='reflection' and not draft and not final:
    draft=True;messages.append(dict(role='tool',tool_call_id=tc[0]['id'],content='Draft only; independently review then submit.'));continue
   unverified=[e['result'] for e in p.events if e.get('tool')=='operator' and e['result']['execution_status']=='SUCCESS' and e['result']['validation_status']!='VERIFIED']
   recomputed=any(e.get('tool')=='python' and e['result']['execution_status']=='SUCCESS' and e['result']['result']==args['value'] for e in p.events)
   if unverified_policy=='require_recompute' and args['value'] is not None and unverified and not recomputed:
    p.failure('submission','FINAL_SUBMISSION_ERROR');error='unverified_output_policy_violation';break
   p.submit(args['value']);events.emit('submission','FINAL_SUBMISSION','SUCCESS',task_id=row['id'],output=args);break
  if tools_used>=3 or wall_used>=9.9:p.failure('tool','CODE_RESOURCE_LIMIT');error='tool_budget_exhausted';break
  # Only problem and code reach executor; expected remains outside this boundary.
  if name=='operator':operator_code=registry.load(operator_id,version)['code']
  result=await executor(operator_code if name=='operator' else args['code'],row['problem'],min(6,9.9-wall_used,max(.001,180-(time.monotonic()-started))),operator_id=operator_id if name=='operator' else 'python',version=version if name=='operator' else 'ephemeral')
  wall_used+=result['elapsed_seconds'];tools_used+=1;p.tool(name,result);raw_events.append(dict(function=name,tool_result=result))
  events.emit('tool','OPERATOR_CALL' if name=='operator' else 'PYTHON_CALL',result['execution_status'],task_id=row['id'],input=row['problem'],output=result,tool_id=operator_id if name=='operator' else 'python',error_type=result.get('error_type'),duration_ms=result['runtime_ms'])
  observation={k:v for k,v in result.items() if k!='trace'}
  if arm=='reflection' and result['execution_status']=='SUCCESS':draft=True;observation['review_instruction']='Independently verify your draft against all constraints; reserve final call.'
  messages.append(dict(role='tool',tool_call_id=tc[0]['id'],content=json.dumps(observation)))
 if not p.submitted:p.failure('submission','FINAL_SUBMISSION_ERROR')
 # Correctness attached only after inference loop ends; no reference in messages/executor args.
 result=evaluate(p.record(),row['expected']);result.update(id=row['id'],arm=arm,tier=row.get('tier','engineering'),scale=row.get('scale','engineering'),expected=row['expected'],error=error,calls=calls,tools=tools_used,output_tokens=output_used,tool_seconds=wall_used,seconds=time.monotonic()-started,raw_tool_events=raw_events,budget_violation=output_used>8192 or wall_used>10 or time.monotonic()-started>180)
 result.update(input_tokens=input_used,unverified_policy=unverified_policy,usage_note='Input/output here cover delivered responses; physical retry and unknown reservations remain in controller ledger.')
 ledger=getattr(transport,'controller',None)
 physical=[r for r in ledger.records() if r['logical_key'].startswith(f"pilot/{row['id']}/{arm}/")] if ledger is not None else []
 result.update(unknown_usage=sum(r.get('usage') is None for r in physical),api_request_attempts=len(physical) if ledger is not None else calls,draft=None,review_performed=arm!='reflection' or draft,end_to_end_wall_seconds=result['seconds'])
 # Retain legacy event accessors while keeping stage-level facts and evaluator annotations.
 for event in result['events']:
  event['function']=event.get('tool','submit' if event['stage']=='submission' and 'answer' in event else None)
  if event.get('tool'):event['tool_result']=event['result']
  if event['stage']=='submission' and 'answer' in event:event['arguments']={'value':event['answer']}
 events.emit('evaluation','INDEPENDENT_EVALUATION','SUCCESS' if result['correct'] else 'FAILED',task_id=row['id'],output=dict(answer=result['answer'],correct=result['correct']),error_type=None if result['correct'] else 'FINAL_WRONG_ANSWER')
 atomic_json(dest,result);return result
