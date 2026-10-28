"""S0 finalization. No API requests and no automatic phase advance."""
import json,shutil,time
from pathlib import Path
from collections import Counter
from scripts.experiment_d0 import ROOT,history
from scripts.offline_loop_protocol import atomic_json,file_hash
r=ROOT;before=json.loads((r/'history_before.json').read_text());assert before==history()
gate=json.loads((r/'offline_gate.json').read_text());reg=json.loads((r/'regression_after_reference_fix/offline_gate.json').read_text());integ=json.loads((r/'integration_v2_results.json').read_text());data=json.loads((r/'dataset_audit.json').read_text());conflict=json.loads((r/'data_quality_conflict.json').read_text());meta=json.loads((r/'data/metadata_summary.json').read_text())
logs=[]
for p in r.rglob('*.json'):
 if any(x in p.parts for x in ['execution_logs','integration_logs','integration_v2_logs']):
  x=json.loads(p.read_text())
  if 'result' in x:logs.append(x['result'])
counts=dict(Counter(x['verdict'] for x in logs));wall=sum(x['elapsed_seconds'] for x in logs)
atomic_json(r/'data_admission_revision_v2.json',dict(changes=[dict(id='taco-test-11',previous='METADATA_ADMITTED',current='QUARANTINED_DATA_CONTRACT_CONFLICT',evidence='data_quality_conflict.json')],replacement_tasks=[],original_data_unchanged=True,metadata_counts_after_quarantine=dict(TACO=dict(Low=150,Medium=294,High=287),LCB_v6_increment=dict(Low=43,Medium=52,High=80)),notes='Provisional counts only; not all references executed, SPJ positional provenance imperfect, near-duplicate grouping incomplete.'))
atomic_json(r/'cost_accounting.json',dict(model_API_requests=0,model_API_tokens=0,actual_model_API_cost_cny=0,API_budget_authorized_cny=0,S1_proposed_not_authorized_cny=2,network_downloads='Public dataset/framework files only; download_ledger.json',logged_native_candidate_executions=len(logs),logged_execution_seconds=wall,engineering_effort='UNPRICED_NOT_ZERO',local_compute_monetary_cost=None,physical_retries=0,unknown_model_usage_requests=0,no_paid_provider_calls=True))
atomic_json(r/'research_decision.json',dict(status='S0_NOT_ACCEPTED_DATA_VALIDATION_BLOCKER',engineered_matrix='PASS_60_OF_60_INITIAL_AND_60_OF_60_REGRESSION',real_source_integration='5_OF_6_PASS_AFTER_REFERENCE_PROTOCOL_V2',blocker='TACO fixed task statement/test output contradictions; preserve failed integration and quarantine task',S1='NOT_RUN_NOT_AUTHORIZED_AND_S0_NOT_ACCEPTED',S2_S4='NOT_AUTHORIZED_NOT_RUN',scope='Engineering conclusion only; no semantic failure rate, mechanism value or model capability inference',next='Resolve data admission consistency and real functional/class output/prelude compatibility before requesting S1 budget'))
atomic_json(r/'repair_history.json',dict(adapter_source_modified_after_freeze=False,records=[dict(version='integration-v1',result='4/6',issues=['TACO first reference Python2 division semantics','Assistant independent LCB reference removed X once instead of all occurrences']),dict(version='integration-v2',diff='integration_reference_repair.diff',result='5/6',changes=['Same TACO task, max5 original references, full-test check; no task replacement','Correct independent LCB enumeration; judge/runtime unchanged'],remaining='TACO tests inconsistent with stated task, independently enumerated; gate remains closed')],full_regression='regression_after_reference_fix/offline_results.json'))
# Field-level mock checks are not a model API smoke.
atomic_json(r/'S1_status.json',dict(status='NOT_RUN',reason=['User attachment explicitly withholds actual API authorization','S0 end-to-end gate not accepted'],proposed_budget_cny=2,approved_budget_cny=0,proposed_task_ids='smoke_task_ids.json',task_roles='ENGINEERING_DEVELOPMENT_ONLY',model_returned_name=None,tokens=0,first_submission_compliance=None,final_executable_rate=None,actual_model_cost=0))
results_table='\n'.join(f"| {x['name']} | {'通过' if x['passed'] else '未通过'} |" for x in integ)
paths_table='\n'.join(f"| {k} | `{v['sha']}` |" for k,v in meta.items())
case_names=[x['name'] for x in json.loads((r/'offline_cases.json').read_text())]
case_table='\n'.join(f"| {i+1} | {n} | 2/2 | 2/2 |" for i,n in enumerate(case_names))
report=f'''# D0 工程适配与 API 冒烟验收报告

日期：2026-10-19；Run ID：experiment_d0_20261019_s0_v1。

## 执行范围与结论

本次执行 **S0 工程适配、离线验收和真实数据核验**。未启动模型 API 冒烟 S1，也未运行 S2–S4。附件第十六节明确指出：2 元是建议，不是实际支出授权。本轮模型调用 0、Token 0、API 花费 0 元。

最终判定：**S0_NOT_ACCEPTED_DATA_VALIDATION_BLOCKER**。30 项工程矩阵初次两轮 60/60 通过，参考协议修正后的完整回归亦 60/60 通过；但真实数据集成验收第二版为 **5/6**，固定 TACO 任务存在题意与测试答案矛盾，不能宣称 S0 整体通过。

这不是 Agent 算法失败，也不是失败驱动方法的研究结论。没有为得到通过结果而替换任务、修改原数据答案或追加新的参考解搜索。S1 未授权且 S0 总门槛未满足，必须保持关闭。

## 独立适配器与隔离设计

新增 scripts/d0_programming_runtime，版本 d0-programming-v1；历史 A–C0 接口与冻结文件未修改。

支持标准输入输出程序、函数、类方法、负整数、字符串、列表及 JSON 可序列化返回；精确类型比较、按 token 比较、显式浮点容差和显式忽略大小写比较分别实现。多用例逐个在新进程执行，失败即保留反例；未完成的用例不能被记为通过。评测器总体时间耗尽与候选单用例超时分开。

安全边界以原生 macOS sandbox 为基础：限制可读目录、禁止写入、网络和派生进程；进程 CPU、墙钟、输出和递归受限。macOS 内存沿用 RSS 采样监测，不宣称瞬时硬内存上限。Python 语言配置另限制动态执行、反射、私有属性和不支持的依赖，不能把它代替操作系统隔离。原生隔离不可用时失败关闭。

当前标准库配置为 sys 受控接口，以及 json/math/collections/itertools/functools/heapq/bisect/string/re/statistics/fractions/decimal/operator/array/typing/copy。sys 仅暴露受控 stdin/stdout/stderr、exit、maxsize、version 与受限递归设置。普通类方法允许；这是受控编程任务子集，不是全部 Python 特性的兼容性证明。

操作系统探针绕开候选语言限制，直接在原生沙箱测试读取 /etc/passwd、文件写入、socket 连接和 fork；均被系统拒绝。程序捕获文件访问或递归违规后再输出正确答案，也仍被判 POLICY_VIOLATION。有限验收不能替代全面恶意 Python 沙箱安全审计。

## 三种环境和答案边界

self_test、hidden、diagnostic 独立记录角色与日志。子进程载荷仅包含源码、接口、输入、参数及资源限制；expected 和参考实现不发送到子进程。期望值由父进程判题。

EngineeringSession 是 S0 模拟能力边界，不是已运行的真实 Agent：公开任务仅允许 id/source/prompt/interface/public_examples；自测只执行模型自给输入，不返回隐藏 verdict；自测耗时计入 10 秒。提交后锁定源码、关闭自测及后续模型上下文；隐藏评测独立计时。模拟验证了这些转换和拒绝。

尚未验证真实模型工具调用、最终交付率、模型返回版本及 Token。不能以 Mock 结果冒充 API 冒烟。

## 数据来源、冻结版本与实际范围

| 数据/框架 | 实际 revision / commit |
| --- | --- |
{paths_table}

TACO 下载了冻结 test split 的单个 parquet，1000 题，245,784,461 字节；完整 SHA256 为 `5d99adc603500c05751aff9f61bed7bbd54a9ad5ea569d108b645346655aae44`，与 HF LFS 元数据一致。下载内容、大小、哈希和耗时见 data/download_ledger.json。

LiveCodeBench 下载了同一数据 revision 的 test6.jsonl，134,303,240 字节、175 题。**这对应 v6 增量，不是完整 release_v6。** 已保存原始加载脚本核对：release_v6 应合并 test.jsonl 至 test6.jsonl 共六个文件；未下载其余分区，不称为完整 1055 题已核验。

Dataset A 使用原公共语义的新工程任务池，种子261019001，六道 n=5/6 输入全部由已有精确 DP 与独立穷举交叉验证。按2/4/7种约束分层；没有声称修改了数学语义或已经建立新的正式180题池。

## 候选数量、特殊判题与去重

| 来源 | Low | Medium | High | 含义 |
| --- | ---: | ---: | ---: | --- |
| TACO 原始 | 200 | 400 | 400 | EASY / MEDIUM+MEDIUM_HARD / HARD+VERY_HARD |
| TACO 元数据初筛 | 151 | 294 | 287 | 排除 SPJ 和图像依赖；尚非全部可信参考已验证 |
| TACO 本轮隔离任务后 | 150 | 294 | 287 | 额外隔离 taco-test-11 |
| LCB v6 增量原始/初筛 | 43 | 52 | 80 | 元数据级，不是已验收180题 |

TACO official SPJ 文件的1000个索引覆盖1000个 test 行，初筛排除218个 special_judge=True 标记及68个图像依赖标记，二者有重叠。虽然支持通用浮点和大小写比较，仍保守排除全部需要官方特殊判题映射的任务，未用宽松比较替代未知非唯一答案判题器。官方 SPJ 文件没有携带数据文件内容哈希，位置对应存在残余版本不确定性，必须在正式阶段进一步核对。

LiveCodeBench 的压缩私有测试采用限制反序列化解析：禁止 pickle class 与 persistent ID，设置解压上限；没有直接运行远端 dataset 脚本或普通 pickle.loads。已保存官方 Errata 并按明确题号排查。

目前只完成可获得原 URL 的精确匹配，未发现匹配项；**不能解释为三个来源完全无重复**。部分 Codeforces/LeetCode URL 尚未重建，题面近似及任务家族分组未完成，Development/Confirmation 尚未划分。

正式540题未就绪：LCB当前增量的 Low/Medium 原始数量已不足每格60题；必须另行扩充至明确冻结的完整 release 并重做准入、去重和数量检查。

## 30 项离线矩阵

预先冻结30项、每项两轮。初次60/60；参考验证协议修正后，在新目录完整回归60/60。没有覆盖旧结果。

| # | 案例 | 初次 | 完整回归 |
| --- | --- | --- | --- |
{case_table}

其中核验了正确/错误程序、输入输出协议、输出类型、容差、异常、超时、内存、输出限制、正确输出伴随违规、隐藏边界和原生失败关闭。内核探针及角色边界测试还包含子检查，不能把“60个工程验收单元”解释为60道研究任务。

## 真实数据集成与修复历史

| 第二版集成检查 | 结果 |
| --- | --- |
{results_table}

第一版4/6：TACO固定任务首份官方解在Python3执行时因Python2整数除法语义异常；我写的LCB参考 `2025-X` 错误地只排除一次X。这不是评分器发现模型能力问题，而是参考程序适用性和研究者参考错误。

修复协议v2保持同一任务，不改候选执行器、不改判题器、不改原始期望。TACO最多检查原顺序前5份参考，先3例，再对通过者检查全部87例；不在失败后继续追加到其余参考以追求通过。LCB参考改为枚举81个乘法表格子，排除所有值等于X的格子；同一任务全部40个私有用例通过。

TACO前5份：第0份Python3运行异常；第1/2/4份使用不支持的动态eval输入而被策略拒绝；第3份通过前三例、在第4个测试输入与数据期望不一致。随后通过独立穷举检查题意，而非直接把它认定为参考代码错误。

## TACO 数据契约冲突证据

固定任务 taco-test-11 要求将字符串切成4个两两不同的非空连续部分。原始测试索引3包含100个子案例，小规模完整枚举在其中确认 **{len(conflict['counterexamples'])} 个题意/期望矛盾**。

- `oeoo` 长度4，只能切成 `o/e/o/o`，答案应NO，数据为YES。
- `mqhqf` 可切成 `m/q/h/qf`，四段不同，答案应YES，数据为NO。
- `ivi` 长度3，不可能切成4个非空部分，答案应NO，数据为YES。

完整反例、原始输入哈希和数据哈希见 data_quality_conflict.json。仅确认本题测试/题意对齐存在冲突，未断言全部TACO错误，也未自行改写整套答案。该任务隔离，未替换成新任务凑集成通过；S0门槛因此保持关闭。

## 18题 S1 候选与未测量项

smoke_task_ids.json 已根据固定种子与元数据选出每来源×每档2题，共18题，全部为工程开发材料，不是独立确认。它们只是候选清单，不代表所有任务已通过参考和接口验收；S1预算亦未授权。

S1的逐题程序、首次提交合规率、最终可执行率、模型名称、Token、真实计费全部为 **NOT_RUN/NA**，不能写成0%或虚构评分。API调用、Token及费用为0。

另有功能/类接口真实数据兼容性未完成：LCB starter 中的 List 等注解及官方预导入需要明确映射；TACO函数输出有单元素包装兼容规则，不能盲目用严格JSON相等或无条件剥层。工程函数/类样例已通过，但这些数据集级转换仍须独立冻结和验收，不能据此放行真实API。

## 成本、日志与完整性

本轮模型API实际花费 **0元**，物理模型请求0，未知用量0。数据下载是公开文件HTTP请求，不是模型API。

保存的候选进程执行日志 {len(logs)} 份，累计记录墙钟 {wall:.3f} 秒；还存在内核探针、元数据/穷举、下载和审查时间，不将该数字当作全部工程耗时。人工及当前助手工程工作未货币化，UNPRICED_NOT_ZERO。费用账本 cost_accounting.json；API停止状态 S1_status.json。

历史受保护文件 {len(before)} 个，结束时内容哈希全部一致。实现、协议、数据、参考失败、修复差异与回归结果均保留。未修改 A–C0，不修改正式结果分母，不把工程故障计入 Agent 失败率。

## 下一步门槛与正式实验配置

当前**不允许进入完整540题实验，也不应立即请求运行S1**。先完成：

1. TACO数据契约冲突的准入/隔离规则与可信参考检查；对源文件和SPJ映射进行版本一致性核验。
2. TACO函数输出包装及LCB注解/预导入的任务适配，新增正确参考回归；不能靠扩大文件或网络权限解决。
3. 对18个候选任务完成所需接口与判题准入，明确特殊判题排除；不根据模型结果换题。
4. S0集成全通过后，再申请S1实际API上限2元；当前没有授权。先9题、必要时完成18题，全部保持工程开发角色。
5. S1后根据真实累计输入、输出、缓存和峰谷计费重估S2；不能沿用历史4.1元账单比例。模型别名会路由升级，需要记录实际返回标识和运行日期。

540题整体API费用仍只能沿用上一轮条件性规划约20–60元、100元分阶段预留，**不是本轮实测或授权**；最多120个概率抽样审查单元和30个探索单元的诊断开销应另按调用/Token上限冻结。当前不能提供540题的实际预算或承诺每格60个合格题。

推荐保留4次模型调用、8192累计输出Token、10秒累计自测作为待S1核验的起始配置；最终隐藏评测必须按来源、测试数和参考运行时间另定，不能照搬单题10秒。当前默认2秒/例、30秒/任务只是工程配置，不是竞赛标准时限，尚不适合直接作正式TLE判定。

本轮发现说明先做工程冒烟是必要的：如果直接跑540题，参考版本问题和数据矛盾可能被错误解释为模型语义失败。没有证据支持本轮任何 Failure-driven Operator 能力判断。

## 复现与交付

主脚本 scripts/experiment_d0.py；独立运行器 scripts/d0_programming_runtime；数据核验 scripts/d0_dataset_audit.py；初版与第二版集成 scripts/d0_integration_acceptance.py / d0_integration_acceptance_v2.py；源码副本 frozen_code/。

协议 protocol.json / integration_protocol.json / integration_v2_protocol.json；30例与执行 offline_cases.json / offline_results.json；完整回归 regression_after_reference_fix/；真实集成 integration_results.json / integration_v2_results.json；修复 integration_reference_repair.diff / repair_history.json；数据版本 data/metadata_summary.json / data/download_ledger.json；原始数据及下载源码 data/；任务清单 dataset_inventory.json / smoke_task_ids.json；数据冲突 data_quality_conflict.json；决策 research_decision.json；成本 cost_accounting.json；完整性 final_integrity_audit.json；交付哈希 artifact_manifest.json。
'''
(r/'D0_工程适配与API冒烟验收报告.md').write_text(report)
# Archive implementations without touching historical source trees.
frozen=r/'frozen_code/final';frozen.mkdir(parents=True,exist_ok=True)
for p in list(Path('scripts/d0_programming_runtime').glob('*.py'))+[Path('scripts/experiment_d0.py'),Path('scripts/d0_dataset_audit.py'),Path('scripts/d0_integration_acceptance.py'),Path('scripts/d0_integration_acceptance_v2.py'),Path(__file__)]:
 dest=frozen/p.name;shutil.copyfile(p,dest)
for f in ['d0_fetch_metadata.py','d0_download.py']:
 p=Path('/tmp')/f
 if p.exists():shutil.copyfile(p,frozen/f)
atomic_json(r/'final_integrity_audit.json',dict(protected_files=len(before),unchanged=before==history(),changed=[],S0_accepted=False,API_called=False))
atomic_json(r/'artifact_manifest.json',{str(p):file_hash(p) for p in r.rglob('*') if p.is_file() and '__pycache__' not in str(p) and p.name!='artifact_manifest.json'})
print(json.dumps(dict(protected_files=len(before),S0='NOT_ACCEPTED',API_cny=0,report=str(r/'D0_工程适配与API冒烟验收报告.md')),ensure_ascii=False))
