"""Final S0.1 report, frozen S1 proposal and read-only history audit."""
import csv,hashlib,json,shutil,time
from pathlib import Path
from collections import Counter,defaultdict
from scripts.experiment_d0_s01 import ROOT,OLD,history,verify
from scripts.d0_s01_runtime.runner import public_task
from scripts.compositional_counting import SPEC
from scripts.offline_loop_protocol import atomic_json,file_hash

def main():
 verify();gate=json.loads((ROOT/'acceptance_gate.json').read_text());entries=json.loads((ROOT/'candidate_admission_final.json').read_text());pool=json.loads((ROOT/'full_pool_audit.json').read_text());reg=json.loads((ROOT/'regression_gate.json').read_text());fixed=json.loads((ROOT/'taco11_final_audit.json').read_text());small=json.loads((ROOT/'reference_small_results.json').read_text());critical=json.loads((ROOT/'critical_integration_results.json').read_text());tasks=json.loads((ROOT/'normalized_candidate_tasks.json').read_text());A={x['id']:x for x in json.loads((OLD/'dataset_A_smoke.json').read_text())};accepted=[x for x in entries if x['status']=='ADMITTED'];excluded=[x for x in entries if x['status']!='ADMITTED']
 # Complete model-visible specifications without introducing hidden answers or references.
 public={};backend={};runtime_contract=dict(runtime='d0-programming-v2-s01',source_interface_modes=['stdin','function','class'],source='Unmodified candidate source; framework context only',lcb_context='List Optional Tuple Dict Set Deque DefaultDict Any Iterable; Counter defaultdict deque; cache lru_cache reduce; heap helpers; bisect helpers; inf gcd accumulate',supported_imports=['sys(restricted)','json','math','collections','itertools','functools','heapq','bisect','string','re','statistics','fractions','decimal','operator','array','typing','copy'],prohibited='No network/file writes/fork, introspection, eval/exec or private attribute access; no unsupported dependency',online_selftest=dict(total_tool_seconds=10,per_call_wall_seconds=2),hidden=dict(per_case_wall_seconds=6,cpu_seconds=5,task_wall_seconds=75,memory_bytes=256*1024**2,output_bytes=2*1024**2),special_judges='Only explicit exact/tokens/float/casefold and declared TACO singleton int-pair schema; nonunique outputs excluded')
 for e in accepted:
  t=tasks[e['id']].copy()
  if e['id'] in A:t['prompt']=SPEC+'\nPublic task instance:\n'+json.dumps(A[e['id']]['problem'],sort_keys=True)
  t['prompt']+='\nExecution interface and allowed environment:\n'+json.dumps(runtime_contract,sort_keys=True)+'\nTask interface:\n'+json.dumps(t['interface'],sort_keys=True)
  t['judge']=tasks[e['id']]['judge'];backend[e['id']]=t;public[e['id']]=public_task(t)
  assert set(public[e['id']])<=set(['id','source','prompt','interface','public_examples'])
 # Fixed proposed order: available strata first, repeat second; no model outcome exists.
 source_order={s:i for i,s in enumerate(['Dataset A v2 task pool','TACO','LiveCodeBench-v6-increment'])};level_order={'Low':0,'Medium':1,'High':2};groups=defaultdict(list)
 for e in accepted:groups[(e['source'],e['difficulty'])].append(e)
 ordered=[]
 for (source,level),rr in groups.items():
  for i,e in enumerate(sorted(rr,key=lambda x:x['id'])):ordered.append((i,level_order[level],source_order[source],e['id']))
 order=[x[-1] for x in sorted(ordered)]
 atomic_json(ROOT/'s1_public_tasks.json',public);atomic_json(ROOT/'s1_backend_tasks.json',backend);atomic_json(ROOT/'s1_runtime_contract.json',runtime_contract);atomic_json(ROOT/'s1_proposed_order.json',dict(status='PROPOSAL_ONLY_NO_AUTHORIZATION',order=order,first9=order[:9],all13=order,balanced_original9cells=False,excluded_ids=[x['id'] for x in excluded],reserves_executed=False))
 prices=dict(source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',checked_date='2026-10-19',model_name='deepseek-flash',documented_version='DeepSeek-V4.1-Flash-1017',reported_model_this_run=None,currency='CNY',per_million_tokens=dict(cache_miss_input_offpeak=1,cache_miss_input_peak=2,output_offpeak=4,output_peak=8,cache_hit_input_offpeak=.02,cache_hit_input_peak=.04),not_API_measurement=True)
 atomic_json(ROOT/'pricing_snapshot.json',prices)
 budgets=[]
 for n in [9,13,18]:
  budgets.append(dict(tasks=n,eligibility='ADMITTED_SUBSET_AVAILABLE' if n<=13 else 'NOT_CURRENTLY_ADMITTED; needs new reserve protocol and validation',normal_assumption=dict(cumulative_input_per_task=12000,cumulative_output_per_task=4000),normal_cny=[n*(12000+4*4000)/1e6,n*(2*12000+8*4000)/1e6],peak_no_cache_cap_cny=n*(2*20000+8*8192)/1e6))
 atomic_json(ROOT/'S1_budget_estimate.json',dict(actual_API_requests=0,actual_API_tokens=0,actual_API_cost_cny=0,authorized_cny=0,proposed_ceiling_cny=2,pricing=prices,scenario=budgets,proposed_limits=dict(logical_calls_per_task=4,cumulative_input_tokens_per_task=20000,cumulative_output_tokens_per_task=8192,online_tool_seconds=10,wall_seconds=180),retry_policy='Propose zero automatic retries for first smoke; reserve unknown usage conservatively and stop before request when ledger would exceed2. Requires explicit authorization.',public_prompt_chars={tid:len(json.dumps(p,ensure_ascii=False)) for tid,p in public.items()},note='Input includes repeated context across requests; token lengths not measured by model. No cache discount assumed. Invoice and returned model remain unmeasured.'))
 logs=[]
 for p in ROOT.rglob('*.json'):
  if any(part.endswith('_logs') for part in p.parts):
   d=json.loads(p.read_text())
   if 'result' in d:logs.append(d['result'])
 atomic_json(ROOT/'cost_accounting.json',dict(model_API_requests=0,model_API_tokens=0,model_API_cost_cny=0,authorized_API_cny=0,automatic_stage_advance=False,logged_process_executions=len(logs),logged_process_wall_seconds=sum(x['elapsed_seconds'] for x in logs),status_counts=dict(Counter(x['verdict'] for x in logs)),public_data_download_bytes=sum(json.loads(s)['bytes'] for s in open(ROOT/'data/download_ledger.jsonl')),engineering_and_assistant_effort='UNPRICED_NOT_ZERO',local_compute_money=None,not_Agent_failure_counts=True))
 before=json.loads((ROOT/'history_before.json').read_text());assert history()==before
 atomic_json(ROOT/'research_decision.json',dict(status=gate['status'],accepted_scope=dict(candidate_ids=[x['id'] for x in accepted],count=13,data_sources=3),excluded_original_candidates=[x['id'] for x in excluded],S1='NOT_RUN_NOT_AUTHORIZED',S2_S4='NOT_RUN_NOT_AUTHORIZED',formal_540_ready=False,answer='Safe to propose 9–13 admitted tasks under explicit new S1 authorization and frozen subset/configuration; not original balanced18 or full540.',failure_driven_method_evaluation='NOT_PERFORMED',original_results_preserved=True))
 rowlist=[]
 for e in entries:rowlist.append(dict(id=e['id'],source=e['source'],difficulty=e['difficulty'],status=e['status'],test_cases=e['test_cases'],invalid_input_cases=len(e['contract_review']['invalid_input_cases']),reasons='; '.join(e['reasons'])))
 with (ROOT/'candidate_admission.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=list(rowlist[0]));w.writeheader();w.writerows(rowlist)
 candidate_table='\n'.join(f"| {e['id']} | {e['source']} / {e['difficulty']} | {e['test_cases']} | {'准入' if e['status']=='ADMITTED' else '隔离'} | {len(e['contract_review']['invalid_input_cases'])} |" for e in entries)
 pool_table='\n'.join(f"| {source} / {level} | {x['raw']} | {x['metadata_admitted']} | {x['unique_groups']} |" for source,levels in pool['counts'].items() for level,x in levels.items())
 critical_table='\n'.join(f"| {x['name']} | {'通过' if x['passed'] else '未通过'} |" for x in critical)
 report=f'''# D0-S0.1 补充工程验收报告

日期：2026-10-19。Run ID：experiment_d0_s01_20261019_v1。

## 总结与验收范围

结论：**{gate['status']}，限于新版本适配器与已准入的13道原候选。** 完整工程回归与新增用例88/88通过，六份LCB离线参考的独立小规模核验235/235通过，关键集成13/13通过。可以在另行明确授权后，对已准入子集开展9–13道真实API工程冒烟；不能直接运行原九格均衡18题，也不能运行540题。

原18候选中13道准入、5道隔离。最初16道参考通过不是最终准入结论：输入契约审查又排除了3道“参考能通过但测试输入不合法”的候选。全部中间结果保留，未改原始期望、未更换模型、未运行备用题来填满18题。

本轮实际模型API请求0、Token0、费用0元。S1/S2/S3/S4均未启动。没有评价失败驱动机制优劣，也没有把工程错误计为Agent语义失败。

## taco-test-11 的最终审查

冻结TACO revision：`d593ed0a2becbbc952230bb89be09189bf1056dc`，test split。原始parquet SHA256：`5d99adc603500c05751aff9f61bed7bbd54a9ad5ea569d108b645346655aae44`。

直接从原始parquet第11行重读题面、input_output与URL，和旧解析记录逐字段一致。测试索引3确有100个子案例；输入第一行100，后100个字符串逐行对应输出100个Token。不是列表包装剥除、空行偏移或索引错配。

全部13个旧矛盾经独立枚举三处分割点再次复现。例如oeoo长度4，只能切成o/e/o/o，四段不互异，题意应NO、原期望YES；ivi长度3不能切4个非空部分，原期望仍YES。原始输出没有改写。

处理状态：**DATA_CONTRACT_UNRESOLVED**，本题在当前数据版本下隔离。已定位到题面与测试契约不一致，但不声称解释了上游错误的生成过程；不推断整个TACO都不可靠。证据见taco11_final_audit.json。

首份历史参考在Python3中以浮点数作为切片索引，是Python2语义依赖；动态eval输入参考亦不符合现有公开执行契约。这些属于UNSUPPORTED_REFERENCE，与独立证明的数据契约问题分开。没有为参考放开eval、网络、文件权限或Python2解释器。

## 接口兼容修复

新包scripts/d0_s01_runtime，版本d0-programming-v2-s01；旧scripts/d0_programming_runtime及S0全部记录只读。

TACO：只对明确的find_mult_3公共返回契约使用单返回包装。题面返回两个整数[数量,最大值]，数据外层是单元素返回信封[[数量,最大值]]；父进程按声明精确解释一层期望，实际返回仍必须恰为两个整数。[[4,63]]作为候选实际返回会被拒绝，True不能代替整数1；普通exact比较保留原列表层级。无无条件剥层、无“任何一边相等就算对”的宽松兜底。

LCB：按官方框架源码确认预导入上下文的存在，提供版本化lcb-safe-v1任务上下文，涵盖List/Optional/Dict等注解、Counter/defaultdict/deque、cache/lru_cache、堆与二分等所需安全别名。stdin、逐行JSON函数参数与Solution类方法分别转换。只注入框架上下文，不修改候选算法源码；执行日志保留原源码哈希。

该配置是官方预导入的受控安全子集，不是全部Python和全部官方星号导出的无条件镜像。未开放原来禁止的动态执行、私有反射或系统模块。真实候选所需的List类方法、字符串和列表返回均已通过完整测试。差异见worker.py.diff、runner.py.diff、evaluation.py.diff，完整配置见s1_runtime_contract.json。

语法错误、接口缺失、普通运行异常、资源失败、评分器错误与基础设施失败分别保留。RUNTIME_ERROR本身不能推出“不支持的语言”或“数据有误”：初始批量标签中将运行异常归为UNSUPPORTED_REFERENCE的记录，在candidate_admission_final.json追加REFERENCE_RUNTIME_UNVERIFIED复核，没有覆盖原记录。

## 18道原候选逐项准入

准入同时要求：接口与隐藏测试可加载、判题明确、输入满足公共约束、正确参考或独立可信证据通过全部现有测试、错误工程程序被检出、资源与隔离验证通过。正确参考通过是必要证据，不能代替输入契约核查。

| ID | 来源 / 难度 | 测试输入数 | 最终状态 | 不合法输入数 |
| --- | --- | ---: | --- | ---: |
{candidate_table}

五道隔离原因：

- taco-test-40：测试157输入num=0，与题面num>0冲突。参考1虽通过775例，仍隔离整个任务；两份枚举参考的内存失败另记资源验证不足。
- taco-test-224：26个输入违反数值/模数约束，例如模数101含不小于50的素因子；预先固定参考还有不支持依赖、超时和测试分歧。未经独立证明的输出分歧不自动记为数据答案错误。
- taco-test-715：67个输入不符合公共约束，如n=8,k=7却含8、9，另有缺少k等结构问题。IndexError/ValueError不计作Agent算法失败。
- taco-test-110：37个输入违反声明范围或批量结构；即使参考0通过248例，也不得准入。
- taco-test-237：41个输入不满足正数、集合长度或元素条件，如b数组含0、集合声明大小与实际数量不一致。参考0通过164例不能消除契约冲突。

所有原始数据和评分保留；没有删除有问题的用例后把任务重新记为通过。taco-test-11不在这18道之内，单独继续隔离。

TACO原参考只使用运行前固定的索引0/1/2，三份均按计划检查，不在失败后追加第4份或第5份。LCB六份工程参考在隐藏验证前冻结，并先以独立穷举或不同数学计算验证235个小例；之后通过全部现有私有测试。它们仅作离线评测证据，不提供给Agent、不注册为新Operator。A的六题用原DP与全序列穷举相互核验，并用原枚举参考源码在隔离环境执行。

最终准入：A6题、TACO1题(taco-test-67)、LCB6题。reserve_order.json记录了事先冻结的确定性备用顺序，本轮未运行备用任务。若要恢复每格2题的18题布局，需要新协议按备用顺序验收，不能直接使用当前被隔离题目。

## 完整LiveCodeBench版本与去重准备

LCB数据revision：`0fe84c3912ea0c4d4a78037083943e8f0c4dd505`；官方框架commit：`28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24`。TACO框架commit：`245eba3beb2d23a07082307de303de2589e4321a`。

完整release_v6六个分区逐个下载并核对HF LFS SHA256：400+111+101+101+167+175=1055题；不再把175题增量称作完整release。原始下载约4.486GB，逐文件哈希、字节和时间见data/download_ledger.jsonl。没有运行远程dataset加载脚本；压缩测试使用禁止class/persistent ID的受限反序列化及解压上限。

| 来源 / 难度 | 原始数 | 元数据初筛数 | 初筛内去重组数 |
| --- | ---: | ---: | ---: |
{pool_table}

LCB有84题在当前受控解析/大小/数据结构配置下无法验收私有测试，12题命中官方Errata，二者有重叠；均保留原因，不放宽反序列化权限强行准入。TACO继续排除官方SPJ及图像依赖，并加入本轮已确认契约隔离任务。官方SPJ清单按1000行位置核对，但没有与数据文件绑定的官方内容哈希，正式大规模阶段仍需关注这一残余风险。

去重已对1000道TACO和1055道LCB完成：重建Codeforces/AtCoder/LeetCode原题标识，合并原URL和规范化题面完全相同项；题名与五词shingle相似度产生候选，相似度>=0.5保守同组、>=0.75记强近似。固定算法比较了{pool['near_duplicate_candidates']}个候选对，记录{pool['dedup_edges']}条关系；当前规则下跨来源匹配组{pool['cross_source_group_count']}个。

**未检出跨源组不等于证明没有重复。** 翻译、深度改写和算法任务家族仍需人工语义复核；算法标签已保留，不把所有DP题粗暴并为一个“重复任务”。尚未划分Development/Confirmation；未来必须按重复组及待定家族保守分组。去重和数量准备已完成到明确的自动化范围，不宣称540道已全部可信评分。

## 完整回归与关键集成

旧30项×2加新增14项×2，共88/88。覆盖List/Optional/Dict注解、预导入、严格包装正反例、布尔类型差异、接口缺失、语法错误，以及旧原生系统隔离、超时、内存、输出、特殊判题和隐藏边界。

六份LCB工程参考的235个独立小例全部通过。方法分别使用二项式系数核验数字迭代、乘法表闭式计数、逐次字符串替换、子序列完全枚举、短串26字母完全枚举LCS、删除后组合枚举公共前缀；不靠单独几个样例认定参考可信。现有全部私有测试进一步验证了这些参考，但仍不声称数学上穷尽所有合法输入。

| 关键集成门槛 | 结果 |
| --- | --- |
{critical_table}

评分器配置异常返回JUDGE_ERROR，不伪装成Wrong Answer；父评测总预算不足返回EVALUATOR_BUDGET_EXHAUSTED，不直接算候选算法TLE。操作系统隔离不可用返回INFRASTRUCTURE_ERROR并失败关闭。运行时异常和拒绝只说明现象，根因留给后续有界审查。

文件、网络、fork探针与“违规后输出正确答案”均保持拒绝。macOS内存仍采用RSS采样监测，不能表述为瞬时内核硬限制；有限工程验收也不是任意恶意Python程序安全证明。

## 答案访问与Agent边界

子进程载荷不含expected或参考实现；父进程判题。公开任务按字段白名单构造；TACO原题和LCB starter/公共例子可见，私有测试和诊断源码不可见。self_test仅接受模型自给输入，忽略请求里伪造的expected/role；不会返回隐藏判题结论。提交后锁定源码、关闭模型续跑及工具调用；隐藏评测不消耗10秒在线工具额度。

s1_public_tasks.json是可供未来模型使用的公开层，s1_backend_tasks.json是父评测层，trusted_engineering_references.json只供研究者核验，均不得混入模型请求。A任务公开层补齐完整原始SPEC而非只给实例JSON；此补齐在API之前完成，没有修改已评分程序或答案。

## S1预算估算与建议范围

本轮真实API花费0，仍无S1授权。建议下一步先从已准入13题的冻结顺序取9题验链路，再决定是否完成其余4题；这不是原九格均衡9题。原18题中5题隔离，18题方案暂不可执行。

2026-10-19核对[DeepSeek官方人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：Flash缓存未命中输入空闲1元/百万、高峰2元/百万；输出空闲4元/百万、高峰8元/百万。不假设缓存折扣。旧别名由当前Flash版本提供服务，必须在S1保存实际返回标识；本轮没有返回模型名称可记录。

预算包含所有轮次累计输入，不能把单个题面Token当作全部输入。假设每题累计输入12000、输出4000，则9题约0.252–0.504元，13题约0.364–0.728元。若冻结每题累计输入上限20000、输出8192，高峰无缓存上界：9题0.950元、13题1.372元；建议S1总上限仍2元，为未知用量预留余量。18题同口径约1.900元但当前并无18道合格任务，且余量太小，不推荐直接放行。

建议四次逻辑模型调用、最后一轮预留提交、180秒在线墙钟、10秒累计自测；首次冒烟不自动重试，未知用量按保守额度记账，请求前检查硬上限。输入或剩余预算不足就明确截断并保留分母；2元是预算建议，不是已批准支出。真实Tokenizer、生成长度、请求失败和服务商账单仍要在S1实测，估计不保证全部任务完成。

隐藏评分当前按6秒/例、CPU5秒、256MiB RSS监测、2MiB输出、75秒/任务验参考；准入13题的完整参考实际执行均在该范围内。它们是工程时限，不能直接作为540题全部题型的竞赛标准限制。

## Go/No-Go与停止位置

**S0_ACCEPTED：可以在新S1协议明确采用已准入子集、冻结配置并另获预算授权后，安全进入9–13题真实API工程冒烟。** 不承诺全部18题可用，不恢复原九格均衡布局，不放行540题。

正式540题仍需完成全量候选参考与契约质量控制、特殊判题版本核验、语义家族复核和按组划分。现有元数据数量三档均超过60，不等于已取得每格60个可信独立任务。

本轮结束立即停止。没有模型API、没有新的失败驱动方法比较、没有Operator生成、没有S1–S4。下一轮API冒烟仍只能回答工程可靠性与真实成本，不以最低失败数量或正确率作为验收目标。

## 源码、版本与复现

新运行器scripts/d0_s01_runtime；协议与完整回归scripts/experiment_d0_s01.py；固定任务和候选验证scripts/d0_s01_validation.py；输入契约复核scripts/d0_s01_contract_review.py；完整池审计scripts/d0_s01_pool_audit.py；关键集成scripts/d0_s01_integration.py；独立工程参考scripts/d0_s01_references.py。

执行顺序（均为已保存的本轮命令，不可在同RunID静默重跑）：

```text
.venv/bin/python -m scripts.experiment_d0_s01 prepare
.venv/bin/python -m scripts.experiment_d0_s01 regression
.venv/bin/python -m scripts.d0_s01_validation fixed
.venv/bin/python -m scripts.d0_s01_validation small
.venv/bin/python -m scripts.d0_s01_validation candidates
.venv/bin/python -m scripts.d0_s01_contract_review
.venv/bin/python -m scripts.d0_s01_pool_audit
.venv/bin/python -m scripts.d0_s01_integration
.venv/bin/python -m scripts.report_d0_s01
```

只读复核：`.venv/bin/python -m scripts.verify_d0_s01`。如需重跑工程回归：`.venv/bin/python -m scripts.replay_d0_s01 --out results/d0_s01_replay_NEW`，必须使用未存在的新目录；不会改旧结果或发起API。

主要证据：taco11_final_audit.json、candidate_admission_final.json/csv、candidate_contract_review.json、full_pool_audit.json、duplicate_groups.json、regression_results.json、reference_small_results.json、critical_integration_results.json、acceptance_gate.json、S1_budget_estimate.json、cost_accounting.json、final_source_manifest.json、final_integrity_audit.json与artifact_manifest.json。全部原始期望、参考失败和中间“初步通过”结果保留。
'''
 (ROOT/'D0-S0.1_补充工程验收报告.md').write_text(report)
 frozen=ROOT/'frozen_code';frozen.mkdir(exist_ok=True)
 files=list(Path('scripts/d0_s01_runtime').glob('*.py'))+list(Path('scripts').glob('*d0_s01*.py'))
 for p in files:
  dest=frozen/('runtime_'+p.name if p.parent.name=='d0_s01_runtime' else p.name);shutil.copyfile(p,dest)
 tmp=Path('/tmp/d0_s01_download.py')
 if tmp.exists():shutil.copyfile(tmp,frozen/tmp.name)
 atomic_json(ROOT/'final_source_manifest.json',{str(p):file_hash(p) for p in files})
 atomic_json(ROOT/'final_integrity_audit.json',dict(protected_files=len(before),historical_files_unchanged=history()==before,changed=[],old_S0_preserved=True,new_runtime_frozen=True,API_requests=0))
 atomic_json(ROOT/'artifact_manifest.json',{str(p):file_hash(p) for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in str(p) and p.name!='artifact_manifest.json'})
 print(json.dumps(dict(status=gate['status'],accepted=len(accepted),excluded=len(excluded),API_cny=0,protected_files=len(before),report=str(ROOT/'D0-S0.1_补充工程验收报告.md')),ensure_ascii=False))
if __name__=='__main__':main()
