"""Post-evaluation factorial source interventions, never Agent feedback or method updates."""
import asyncio,json,difflib
from pathlib import Path
from scripts.experiment_b16 import ROOT
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.python_tool_interface_v3 import executor

async def run():
 inventory={r['evidence_id']:r for r in read_json(ROOT/'hidden_source_inventory.json')};tasks={read_json(p)['task']['id']:read_json(p)['task'] for p in (ROOT/'hidden_events').glob('*.json')};type_specs={r['evidence_id']:r for r in read_json(ROOT/'hidden_intervention_specs.json')}
 cases={
 'hidden_753deb25c59dd404a83c_C_s0':[
 ['key = (s, c0, 0, sm)','key = (s, s, c0, 0, sm)'],
 ['for (last, c0, pc, sm), v in dp.items():','for (first, last, c0, pc, sm), v in dp.items():'],
 ['key = (s, nc0, npc, nsm)','key = (first, s, nc0, npc, nsm)'],
 ['            pass\n        total += v','            if first == last:\n                continue\n        total += v']],
 'hidden_6109642627d2b2c9d426_C_s0':[
 ['dp[(None, 0, 0, ())]','dp[(None, None, 0, 0, ())]'],
 ['for (last, cnt, sm, suf), ways in dp.items():','for (first, last, cnt, sm, suf), ways in dp.items():'],
 ['ndp[(d, ncnt, nsm, nsuf)]','ndp[(d if first is None else first, d, ncnt, nsm, nsuf)]'],
 ['            pass\n        total += ways','            if first == last:\n                continue\n        total += ways']],
 'hidden_6027bf093eacfdbe26d9_R_s1':[
 ['    seen = set()','    states = [()] + [(a,) for a in alpha] + states\n    seen = set()']]
 }
 results=[]
 def patch(code,reps):
  for a,b in reps:assert a in code;code=code.replace(a,b)
  return code
 for sid,other in cases.items():
  r=inventory[sid];task=tasks[r['task_id']];code=Path(r['code_path']).read_text();ty=type_specs[sid]['replacements'];variants={'original':code,'type_only':patch(code,ty),'other_only':patch(code,other),'both':patch(patch(code,ty),other)};outputs={}
  for name,src in variants.items():
   dest=ROOT/'compound_variants'/(sid+'_'+name+'.py');dest.parent.mkdir(exist_ok=True);dest.write_text(src)
   out=await executor(src,task['problem'],6);outputs[name]=dict(execution=out,correct=out.get('status')=='success' and out.get('value')==task['expected'],source=str(dest),sha256=file_hash(dest))
  results.append(dict(evidence_id=sid,task_id=r['task_id'],method=r['method'],classification='COMPOUND_DEFECT',strict_M2_credit=False,expected=task['expected'],variants=outputs,explanation='Type mismatch plus missing endpoint information' if r['method']=='C' else 'Type mismatch plus missing reachable short-prefix transition states; original type comparison is masked by state initialization failure.'))
 atomic_json(ROOT/'compound_defects.json',results)
 print(json.dumps([dict(id=r['evidence_id'],expected=r['expected'],outputs={k:v['execution'].get('value') for k,v in r['variants'].items()}) for r in results],indent=2))
if __name__=='__main__':asyncio.run(run())
