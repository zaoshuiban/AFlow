"""Task-specific input/output schema adapters, never algorithm edits."""
import json

def taco_task(tid,record):
 mode=record['interface'];io=record['io'];interface={'mode':mode}
 if mode=='function':interface['function']=io['fn_name']
 if mode=='class':interface.update({'class':'Solution','method':io['fn_name']})
 cases=[]
 for inp,out in zip(io['inputs'],io['outputs']):
  if mode=='stdin':
   if not isinstance(inp,str):raise ValueError('Unsupported stdin input wrapper')
   if isinstance(out,list) and all(isinstance(x,str) for x in out):out='\n'.join(out)
   if not isinstance(out,str):raise ValueError('Unsupported stdin expected type')
   cases.append(dict(stdin=inp,expected=out))
  else:
   if type(inp) is not list:raise ValueError('Function args must be explicit positional array')
   cases.append(dict(args=inp,expected=out))
 judge={'mode':'tokens'} if mode=='stdin' else {'mode':'exact'}
 if tid=='taco-test-40':
  # Public return is (amount,max), two ints; corpus stores a singleton return envelope.
  judge={'mode':'taco_single_return','return_schema':'int_pair'}
 return dict(id=tid,source='TACO',prompt=record['question'],interface=interface,judge=judge,cases=cases,case_wall=6,limits=dict(cpu_seconds=5,output_bytes=2*1024**2))

def lcb_task(tid,record,cases):
 metadata=record.get('metadata',{});mode=record['interface'];interface={'mode':mode,'context':'lcb-safe-v1'}
 if mode=='function':interface['function']=metadata['func_name']
 if mode=='class':interface.update({'class':'Solution','method':metadata['func_name']})
 normalized=[]
 for c in cases:
  if mode=='stdin':normalized.append(dict(stdin=c['input'],expected=c['output']))
  else:
   args=[json.loads(s) for s in c['input'].splitlines() if s.strip()];expected=json.loads(c['output']);normalized.append(dict(args=args,expected=expected))
 return dict(id=tid,source='LiveCodeBench',prompt=record['prompt'],interface=interface,judge={'mode':'tokens' if mode=='stdin' else 'exact'},cases=normalized,public_examples=record['public_examples'],case_wall=6,limits=dict(cpu_seconds=5,output_bytes=2*1024**2))
