"""B1 final integrity, descriptive addendum and reproducible artifacts."""
import json,shutil
from pathlib import Path
from collections import Counter
from scripts.experiment_b1 import ROOT,verify
from scripts.run_experiment_b1 import verify_plan
from scripts.report_experiment_b1 import report
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash


def finalize():
 verify();verify_plan();freeze=read_json(ROOT/'execution_freeze.json')
 for p,h in freeze['sources'].items():assert file_hash(Path(p))==h,p
 report();a=read_json(ROOT/'analysis.json');obs=[];events=[];req=[]
 for arm in ('baseline','reflection'):
  obs.extend(read_json(f) for f in (ROOT/f'agent_{arm}/observations').glob('*.json'))
  req.extend(read_json(f) for f in (ROOT/f'agent_{arm}/api_attempts').glob('*.json'))
 events=[e for f in (ROOT/'detected_new').glob('*.json') for e in read_json(f)['events']]
 assert len(obs)==72 and len({(r['id'],r['arm']) for r in obs})==72
 assert not any(r['budget_violation'] for r in obs);assert len(req)==a['total_usage']['requests'];assert a['total_usage']['reserved_or_reported_cost_cny']<=8
 rejected=Counter(e['output']['error'] for e in events if e['failure_family']=='Tool Integration')
 audit=read_json(ROOT/'new_source_mechanism_audit.json');m1ids={r['base_task_id'] for r in audit['verified']};related_ids={r['base_task_id'] for r in audit.get('related_compound_defects',[])}
 reflection_cases=[]
 for tid in sorted(m1ids|related_ids):
  paired={arm:read_json(ROOT/f'agent_{arm}/observations/{tid}_r0_{arm}.json') for arm in ('baseline','reflection')}
  reflection_cases.append(dict(task_id=tid,strict_prespecified_M1=tid in m1ids,arms={arm:{k:r[k] for k in ('correct','answer','calls','tools','input_tokens','output_tokens','seconds')} for arm,r in paired.items()}))
 atomic_json(ROOT/'mechanism_specific_reflection.json',reflection_cases)
 atomic_json(ROOT/'integration_censoring.json',dict(error_counts=dict(rejected),affected_executions=sum(any(e.get('error_type') in ('CODE_CONTRACT_ERROR','CODE_IMPORT_ERROR') for e in r['events']) for r in obs),total_executions=72,note='Strict stable execution contract, not math incapability; no unsafe bypass, filtering, retries or score replacement. Historical/new environment rates not causally comparable.'))
 # Restore current detector documentation while preserving its initial exploratory version.
 path=ROOT/'historical_trace_analysis.md'
 if not (ROOT/'detector_v0/historical_trace_analysis.md').exists():shutil.copyfile(path,ROOT/'detector_v0/historical_trace_analysis.md')
 path.write_text('# Historical trace analysis (detector v1)\n\n'+json.dumps(a['natural_history'],ensure_ascii=False,indent=2)+'\n\nThe original preliminary v0 output is retained under detector_v0/. Current v1 does not classify hypothesis-probe AssertionError as verified code defects. Broad operational failures and strict wrong-count returns must be kept separate; functional subset: '+json.dumps(a['history_review']['functional'],ensure_ascii=False)+'\n')
 marker='\n## 12. 最终源码审查与解释边界\n'
 appendix=marker+f'''
本轮新运行有 **70/72** 次至少一次工具集成错误，具体拒绝原因：`{dict(rejected)}`。主要为顶层测试调用和装饰器；虽然公开环境说明已禁止，模型仍提交这些形式，实际预算被接口恢复占用。**这一结果不能解释为70次数学能力不足。** 稳定隔离、接口和预算没有为取得理想结果而放宽；也没有重跑直到成功。本轮的新旧比例不能直接比较，因为历史执行器接受更多源码形式。

严格冻结的M1仅有**1个**新诊断输入得到直接复现：Baseline程序错误地比较末尾相邻元素，补首元素状态后由46980变为正确35640。另一个诊断输入中的程序遗漏缓存键的首元素上下文，且计数只限制上界，原6448，单独上下文修复6402，单独精确计数修复1296，两者一起才正确1728；删除首尾条件后，精确计数修复已足够。后者属于**相关但不同的事后复合缺陷，不计M1复现**：单独补精确计数后是少计1296，不符合冻结的纯遗漏首尾约束之过计预测。M1因此记Grade A（新Agent稳定性未建立），不能为了凑足两例而扩大机制定义。

直接M1题及另列的复合缺陷题，配对Reflection均最终正确，两个系统均用3次工具调用；一题Reflection耗时略高，另一题略低。没有证据证明普通Reflection为该机制承担稳定、显著的额外成本。全部配对资源见 mechanism_specific_reflection.json，并标明哪一例属于严格预设M1；不能只因某组Token更少或终局更快而判断能力收益。

M2在另外两个诊断输入上出现整数/字符比较错误，类型转换的最小修复分别恢复精确计数；其中一个缩至n=7后即使错误代码也正确，说明类型缺陷不一定影响所有参数。该复现是M1诊断数据中的**事后次级发现**，不是独立设计的M2反事实/确认实验。故可暂记B级局部实现弱点，但机会仍Uncertain，不能凭这两题推荐算子。

M1的独立确认8题没有新增经过同等源码干预确认的同机制事件；其中存在其他错误及接口拒绝，不能将“未核验到”解释为“错误率为0”。确认输入没有用于修改触发条件或程序修复，全部8题原分母保留。新诊断中的复合缺陷修复只在Development输入上进行，未把其结果伪装成独立确认。

Table 5的新增信号为本次最多3个审查机制范围内的2个候选，经过新配对程序诊断的是M1；这验证了局部错误可以补充研究线索，尚未验证可编程能力复用的必要性或净收益。建议下一步先在不降低隔离的前提下解决**工具代码契约遵循/接口适配的测量混杂**，再以新任务做小规模复核，暂不进入完整算子合成。若继续机制研究，优先保持小粒度、可执行验证及清晰的类型/状态契约。

自动检测只负责可观察事实和精确返回值比较；机制源码审查与最小干预由研究助手辅助。不存在自动聚类或自主发现成果声明。复现完整最终分析：`.venv/bin/python -m scripts.audit_b1_sources`（需要原生沙箱许可，仅本地开发输入），随后 `.venv/bin/python -m scripts.finalize_experiment_b1`。检测及协议检查：`.venv/bin/python -m unittest tests.test_experiment_b1 tests.test_experiment_b1_protocol`，11项通过。
'''
 for name in ('experiment_b1_report.md','mechanism_validation_report.md','capability_gap_report.md','local_failure_report.md'):
  p=ROOT/name;p.write_text(p.read_text().split(marker)[0]+appendix)
 atomic_json(ROOT/'final_integrity_audit.json',dict(history_snapshot_files_checked=len(read_json(ROOT/'manifest.json')['history_snapshot']),all_history_hashes_unchanged=True,all_execution_sources_unchanged=True,all_prediction_and_input_hashes_unchanged=True,observations=72,unique_trial_method_pairs=72,operators_generated=0,confirmation_used_for_repairs=False,budget_violations=0,analysis_source_hashes={str(p):file_hash(p) for p in [Path('scripts/report_experiment_b1.py'),Path('scripts/finalize_experiment_b1.py'),Path('scripts/audit_b1_sources.py'),Path('tests/test_experiment_b1_protocol.py')]}))
 atomic_json(ROOT/'completion.json',dict(complete=True,natural_historical_trajectories=60,new_natural_api_trials=0,targeted_agent_trials=72,diagnostic_pairs=10,repeated_task_instances=4,extra_repeat_trials=16,independent_confirmation_inputs=8,mechanisms_deeply_tested=1,operators_generated=0,tests_passed=11,api_usage=a['total_usage'],recommendation='Local errors add mechanism leads; provisional B weaknesses, no Grade C or High Opportunity; resolve execution-contract confounding before new synthesis.'))
 print(dict(complete=True,usage=a['total_usage'],gaps=[(g['mechanism_id'],g['grade'],g['opportunity']) for g in a['gaps']]),flush=True)

if __name__=='__main__':finalize()
