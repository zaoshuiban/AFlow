"""Real-code generations and repairs separated from physical API attempts."""
import json,uuid
from scripts.offline_loop_protocol import atomic_json,digest
from .api import InfrastructureInterrupted
from .events import EventLog

async def synthesize(controller,messages,validator,max_repairs=1,key='candidate_1'):
 messages=list(messages);initial=0;repairs=0;parsed=0;code=None;attempts=[]
 def finish(result):
  result['model_generation_attempts']=parsed
  result['counter_note']='initial_generation_attempts counts first usable source; model_generation_attempts/parsed_model_responses count parseable source responses including repairs; code_repair_attempts counts delivered repair operations on real source (even malformed output), excluding infrastructure interruptions.'
  atomic_json(controller.root/'generations'/(digest(key)+'_'+uuid.uuid4().hex+'.json'),result)
  return result
 for slot in range(max_repairs+1):
  phase='generation' if code is None else 'repair'
  try:response=await controller.request(f'{key}/{phase}/{slot}',dict(messages=messages),8192,phase=phase)
  except InfrastructureInterrupted as exc:
   return finish(dict(status='INFRASTRUCTURE_INTERRUPTED',code=code,initial_generation_attempts=initial,code_repair_attempts=repairs,parsed_model_responses=parsed,api_request_attempts=controller.usage()['api_request_attempts'],attempts=attempts,error=str(exc)))
  if code is not None:repairs+=1
  try:
   data=json.loads(response['response']);new=data['code']
   if not isinstance(new,str) or not new.strip():raise ValueError('Missing real candidate source')
  except (ValueError,KeyError,TypeError):
   controller.log.emit('generation','MODEL_GENERATION','RESPONSE_PARSE_ERROR',error_type='MODEL_RESPONSE_ERROR',output=response)
   return finish(dict(status='MODEL_RESPONSE_ERROR',code=code,initial_generation_attempts=initial,code_repair_attempts=repairs,parsed_model_responses=parsed,api_request_attempts=controller.usage()['api_request_attempts'],attempts=attempts))
  parsed+=1
  if code is None:initial+=1
  code=new;validation=await validator(code);attempts.append(dict(phase=phase,code=code,validation=validation))
  controller.log.emit('generation',phase.upper(),'SUCCESS',input=messages,output=data)
  if validation['passed']:break
  messages.extend([dict(role='assistant',content=response['response']),dict(role='user',content='Repair this real candidate using independent development feedback: '+json.dumps(validation))])
 return finish(dict(status='VALIDATED' if validation['passed'] else 'REJECTED',code=code,initial_generation_attempts=initial,code_repair_attempts=repairs,parsed_model_responses=parsed,api_request_attempts=controller.usage()['api_request_attempts'],attempts=attempts))
