"""Finalize B1.8 offline audit without altering frozen experiment assets."""
import json,statistics
from pathlib import Path
from scripts.experiment_b18 import ROOT,verify,snapshot
from scripts.offline_loop_protocol import atomic_json,file_hash
verify()
r=json.loads((ROOT/'development_replay.json').read_text())
summary=[]
for mid,variant in sorted(set((x['task']['mechanism'],x['variant']) for x in r)):
 a=[x for x in r if (x['task']['mechanism'],x['variant'])==(mid,variant)]
 summary.append(dict(mechanism=mid,variant=variant,executions=len(a),correct_executions=sum(x['correct'] for x in a),unique_inputs=3,median_wall_seconds=statistics.median(x['measured_wall_seconds'] for x in a),median_cpu_seconds=statistics.median(x['execution'].get('cpu_seconds',0) for x in a)))
atomic_json(ROOT/'development_summary.json',summary)
paths=['results/experiment_b0_20261014_v1/diagnostic_trace_audit.json','results/experiment_b1_20261015_v1/new_source_mechanism_audit.json','results/experiment_b1_20261015_v1/capability_gap_candidates.json','results/experiment_b1_20261015_v1/counterfactual_diagnosis.json','results/experiment_b15_20261015_v1/mechanism_grades.json','results/experiment_b16_20261015_v1','results/experiment_b17_20261015_v1/experiment_b17_report.md','results/experiment_b17_20261015_v1/research_decision.json']
evidence=[]
for f in paths:
 p=Path(f)
 evidence.append(dict(path=f,kind='historical_read_only',sha256=file_hash(p) if p.is_file() else None,directory_index='history_before.json' if p.is_dir() else None))
