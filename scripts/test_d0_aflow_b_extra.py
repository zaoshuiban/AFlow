"""Additional independent selection/isolation/sandbox boundary checks; offline only."""
import asyncio,json
from scripts.d0_aflow_b_adapter import P,Adaptation,ReplayTransport
from scripts.d0_s01_runtime.runner import execute_case
async def main():
 pub={'interface':{'mode':'function','function':'f'},'cases':[{'args':[2],'expected':3}],'judge':{'mode':'exact'},'source':'public fixture'}
 good='def f(x):\n return x+1\n';bad='def f(x):\n return 0\n'
 seq=[{'response':bad},{'response':good},{'response':bad},{'solution_letter':'B'}]
 a=Adaptation(pub,ReplayTransport(seq),P/'extra_replays/ensemble_B');r=await a.run('public spec','f');assert r['source']==good and len(a.transport.requests)==4
 markers=[]
 for name in ['10_public_hidden_isolation']:
  replay=json.loads((P/'mock_replays'/name/'replay.json').read_text());assert '987654321' not in json.dumps(replay['requests']);markers.append('hidden expected marker absent from every online node request')
 r=await execute_case('def f(x):\n open("/tmp/AFLOW_FORBIDDEN_WRITE","w").write("x")\n return x',pub['interface'],{'args':[2],'expected':2},role='self_test',log_dir=P/'extra_replays/policy')
 assert r['verdict'] in ['POLICY_VIOLATION','RUNTIME_ERROR','INTERFACE_REJECTION'],r
 assert not __import__('pathlib').Path('/tmp/AFLOW_FORBIDDEN_WRITE').exists()
 tests=[{'name':'distinct candidates select B','passed':True},{'name':markers[0],'passed':True},{'name':'candidate file write blocked in native sandbox','passed':True,'status':r['verdict']}]
 (P/'extra_test_results.json').write_text(json.dumps(tests,ensure_ascii=False,indent=2));print('extra checks',len(tests),'API 0')
if __name__=='__main__':asyncio.run(main())
