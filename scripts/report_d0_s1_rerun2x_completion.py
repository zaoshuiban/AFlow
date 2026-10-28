"""Generate combined 13-task report; offline only."""
import hashlib,json
from pathlib import Path
import scripts.report_d0_s1_rerun2x as report
P=Path('results/experiment_d0_s1_20261019_rerun2x_completion_v1')
def main():
 report.P=P;report.main();protocol=json.loads((P/'protocol.json').read_text());prior=Path(protocol['prior_run'])
 assert hashlib.sha256((prior/'results.json').read_bytes()).hexdigest()==protocol['prior_results_sha256']
 assert hashlib.sha256((prior/'ledger.json').read_bytes()).hexdigest()==protocol['prior_ledger_sha256']
 audit=[]
 for root in [prior,P]:
  for f in (root/'tasks').glob('*/request_*.json'):
   request=json.loads(f.read_text())['request'];choice=request['tool_choice'];name=choice.get('function',{}).get('name') if isinstance(choice,dict) else None
   if name!='submit':continue
   response_path=f.with_name(f.name.replace('request_','response_'))
   response=json.loads(response_path.read_text());returned=[t['function']['name'] for t in response['choices'][0]['message'].get('tool_calls',[])]
   audit.append({'task':f.parent.name,'request':str(f),'tools_exposed':[t['function']['name'] for t in request['tools']],'requested':'submit','returned':returned,'honored':returned==['submit']})
 (P/'submit_choice_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
 reportfile=P/'D0-S1_翻倍预算重跑报告.md';text=reportfile.read_text();text+='\n## 补充证据核验\n\n共'+str(len(audit))+'个请求明确指定submit，其中'+str(sum(not a['honored'] for a in audit))+'个返回不符合该工具选择；逐次请求/响应对应关系见submit_choice_audit.json。该现象不等于算法错误，也不能仅凭它断言服务商实现错误。\n\n唯一最终运行异常来自 d0-count-v2-7900c60c26786d4f，错误为 `KeyError: \'1\'`；执行器返回RUNTIME_ERROR，未记为Wrong Answer或Judge Error。未进行最小反事实修复和根因确认，故不作机制结论。5题未提交均保留分母，其首次程序尚未作隐藏评分，不能据最终缺失推断其程序不正确。\n\n前5题结果及费用账本哈希与补充协议一致，未改动。合并报告复现命令：`.venv/bin/python -m scripts.report_d0_s1_rerun2x_completion`。线上执行、离线恢复及报告的本地资源与人工费用未换算为货币，未声称计入服务商账单。\n'
 reportfile.write_text(text)
 for name in ['complete_d0_s1_rerun2x.py','report_d0_s1_rerun2x_completion.py']:(P/name).write_bytes((Path('scripts')/name).read_bytes())
 manifest={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in P.rglob('*') if f.is_file() and f.name!='artifact_manifest.json'};(P/'artifact_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
 assert all(hashlib.sha256(Path(f).read_bytes()).hexdigest()==h for f,h in manifest.items())
 print('Artifact hashes verified:',len(manifest),'Submit tool mismatches:',sum(not a['honored'] for a in audit))
if __name__=='__main__':main()
