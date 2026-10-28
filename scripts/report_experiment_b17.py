"""Read-only evidence accounting and Chinese B1.7 report. Never runs new model calls."""
import csv,difflib,hashlib,json,statistics
from pathlib import Path
from collections import Counter,defaultdict
from scripts.experiment_b17 import ROOT,snapshot
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.experiment_b1 import load_codes
from scripts.operator_runtime.events import hash_value

def main():
 assert snapshot()==read_json(ROOT/'history_before.json')
 for freeze_name in ['checker_freeze.json','stage_B_protocol.json']:
  for f,h in read_json(ROOT/freeze_name)['sources'].items():assert file_hash(Path(f))==h,f
 unknown=read_json(ROOT/'unknown_53_audit.json');gate=read_json(ROOT/'stage_A_gate.json');dev=read_json(ROOT/'development_detection.json');confirm=read_json(ROOT/'confirmation_detection.json');fixtures=read_json(ROOT/'sealed/confirmation_programs.json')
 phases=defaultdict(Counter);times=defaultdict(list)
 for r in dev:phases[r['label']][r['result']['status']]+=1;times[r['label']].append(r['wall'])
 dev_stats={k:dict(v,n=sum(v.values()),mean_seconds=statistics.mean(times[k]),max_seconds=max(times[k])) for k,v in phases.items()}
 fixture_unique={label:len({hashlib.sha256(r['code'].encode()).hexdigest() for r in fixtures if r['label']==label}) for label in ['M2','NON_M2','UNKNOWN']}
 confirmation_times={kind:{label:dict(mean_seconds=statistics.mean([r['results'][kind]['elapsed_seconds'] for r in confirm if r['label']==label]),n=sum(r['label']==label for r in confirm)) for label in ['M2','NON_M2','UNKNOWN']} for kind in ['generic','mechanism']}
 atomic_json(ROOT/'detection_confusion_matrices.json',dict(historical_development=dev_stats,independent_semantic_fixture_confirmation=gate['metrics'],confirmation_times=confirmation_times,unique_source_hashes_by_label=fixture_unique,positive_no_warning='Includes PASS and ABSTAIN; abstention is not a correct negative',unknown_labels_excluded_from_precision=True,scope='Confirmation cases are grouped variants of four new positive source templates, not 32 independent natural Agent programs. No statistical population-precision claim.'))
 cps={p.stem:read_json(p) for p in (ROOT/'checkpoints').glob('*.json')};branches=[read_json(p) for p in sorted((ROOT/'branch_results').glob('*.json'))];assert len(cps)==6 and len(branches)==18
 by={(r['task_id'],r['branch']):r for r in branches};paired=[];summary={};all_sources=[]
 for arm in ['R','G','C']:
  z=[r for r in branches if r['branch']==arm];obs=[r['observation'] for r in z if 'observation' in r];statuses=Counter((r.get('checker') or {}).get('status','NOT_RUN') for r in z)
  summary[arm]=dict(units=6,final_correct=sum(r['correct'] for r in z),initial_observed_wrong=sum(cp['status']=='READY' and cp['tool_result'].get('status')=='success' and cp['tool_result']['value']!=cp['task']['expected'] for cp in cps.values()),verified_initial_M2=0,actual_feedback=sum((r.get('checker') or {}).get('status')=='VIOLATION' for r in z),checker_statuses=dict(statuses),infrastructure_failures=sum(r['status'] in ['infrastructure_interrupted','COMMON_CHECKPOINT_UNAVAILABLE'] for r in z),model_calls_including_common=sum(o['calls'] for o in obs),output_tokens_including_common=sum(o['output_tokens'] for o in obs),input_tokens_including_common=sum(o['input_tokens'] for o in obs),tool_seconds_including_common=sum(o['tool_seconds'] for o in obs),continuation_seconds=sum(r.get('continuation_seconds',0) for r in z),mean_trajectory_seconds=statistics.mean(r.get('total_trajectory_wall_seconds',0) for r in z),checker_seconds=sum((r.get('checker') or {}).get('elapsed_seconds',0) for r in z),repair_rate=None,repair_rate_reason='No verified defective initial checkpoints; denominator zero, not 0% recovery.')
  for r in z:
   if 'observation' not in r:continue
   o=r['observation'];cp=cps[r['task_id']];codes=load_codes(ROOT/('stage_B_'+arm),o)
   for i,e in enumerate(o['events']):
    raw=e.get('tool_result')
    if not raw:continue
    src=codes.get(i)
    if raw.get('trace',{}).get('code_sha256')==hash_value(cp['code']):src=cp['code']
    if src:
     p=ROOT/'branch_sources'/(r['task_id']+'_'+arm+'_s'+str(i)+'.py');p.parent.mkdir(exist_ok=True);p.write_text(src)
     all_sources.append(dict(task_id=r['task_id'],branch=arm,step=i,source=str(p),source_sha256=file_hash(p),same_as_checkpoint=src==cp['code'],diff_from_checkpoint=''.join(difflib.unified_diff(cp['code'].splitlines(True),src.splitlines(True))),execution=raw,returned_correct=raw.get('status')=='success' and raw.get('value')==cp['task']['expected']))
 for a,b in [('C','R'),('G','R'),('C','G')]:
  for tid in cps:
   x,y=by[(tid,a)],by[(tid,b)];paired.append(dict(task_id=tid,comparison=a+'-'+b,checkpoint_sha256=file_hash(ROOT/'checkpoints'/(tid+'.json')),new_correct=x['correct'],reference_correct=y['correct'],outcome='win' if x['correct'] and not y['correct'] else 'loss' if y['correct'] and not x['correct'] else 'both_correct' if x['correct'] else 'both_wrong',both_infrastructure_valid=x['status'] not in ['infrastructure_interrupted','COMMON_CHECKPOINT_UNAVAILABLE'] and y['status'] not in ['infrastructure_interrupted','COMMON_CHECKPOINT_UNAVAILABLE']))
 with (ROOT/'checkpoint_paired_results.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(paired[0]));w.writeheader();w.writerows(paired)
 atomic_json(ROOT/'branch_program_audit.json',dict(programs=all_sources,note='Same initial source and observed result replayed; later newly generated sources retained with diffs. Correct result alone does not prove absence of latent M2.'))
 result=dict(checkpoints=6,branches=18,all_denominators_preserved=True,statistics=summary,paired=paired,sensitivity={c:dict(eligible=sum(r['both_infrastructure_valid'] for r in paired if r['comparison']==c),outcomes=dict(Counter(r['outcome'] for r in paired if r['comparison']==c and r['both_infrastructure_valid']))) for c in ['C-R','G-R','C-G']},recovery_categories=dict(correctly_detected_and_repaired='NOT_ESTIMABLE_NO_INITIAL_DEFECT',detected_repair_failed=0,reflection_self_repair='NOT_ESTIMABLE_NO_INITIAL_DEFECT',false_alarm_unnecessary_edit=0,introduced_functional_error=sum(not r['returned_correct'] for r in all_sources),correct_answer_without_proven_target_repair='All final answers correct, no verified initial target defect to repair',infrastructure_or_resource_failures=sum(o['infrastructure_failures'] for o in summary.values())),stage_C_gate_passed=False,stop='No verified initial M2 and no paired gain. Do not append tasks to find failures.')
 atomic_json(ROOT/'checkpoint_results.json',result)
 atomic_json(ROOT/'stage_C_status.json',dict(status='NOT_RUN_STAGE_B_GATE_FAILED',natural_incidence=None,natural_accuracy=None,regression_rate=None,reason='No effective fixed-checkpoint repair evidence; independent natural study not started, not zero benefit/zero incidence.'))
 ledger={x:read_json(ROOT/('stage_B_'+x)/'usage.json') for x in ['common','R','G','C']};known=sum(x['known_token_estimate_cny'] for x in ledger.values());reserved=sum(x['reserved_or_reported_cost_cny'] for x in ledger.values());unknown_requests=sum(x['unknown_usage_requests'] for x in ledger.values());assert reserved<=10
 costs=dict(API_known_token_estimate_cny=known,conservative_ledger_cny=reserved,unknown_usage_requests=unknown_requests,actual_invoice_cny=None,physical_requests=sum(x['api_request_attempts'] for x in ledger.values()),by_phase=ledger,per_method_standalone_estimate={a:dict(known_cny=ledger['common']['known_token_estimate_cny']+ledger[a]['known_token_estimate_cny'],known_per_correct_cny=(ledger['common']['known_token_estimate_cny']+ledger[a]['known_token_estimate_cny'])/summary[a]['final_correct'],checker_seconds=summary[a]['checker_seconds']) for a in ['R','G','C']},common_cost_accounting='Actual physical ledger charges shared generation once. Per-method standalone estimates attribute the full common generation needed for that method, so their sum is larger than actual total.',build_and_validation_cost=dict(direct_model_API_cny=0,local_execution_seconds=sum(sum(v) for v in times.values())+sum(r['results'][k]['elapsed_seconds'] for r in confirm for k in ['generic','mechanism']),researcher_and_assistant_cost_cny=None,unpriced_not_zero=True),total_cost_per_correct='Fully loaded total is unknown because engineering/assistant effort is not priced. Online known estimates only shown; not a net economic deployment claim.')
 atomic_json(ROOT/'cost_analysis.json',costs)
 old=Path('scripts/pattern_type_contract_checker.py').read_text();new=Path('scripts/b17_checkers.py').read_text();(ROOT/'checker_changes.diff').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='frozen_B1.6_static_checker',tofile='new_B1.7_runtime_contract_checker')))
 atomic_json(ROOT/'research_decision.json',dict(status='NO_DISTINCT_MECHANISM_VALUE',detection='Established for declared executed-comparison scope on small synthetic confirmation, limited historical coverage',G_vs_C='Same public-schema observable contract and shared engine; no additional predicate or capability in C. Not proof against every possible failure-driven checker.',intervention='NOT_ESTABLISHED: no initial verified M2 among six frozen checkpoints; 6/6 correct in every branch does not demonstrate repair.',deployment='NOT_EVALUATED_STAGE_C_NOT_RUN',generate_solver=False,additional_search=False))
 text='''# Experiment B1.7：失败驱动局部检查器的可检测性与因果修复实验

**研究决策：NO_DISTINCT_MECHANISM_VALUE。** 检测范围内已有非零有效警告，但当前机制Checker没有超出公开类型契约Generic Checker的独立能力。新检查点没有提供可验证的初始M2错误，因果修复收益未建立，按门槛停止阶段C。没有生成完整求解器或追加失败搜索。

## 协议与数据边界

运行ID：experiment_b17_20261015_v1。复用operator-runtime-v1、python-tool-interface-v3、原生macOS隔离、重试/预算控制及原运行器。历史A/B0/B1/B1.5/B1.6均只读。历史程序一旦用于本轮开发统一重标Development；B1.6隐藏程序不是本轮独立确认。

阶段A在确认前冻结：已标注阳性有效报警至少8例且覆盖率≥80%，16个已标注非M2确认样例最多0次错误警告，工程测试通过；只确认一个版本，确认后不调整检查器。阈值是操作性准入，不是总体误报率的统计上界。阶段B固定6个输入、6个公共检查点、18个续跑单元，不按错误筛选；全轮API保守上限10元，公共生成与三分支分别≤1元，其余6元仅预留给通过门槛的阶段C。

阶段C要求C在不同检查点至少新增2个经源码确认的M2恢复、无误干预，并比G有至少1个净收益；本轮未达到，不生成自然测试结果。完整冻结配置见protocol.json、checker_freeze.json、stage_B_protocol.json。

## RQ1：53次UNKNOWN原因与新检查器

旧检查器的53次调用均能解析，不是53次解析失败。逐次保存原源码、旧检查器内部pattern_names/types/conflicts、代码形态及既有标签：

|旧静态弃判根因|调用数|
|---|---:|
|比较操作数表示无法确定|25|
|未建立直接模式来源|22|
|别名/迭代来源传播丢失|6|

其中仅5次已有严格M2源码/干预标签，40次此前未完成机制审查；其余为其他缺陷、复合缺陷或探索。故不能把53次UNKNOWN统称53次漏检。表中是静态弃判原因，缺少运行可达性与语义影响证据是共同限制，逐项记录见unknown_53_audit.json。

新Checker使用原生隔离中的实际比较探针：识别模式字段来源的二元==/!=；记录实际经过的比较；只有数字/数字字符表示不一致、规范化后相等，且仅改变比较表示使程序返回行为变化，才给VIOLATION。没有违规的已观察支持范围给PASS；无可达比较、原程序失败、探针/反事实未完成或无返回影响给ABSTAIN。输出不给正确答案，也不把临时反事实程序交给Agent作自动修复。原执行器、安全白名单和生产源码均未放宽。

范围限制：未覆盖字典成员/模式索引KeyError、所有跨过程数据流或任意Python语义；PASS仅限被观察的支持比较，不是源码无M2或计数正确证明。诊断包装对常规纯函数样本有语义回归测试，不主张对依赖函数对象表示、反射或任意副作用的程序普遍等价。历史原程序/修复程序本身已完整保留，不静默删除顶层演示语句。

'''
 text+='|历史开发记录标签|VIOLATION|PASS|ABSTAIN|均时秒|\n|---|---:|---:|---:|---:|\n'
 for label,z in dev_stats.items():text+=f"|{label}|{z.get('VIOLATION',0)}|{z.get('PASS',0)}|{z.get('ABSTAIN',0)}|{z['mean_seconds']:.4f}|\n"
 text+='''
这些记录含同输入的修订程序及历史来源重叠，不当作独立Agent复现次数。27个已验证M2开发记录中21个被警告、5个弃判、1个范围内PASS（未覆盖的trie成员检测）；后者不能解释为M2已被排除。3个UNKNOWN记录的2次警告不进入TP统计。额外B0复合程序与B1因果对照单列additional_historical_programs.json，不混进新确认。

确认集是12个缺陷程序输入对、12个对应修复、4个其他错误、4个证据不足样例，生成时不咨询Checker，标签由隔离执行及精确DP/暴力交叉核验。它们来自4种阳性源码模板的参数变体，不是32个独立自然Agent算法。阳性有4个唯一源码哈希；因此只能证明有限契约范围可执行，不能外推普遍检测能力。

|确认结果|Generic G|机制 C|
|---|---:|---:|
|正确警告|12/12|12/12|
|已标注非M2错误警告|0/16|0/16|
|已标注阳性未警告|0/12|0/12|
|未知标签弃判|4/4|4/4|
|总体决断覆盖率|87.5%|87.5%|
|观测警告精度|100%|100%|

54项探针/安全回归、2项续跑预算测试通过。没有通过全部弃判制造零误报；精度和覆盖率单列。各类时延及原始输出见detection_confusion_matrices.json和confirmation_detection.json。

## RQ2：是否具有独立机制价值

G根据公开任务schema中的整数字母表及数字编码模式构造表示契约，不使用历史失败ID、答案或示例常量。C从历史M2归纳后得到同一表示谓词。两者共享执行证据引擎，故这是刻意保留强Generic对照的规范比较，不是两个独立算法实现的竞技。确认输出相同，C没有额外检测谓词；本轮不能把普通类型契约能力称为失败驱动创新，也不评价从失败自动生成检查器的过程。

此结论只适用于本轮C/G，不能否定所有机制特定检查器，也不能声称任意通用类型检查都已解决M2。

## RQ3：同检查点配对续跑

先按冻结规则生成程序，不提供M2提示，不按结果挑选。6个公共首轮程序全部给出正确完整计数；G/C各2次PASS、4次ABSTAIN，均无VIOLATION反馈。4次弃判不能认定源码不存在潜在M2。本批没有经验证的初始M2错误，因此缺陷修复率的分母为0，指标为不可估计，不是0%或100%。不追加生成去寻找失败。

三组复用完全相同的首轮响应、源码和工具结果；实际仅发送剩余调用。共同首轮Token/调用/工具时间仍占原预算，剩余总墙钟由公共已用时间扣除。每条完整轨迹最多4模型调用、8192输出Token、10秒工具墙钟、180秒总墙钟，G/C检查时间和反馈Token也计入。

|分支|最终正确|定向反馈|已验证初始M2|修复率|
|---|---:|---:|---:|---|
|R|6/6|0|0|不可估计|
|G|6/6|0|0|不可估计|
|C|6/6|0|0|不可估计|

C-R、G-R、C-G逐题均为6次双方正确，无单方胜负。所有配对有效，无基础设施中断；主分母仍保留6个单位。共享检查点续跑实现了控制，但未观察到实际错误，因而不能说Reflection已可靠修复M2，也不能说反馈有效。逐题配对、模型上下文、剩余预算、原始请求、完整源码及修改差异分别保存于checkpoints/、branch_results/、stage_B_*/、branch_program_audit.json。

## RQ4：成本、误报和负迁移

本批没有定向反馈，没有观察到新增功能错误或最终回归损失；因缺少实际干预，不能据此证明低误伤部署安全。三分支每条轨迹都用了3次模型调用（含共享首轮）。额外检查有本地执行/墙钟开销，完整资源指标见checkpoint_results.json。

'''
 text+='|方法|计入共享生成的已知在线估算元|每个正确答案已知在线估算元|Checker总秒|\n|---|---:|---:|---:|\n'
 for a,z in costs['per_method_standalone_estimate'].items():text+=f"|{a}|{z['known_cny']:.6f}|{z['known_per_correct_cny']:.6f}|{z['checker_seconds']:.4f}|\n"
 text+=f'''\n实际物理请求总数{costs['physical_requests']}，已知Token估算¥{known:.6f}，保守账本¥{reserved:.6f}，未知用量请求{unknown_requests}；实际账单未知。实际总账只计共享生成一次，表中每种方法独立部署归因包含完整公共生成，不能把表三行相加当真实支出。构建/人工/助手推理费用未定价，不能按零处理，故完全摊销的每个正确答案总成本未知。没有生成研究算子。\n\n'''
 text+='''## RQ5与Go/No-Go

阶段B没有有效修复证据，阶段C未启动。自然发生率、真实自然分布净收益、独立负迁移风险均未验证；6个冻结检查点含定向条件与对照，不用于估计自然M2频率。

- 已证明的范围内事实：旧弃判可以定位；有限范围的运行契约能产生真实有效警告；本轮C/G检测谓词与确认结果相同。
- 探索性证据：历史开发程序检测有非零覆盖但仍有弃判和漏检；小规模新检查点的续跑全部交付正确。
- 未验证：对初始M2错误的Agent因果修复增益、自然部署收益、自动从失败生成检查器的优势。

最终标签：**NO_DISTINCT_MECHANISM_VALUE**。次级状态：因果干预收益未建立，阶段C未运行。**不进入算子生成，不判GO_FOR_GENERALIZATION**。不把“未测到”改写成“证明无效”，不修改确认数据、检查器或触发条件追求正结果。

## 复现

```sh
.venv/bin/python -m unittest tests.test_experiment_b17 tests.test_b17_checkpoint tests.test_engineering_stabilization
.venv/bin/python -m scripts.report_experiment_b17
```

既有API单元不可清空补跑；新研究须使用新Run ID并重新冻结协议。离线确认只消费过一次，原始记录与所有历史来源保留。
'''
 (ROOT/'experiment_b17_report.md').write_text(text)
 atomic_json(ROOT/'final_integrity_audit.json',dict(historical_files=len(snapshot()),history_unchanged=True,checker_and_continuation_sources_unchanged=True,confirmation_consumed_once=True,checkpoint_count=6,branches=18,no_phase_C=True,no_solver_generated=True))
 atomic_json(ROOT/'completion.json',dict(status='complete',research_decision='NO_DISTINCT_MECHANISM_VALUE',stage_A_passed=True,stage_B=18,stage_C='NOT_RUN_GATE_FAILED',known_API_estimate_cny=known,actual_invoice_cny=None))
 print(json.dumps(dict(decision='NO_DISTINCT_MECHANISM_VALUE',branch_summary=summary,cost=costs),ensure_ascii=False))
if __name__=='__main__':main()