atomic_json(ROOT/'evidence_inventory.json',evidence)
atomic_json(ROOT/'rule_acquisition_cost.json',dict(new_model_requests=0,new_API_cny=0,API_tokens=0,native_executions=len(r),native_wall_sum_seconds=sum(x['measured_wall_seconds'] for x in r),human_and_assistant_analysis_cost='UNPRICED_NOT_ZERO',historical_build_cost='REUSED_NOT_ZERO; historical accounting retained in original runs',rule_synthesis_cost_comparison='NOT_ESTIMATED; no blinded G/F synthesis trial',reference_computation='Offline truth only; exact_dp plus brute_force for n<=7; CPU cost not separately metered',billing_note='User reported earlier A bill 4.10 CNY and earlier calibration 4 CNY; not charged to B1.8'))
atomic_json(ROOT/'selection_information_audit.json',dict(rule_pool_equivalent=True,validated_failure_priority=False,historical_frequency_prior_valid=False,reason='Targeted/uneven source review and repeated inputs cannot estimate natural mechanism rates or unbiased rule utility.',real_resource_observation=dict(per_execution_wall_cap_seconds=3,CPU_cap_seconds=5,max_observed_wall_seconds=max(x['measured_wall_seconds'] for x in r),total_wall_seconds=sum(x['measured_wall_seconds'] for x in r)),formal_G_F_S_budget_comparison='NOT_RUN',generic_ample_budget='No separate 10-second run: all development executions completed under 3s. This is not ample-budget detector coverage.',interpretation='No demonstrated real selection bottleneck in this small replay. Larger rule pools and blinded acquisition/selection remain untested.'))
atomic_json(ROOT/'research_decision.json',dict(status='STOP_CURRENT_LOCAL_FAILURE_CHECKER_ROUTE',stage_A='COMPLETE_NO_DISTINCT_TRANSFERABLE_CONDITION_ESTABLISHED',stage_B='NOT_ENTERED_NO_INCREMENTAL_CANDIDATE',stage_C='NOT_RUN_STAGE_B_GATE_NOT_MET',new_API=False,formal_detection_metrics=None,selection_gain='NOT_ESTABLISHED',causal_repair_gain='NOT_TESTED',scope='Current M1/M2/M3 evidence; not an impossibility result for all failure-guided methods'))
table='\n'.join(f"| {x['mechanism']} | {x['variant']} | {x['correct_executions']}/{x['executions']} | {1000*x['median_wall_seconds']:.2f} |" for x in summary)
report=f'''# Experiment B1.8 中文实验报告

## 结论与阶段决策

本轮完成阶段 A 的离线信息增量审计及历史开发材料的有界反事实回放，**停止当前 M1–M3 的“局部失败生成 Checker”路线，不进入正式阶段 B，也不启动阶段 C 或新模型 API**。接受 B1.7 的 `NO_DISTINCT_MECHANISM_VALUE`，不重新训练 M2 Checker。

真实失败证据提供了具体故障位置、执行见证和最小修复验证；本轮没有发现公共规格之外的新可迁移检查契约，也没有建立其改善预算受限检查选择的独立证据。因此不能声称 Agent 能力提升。这里的停止是证据门槛未满足，并非证明历史失败信息在所有任务上都无价值。

## 研究问题、协议与冻结

RQ1–RQ5 分别考察规则信息、预算选择、独立检测、条件性恢复及正确停止。协议在回放前冻结于 `protocol.json`，包含任务、规则、源代码与稳定运行环境哈希。历史文件先全量记录哈希，结束时逐项复核；本轮只写入新目录及新的 B1.8 脚本。

阶段 A 的进入 B 门槛：至少存在一个可迁移的 F 独有可观察谓词，且 G 无法以相近成本获得；或者有非平凡、可独立检验的失败证据选择假设，并存在真实资源瓶颈。仅有规则来自历史报告，或具体故障行已知，不足以通过。阶段 C 必须先取得独立检测增量，且需另行冻结 API 上限。本轮 API 上限为 0 元。

协议中的 3 秒与 10 秒为可行性预算档；实际只执行每单元 3 秒墙钟、5 秒 CPU 上限的开发回放，**没有额外运行 10 秒正式对照，也没有把两个预算数字解释成选择收益**。未实施正式检测确认，故未设置或报告确认精度阈值；不得以开发结果代替确认门槛。

## 历史证据审查与独立性

| 阶段 | 本轮采用的证据与边界 |
| --- | --- |
| A | 组合精确计数及完整公共规格提供任务背景；原算子能力收益不等于失败信息收益。 |
| B0 | 有源码、反事实诊断；恢复轨迹中历史截断与整数/字符表示存在复合缺陷，不能算作新的纯 M3 独立复现。工程混杂结果不支持创新结论。 |
| B1 | M1 历史来源支持涉及 4 个任务；严格新源码审计确认 1 个 M1 输入。M3 有历史源码与局部修复证据，未形成新的 Agent 独立确认。重复同一程序的参数测试不计为新算法。 |
| B1.5 | 严格 M1 见于 2 个不同输入的程序；二者最终均恢复，不能据初始缺陷宣称 Reflection 无法处理。外部可变缓存上下文及同时缺少数量终止条件等复合错误单列。 |
| B1.6 | M2 有跨输入证据；旧 Checker 53 次 UNKNOWN 不能视为 53 次漏检。基础设施中断使部分方法胜负不可解释为机制收益。 |
| B1.7 | G/C 确认材料均 12/12 正确警告、0/16 非 M2 警告，谓词和引擎相同；32 个语义样例并非 32 个独立自然程序。6 个真实检查点无已确认初始 M2，三个分支均 6/6，无目标因果修复证据。 |

逐文件来源及哈希见 `evidence_inventory.json`、`history_before.json`。原始轨迹继续保存在原目录，本轮不复制后改写其标签或结果；所有用于本轮分析的旧程序一律视为 Development，不再作为独立确认。

## 公共规格与失败规则逐项比较

A 类是公共规格直接给出的契约；B 类是可由规格推导、但实现可能需要复杂分析的检查；C 类是具体程序上的执行及反事实事实。**C 类事实需要运行证据，不代表只有历史失败方法能取得这些证据。**

| 机制 | A：公共契约 | B：可推导条件 | C：具体证据 | G/F 是否新增谓词 |
| --- | --- | --- | --- | --- |
| M1 首元素遗漏 | 首尾不同，长度 1 无解 | 有效状态或等价条件化必须保留影响末端接受的首元素信息 | 哪个状态合并了不同首元素、该路径是否执行、补入信息是否恢复语义 | 未发现。G 的未来接受等价条件与 F 的状态遗漏规则相同 |
| M2 表示不一致 | 整数符号和字符串模式必须按一致表示匹配 | 跟踪实际比较操作数及转换 | 可达的不一致比较及其输出影响 | B1.7 已显示相同；本轮不重建 |
| M3 过早截断 | 所有长度 L 的连续模式均须检查，含重叠 | 显式后缀程序应先形成候选再检查，再保留未来所需信息 | 具体截断位置、数据流及调整顺序/长度的反事实结果 | 未发现。G 的信息充分性与 F 的早截断规则等价 |

不能仅凭不存在 `first` 变量警告 M1：按首元素分次计算、隔离缓存、编码状态或条件化均可能正确。不能仅凭原始缓冲区小于 L 警告 M3：KMP/Aho 等自动机压缩状态可能充分。M2 也不能仅凭 tuple/string 同时出现或答案错误归因。

F 的历史证据可以指出已经审查过的故障行，但给 F 该行、让 G 从任意代码找行的成本差异，在本轮没有盲测计量。不能把人工已完成的定位工作当作零成本独立发现。

## 开发回放：执行语义与真实成本

冻结 2 个 B1 历史原始程序，每个使用原任务、n=7、移除目标约束三类输入，每种源码重复计时 3 次，共 54 次隔离执行。小输入答案以独立暴力枚举交叉验证，其他答案由离线精确参考计算；参考信息仅用于离线评分，没有提供给在线 Checker 或生成器。

M1 的公共依赖修复与历史修复字节完全相同；这是规则实现等价的展示，**不是两个盲生成方法**。M3 的历史修复在检查前保留 3 个符号；公共流式替代先追加并检查，再保留 2 个符号。两者限于这份源码和长度 3 的模式，不是一般完整求解器或可部署检查器。本轮仅保存历史程序副本及局部干预，没有生成新的完整求解器。

| 机制 | 开发源码版本 | 正确执行次数/计时次数 | 墙钟中位数 ms |
| --- | --- | --- | --- |
{table}

原 M1 在历史原题返回 256516864，参考为 187498944；移除首尾约束后正确。n=7 时恰好计数导致答案为 0，原/修复均正确，这个小例不是有效缺陷触发。原 M3 在历史题返回 128184160768461，参考为 36217669501952；n=7 为 1323 对 1084；移除模式约束后正确。两个局部修复版本均恢复对应开发输入结果。

54 次执行全部正常退出，累计墙钟 {sum(x['measured_wall_seconds'] for x in r):.6f} 秒，单次最大 {max(x['measured_wall_seconds'] for x in r):.6f} 秒。使用原稳定化 native sandbox、同一资源限制与失败关闭策略，无运行环境修改。这是程序执行成本，不是自动定位、构建 Checker 或完整规则选择成本；启动开销主导，毫秒差异不能解释为统计显著优势。

分母是 2 个原始算法、6 个输入、3 个源码版本、3 次计时，**不是 54 个独立错误程序**。答案一致不是检测精度。正式混淆矩阵、漏检率、误报率、弃判率及独立程序覆盖均为 NA，而非零。

## 预算受限选择与构建成本

G 与 F 当前候选规则池无已识别的谓词差异；S 应使用事先固定顺序，不能为得到正收益而事后挑选。历史材料来自定向触发、不同深度的源码审查以及重复参数输入，不能用其机制计数构建无偏自然频率先验。因此本轮没有可验证的 F 优先级，也没有运行 G/F/S 正式选择比较。

开发回放中单次执行远低于 3 秒上限，但这不证明任意程序分析都廉价。没有测量大规模规则池、盲规则生成、无故障行提示的定位成本，或真正会触发选择取舍的工作负载；因此“失败信息降低选择成本”仍是未知项，不能宣称已被否定。也不能任意缩小预算或关闭 Generic 的廉价检查来制造差异。

本轮新模型请求 0、API Token 0、新 API 花费 **0 元**。本地资源见 `rule_acquisition_cost.json`。人工及当前助手的审查、规则构造时间未单独计价，标记 UNPRICED_NOT_ZERO；历史反事实和工程构建成本是复用成本，不能记成零，也不能在没有盲生成对照时推断失败证据节省多少 Prompt Token。用户已报的 A 正式账单 4.10 元和此前校准 4 元仍属于历史账目，不并入本轮。

## 分阶段结果与研究判断

- 阶段 A：完成；未建立 F 独有的可迁移检查条件或可验证选择增量候选。
- 阶段 B：`NOT_ENTERED_NO_INCREMENTAL_CANDIDATE`。未创建独立确认集，未运行正式 G/F/S；不得用历史回放代替。
- 阶段 C：`NOT_RUN_STAGE_B_GATE_NOT_MET`。无新 Agent 检查点、无配对恢复准确率、无自然发生率或部署净收益估计。
- 总决策：`STOP_CURRENT_LOCAL_FAILURE_CHECKER_ROUTE`，限定当前 M1–M3 证据；不扩样追求正结果。

RQ1：契约可从公共规格直接给出或推导；历史证据增添具体程序定位与因果见证，尚无新独立谓词。RQ2：相同预算下的优先选择收益未建立，历史定向分布不能代替自然先验。RQ3：未运行独立检测确认，开发修复成功不能替代检测增量。RQ4：阶段门槛未通过，条件性 Agent 修复不启动。RQ5：本轮确实选择不构建新组件、不调用 API，完整保留非正面结果。

支持停止的证据是规则等价、M2 的既有强对照、M1/M3 局部语义替代成功及缺少可信选择先验。反对“失败信息绝无价值”的证据是历史反事实能明确定位当前代码的真实故障，并能指导局部修复。未知项包括盲分析成本、独立程序检测性能、真实瓶颈下的优先级学习、目标缺陷自然频率及最终恢复净收益。

**最终回答：在当前证据范围内，真实失败信息帮助了具体程序的定位与反事实验证；它尚未被证明发现了新的可执行检查条件，也尚未被证明更有效地选择已有检查。可迁移规则主要重复公共任务规格提供的信息，不支持继续开发本轮失败驱动工作流干预算法。**

## 交付与复核

协议 `protocol.json`；规则对照 `mechanism_rule_comparison.json`；来源 `evidence_inventory.json`；开发任务 `development_replay_tasks.json`；原始执行 `development_replay.json`；源码副本 `source_copies/`；局部修复差异 `*.diff`；成本 `rule_acquisition_cost.json`；选择审计 `selection_information_audit.json`；决策 `research_decision.json`；完整性 `final_integrity_audit.json`。均位于本报告目录。
'''
(ROOT/'experiment_b18_report.md').write_text(report)
before=json.loads((ROOT/'history_before.json').read_text());after=snapshot()
atomic_json(ROOT/'final_integrity_audit.json',dict(protected_files=len(before),unchanged=before==after,changed=[p for p in before if before[p]!=after.get(p)],added_historical_files=[p for p in after if p not in before],frozen_protocol_valid=True))
assert before==after
atomic_json(ROOT/'artifact_manifest.json',{str(p):file_hash(p) for p in ROOT.rglob('*') if p.is_file() and p.name!='artifact_manifest.json'})
print(json.dumps(dict(history_files=len(before),history_unchanged=True,executions=len(r),API_cny=0,report=str(ROOT/'experiment_b18_report.md')),ensure_ascii=False))
