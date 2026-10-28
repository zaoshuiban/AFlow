"""Finalize B0 descriptive report after all frozen trials complete."""
import json
from pathlib import Path
from scripts.experiment_b0 import ROOT,verify
from scripts.report_experiment_b0 import report
from scripts.audit_b0_provenance import audit
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash


def finalize():
 verify();report();audit()
 a=read_json(ROOT/'diagnostic_trace_audit.json');d=read_json(ROOT/'analysis.json')
 checks=a['recovered_case']['factorial_checks']
 assert all(c['correct']==(c['history_intervention'] and c['type_intervention']) for c in checks)
 assert a['control_failure']['intervention_result']['value']==a['control_failure']['expected']
 rows={m:{r['id']:r for r in [read_json(f) for f in (ROOT/f'evaluation_{m}/observations').glob('*.json')]} for m in ('reflection','direct','mechanism')}
 infra={i for i,r in rows['reflection'].items() if r['error'].startswith('request_failure:')}
 sensitivity={'excluded_only_for_sensitivity':sorted(infra),'n':44-len(infra),'correct':{m:sum(r['correct'] for i,r in rs.items() if i not in infra) for m,rs in rows.items()},'primary_all44_unchanged':True}
 propagation={}
 for method in ('direct','mechanism'):
  wrong=[]
  for r in rows[method].values():
   t=next(e['tool_result'] for e in r['events'] if e['function']=='operator' and 'tool_result' in e)
   if t['status']=='success' and t['value']!=r['expected']:wrong.append((r,t))
  propagation[method]=dict(wrong_successful_outputs=len(wrong),final_repeated_wrong=sum(r['submitted'] and r['answer']==t['value'] for r,t in wrong),recovered=sum(r['correct'] for r,t in wrong))
 atomic_json(ROOT/'infrastructure_sensitivity.json',sensitivity);atomic_json(ROOT/'wrong_output_propagation.json',propagation)
 text='''\n\n## 补充审计：机制可信程度、实际候选与复现边界\n\n对诊断中 recovered local failure（92088c8c1b8a6f763b81）的首次实际程序做2×2干预：原版、仅后缀修复、仅类型修复均错误；同时修复后，在原 n=10 及独立 n=6 检查都正确（n=6 又经暴力枚举确认）。原程序先截断为2元素，再比较3元素模式，且将整数元组与字符元组比较，两个缺陷共同使匹配不可达。Agent 后续改用枚举得到正确最终答案。此证据支持“新 Agent 的成功轨迹也可能含可验证状态缺陷”，不能支持“长模式使 Agent 终局稳定失败”。\n\n唯一诊断终局错发生于长度2对照 c75c08437f7aee11656a：初次代码显式跳过多元素模式，第二次把 prev 与新元素比较，漏掉真正相邻的 last。单行改用 last 后得到正确27720（原终局61376）。因此预设的长模式相关性不能完整识别一般状态转移错误；这属于反对过窄触发边界的证据。两次审查均在算子冻结后进行，未反馈生成器，未修改算子或分数，见 diagnostic_trace_audit.json。\n\n生成对照中的首次连接失败不可解释为数学能力差：初始生成质量为 NA；第二个请求槽位才返回第一份真实代码，两组都没有对这份真实源码执行过修复。F 首份真实代码在公共整数 alphabet 上用字符索引字典而出现 KeyError；M 首份真实代码仍在匹配前截断后缀，且首尾约束未完成。显式机制说明并不保证模型落实行为要求；本轮结果无法将两组差异可靠归因于机制信息。未追加候选直至获得理想结果。\n\n完整 Full Solver 的域内适用性与机制相关性不同。转移/对照的12+12结构标签只检验冻结的目标相关性规则，不能证明“何时调用能带来净收益”。没有实际条件路由实验，也没有域外有效性阴性样本；适用性分析的边界明确保留。\n\n本轮建议是：首先投入可验证局部错误发现与因果诊断，并修正后续实验中“网络失败消耗源码修复机会”的控制流程，再用新封存题和独立生成种子重复小规模对照。不要据此次结果扩大自动发现系统，也不要将 API 失败从当前分母删除。\n'''
 text+='\n基础设施敏感性分析（全44题主表不变）：Reflection 有5题连接失败；同时去掉这5个配对后，Reflection 33/39、Direct 29/39、Mechanism 14/39。M 在全体主表相对 Reflection 的3次胜出全部来自对方连接失败，非已证实的数学失败修复；有效配对上为0次修复、19次新错。Direct 的总体+1也不能解释为能力收益。完整配对排除名单见 infrastructure_sensitivity.json。\n\n错误输出传播审计：Direct 首次工具有6次成功返回错误整数，6次全部由后续处理恢复；其余38次为执行异常。Mechanism 首次工具有41次成功返回错误整数，其中14次被后续处理修复、26次最终仍提交同一个错误值，另1次改成其他错误值。该差异支持区分“可见异常”和“正常返回的错误结果”，不能把低Token/时延解释为成本有效收益。见 wrong_output_propagation.json。\n'
 for name in ['experiment_b0_report.md','mechanism_diagnosis.md','research_direction_report.md','evaluation_report.md','operator_generation_report.md']:
  p=ROOT/name;s=p.read_text().split('\n\n## 补充审计：')[0];p.write_text(s+text)
 reproducible=['scripts/experiment_b0.py','scripts/report_experiment_b0.py','scripts/audit_b0_diagnostics.py','scripts/audit_b0_provenance.py','scripts/finalize_experiment_b0.py']
 atomic_json(ROOT/'analysis_sources.json',{p:file_hash(Path(p)) for p in reproducible})
 allreq=[read_json(p) for p in ROOT.glob('*/requests/*.json')]
 assert len(allreq)==d['total_usage']['requests']
 assert d['total_usage']['reserved_or_reported_cny']<=14
 assert sum(r['budget_violations'] for r in d['results']['all'].values())==0
 atomic_json(ROOT/'completion.json',dict(complete=True,diagnostic_observations=20,evaluation_observations=132,all_original_denominators_retained=True,zero_automatic_retries=True,
    source_and_seal_verified=True,external_api_requests=len(allreq),unknown_usage=sum(r.get('usage') is None for r in allreq),estimated_cost_not_invoice=True,
    analysis_reproduce='.venv/bin/python -m scripts.audit_b0_diagnostics && .venv/bin/python -m scripts.finalize_experiment_b0'))
 print('Final report and provenance complete')

if __name__=='__main__':finalize()
