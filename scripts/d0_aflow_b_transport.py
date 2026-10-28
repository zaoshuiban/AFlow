"""Injected-client bounded transport. No API client creation, credentials, or autorun."""
import json,time,re
from pathlib import Path
from datetime import datetime,timezone
from scripts.d0_aflow_b_adapter import BudgetFault,FormatFault,EmptyOutput,code_extract
class BoundedTransport:
 def __init__(self,client,root,stage_cap_cny,max_calls=8):
  self.client=client;self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.max_calls=max_calls;self.cap=stage_cap_cny;self.requests=[];self.cost_manager=type('Cost',(),{'total_cost':0.0})();self.committed=0.0
 def save(self):
  (self.root/'ledger.json').write_text(json.dumps({'committed_upper_cny':self.committed,'cap_cny':self.cap,'requests':self.requests},ensure_ascii=False,indent=2))
 async def request(self,model,context,mode,function_name=None):
  if len(self.requests)>=self.max_calls:raise BudgetFault('node call cap')
  key='reflection_and_solution' if model.__name__=='ReflectionTestOp' else 'response'
  if mode=='context_fill':
   context+='\nReturn strictly XML fields: '+''.join('<'+k+'>'+str(f.description or '')+'</'+k+'>' for k,f in model.model_fields.items())
  elif mode=='code_fill':context+='\nReturn complete runnable Python source, optionally in one python code fence, with no explanation. Preserve the public task interface.'
  prompt=[{'role':'user','content':context}];bound=len(json.dumps(prompt,ensure_ascii=False).encode())+2048
  if bound>32000:raise BudgetFault('input reservation exceeds per-node cap')
  reservation=(bound*2+8192*8)/1e6
  if self.committed+reservation>self.cap:raise BudgetFault('stage cost reservation')
  request={'model':'deepseek-v4-flash','messages':prompt,'temperature':0,'max_tokens':8192,'extra_body':{'thinking':{'type':'disabled'}}}
  row={'status':'IN_FLIGHT','physical_model_request':True,'mode':mode,'schema':model.__name__,'function_name':function_name,'request':request,'reserved_cny':reservation,'utc':datetime.now(timezone.utc).isoformat()};self.requests.append(row);self.committed+=reservation;self.save();start=time.monotonic()
  try:response=await self.client.chat.completions.create(**request,timeout=60)
  except Exception as e:
   row.update(status='UNKNOWN_DELIVERY',error_type=type(e).__name__,seconds=time.monotonic()-start);self.save();raise
  raw=response.model_dump();(self.root/f'response_{len(self.requests)-1}.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
  usage=response.usage
  if usage is None:row['status']='UNKNOWN_USAGE';self.save();raise BudgetFault('usage missing; reservation retained')
  cost=(usage.prompt_tokens*2+usage.completion_tokens*8)/1e6;self.committed+=cost-reservation;self.cost_manager.total_cost+=cost
  row.update(status='COMPLETED',returned_model=response.model,usage=usage.model_dump(),cost_upper_cny=cost,seconds=time.monotonic()-start);self.save()
  if usage.prompt_tokens>bound or usage.completion_tokens>8192:raise BudgetFault('reported usage exceeds reservation bounds')
  text=response.choices[0].message.content
  if not isinstance(text,str) or not text.strip():raise EmptyOutput('empty actual content')
  if mode=='context_fill':
   found=dict(re.findall(r'<(\w+)>(.*?)</\1>',text,re.S));letter=found.get('solution_letter','').strip()
   if not letter:raise FormatFault('missing ensemble selection')
   return {k:v.strip() for k,v in found.items()}
  if mode=='code_fill':return {key:code_extract(text)}
  return {'response':text}
