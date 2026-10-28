"""Audit completed development calibration without loading sealed test content."""
from collections import Counter
from pathlib import Path
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.experiment_a_calibration import ROOT
from scripts.counting_benchmark import ROOT as BENCH_ROOT


def report():
    stage1=read_json(ROOT/'reflection_repair/summary.json');cal=read_json(ROOT/'calibration/summary.json')
    if not stage1['complete'] or not cal['complete']:raise ValueError('Wait for both stages to complete')
    bm=read_json(BENCH_ROOT/'manifest.json')
    checks=[]
    for stage in ('reflection_repair','calibration'):
        root=ROOT/stage;m=read_json(root/'manifest.json')
        for rel,sha in m['source_hashes'].items():
            checks.append(dict(path=str(root/'frozen_code'/rel),passed=file_hash(root/'frozen_code'/rel)==sha))
        checks.append(dict(path=str(root/'inputs/development.json'),passed=file_hash(root/'inputs/development.json')==m['input_hash']))
    checks.append(dict(path=str(BENCH_ROOT/'development.json'),passed=file_hash(BENCH_ROOT/'development.json')==bm['development_sha256']))
    checks.append(dict(path='formal_seal_hash_only',passed=file_hash(BENCH_ROOT/'sealed/formal.json')==bm['formal_seal']['sha256']))
    assert all(c['passed'] for c in checks)
    obs=[read_json(p) for p in (ROOT/'calibration/observations').glob('*.json')]
    dev=read_json(BENCH_ROOT/'development.json');ids={r['id'] for r in dev}
    assert len(obs)==180 and {(r['id'],r['arm']) for r in obs}=={(i,a) for i in ids for a in ('baseline','reflection')}
    def infrastructure(r):
        return any(t in r['error'] for t in ('APIConnectionError','InternalServerError','RateLimitError'))
    diagnosis={}
    for arm in ('baseline','reflection'):
        rows=[r for r in obs if r['arm']==arm]
        tool_errors=Counter(e['tool_result'].get('error','') for r in rows for e in r['events'] if e.get('tool_result',{}).get('status')=='error')
        diagnosis[arm]=dict(n=len(rows),correct=sum(r['correct'] for r in rows),submitted=sum(r['submitted'] for r in rows),
            wrong_final_integer=sum(r['submitted'] and r['answer'] is not None and r['answer']!=r['expected'] and not r['error'] for r in rows),
            explicit_abstention=sum(r['error']=='explicit_abstention' for r in rows),
            no_final=sum(not r['submitted'] for r in rows),budget_violations=sum(r['budget_violation'] for r in rows),
            infrastructure_errors=sum(infrastructure(r) for r in rows),
            valid_execution_n=sum(not infrastructure(r) for r in rows),
            tool_errors=dict(tool_errors),review_stage_reached=sum(r['review_performed'] and arm=='reflection' for r in rows),
            draft_correct=sum(r['draft']==r['expected'] for r in rows if r['draft'] is not None),
            draft_wrong_final_right=sum(r['draft'] is not None and r['draft']!=r['expected'] and r['correct'] for r in rows),
            draft_right_final_wrong=sum(r['draft']==r['expected'] and not r['correct'] for r in rows),
            by_tier={tier:dict(n=sum(r['tier']==tier for r in rows),correct=sum(r['tier']==tier and r['correct'] for r in rows),submitted=sum(r['tier']==tier and r['submitted'] for r in rows)) for tier in ('Easy','Medium','Hard')})
    a={r['id']:r for r in obs if r['arm']=='baseline'};b={r['id']:r for r in obs if r['arm']=='reflection'}
    valid_ids=[i for i in ids if not infrastructure(a[i]) and not infrastructure(b[i])]
    pairs=dict(n=len(valid_ids),excluded_infrastructure_pairs=len(ids)-len(valid_ids),reflection_wins=sum(b[i]['correct'] and not a[i]['correct'] for i in valid_ids),reflection_losses=sum(a[i]['correct'] and not b[i]['correct'] for i in valid_ids),ties=sum(a[i]['correct']==b[i]['correct'] for i in valid_ids))
    usage=[]
    for root in (Path('results/experiment_a_calibration_20261014/reflection_repair'),
                 Path('results/experiment_a_calibration_20261014_v2/reflection_repair'),
                 Path('results/experiment_a_calibration_20261014_v2/calibration'),ROOT/'reflection_repair',ROOT/'calibration'):
        rs=[read_json(p) for p in root.glob('requests/*.json')]
        inp=sum((r.get('usage') or {}).get('input_tokens',0) for r in rs);out=sum((r.get('usage') or {}).get('output_tokens',0) for r in rs)
        usage.append(dict(path=str(root),requests=len(rs),input_tokens=inp,output_tokens=out,known_estimate_cny=(2*inp+8*out)/1e6,
            reserved_estimate_cny=sum(r['charged_budget_cny'] for r in rs),unknown_usage=sum(r.get('usage') is None for r in rs)))
    redundancy=read_json(BENCH_ROOT/'development_constraint_audit.json')['summary']
    audit=dict(checks=checks,exact_observation_grid=True,formal_hash_unchanged=True,formal_content_loaded=False,development_constraint_audit=redundancy,
        formal_started=False,new_operator_generated=False,diagnosis=diagnosis,pairs=pairs,usage=usage,
        cost_known_estimate_cny=sum(r['known_estimate_cny'] for r in usage),cost_reserved_estimate_cny=sum(r['reserved_estimate_cny'] for r in usage))
    cost_record=ROOT/'actual_cost.json'
    if cost_record.exists():audit['user_reported_actual_cost']=read_json(cost_record)
    atomic_json(ROOT/'audit.json',audit)
    lines=['# 实验 A：Reflection 修复与组合约束计数开发校准','',
        '**阶段 1、2 已完成。240 道正式题已封存；没有运行正式评测，没有在新 Benchmark 上生成或选择算子。**','',
        '## Reflection 最终交付修复','',
        '执行器预留最后一次模型调用和至少 512 个生成 Token，最后一轮只开放 submit 工具；若生成额度或工具时间提前耗尽，则提前切到最终提交，不能因耗尽非最终额度而跳过交付。首次有效工具结果进入复核阶段，不额外要求一次草稿提交。真实模型最终 submit 才算交付；草稿或工具输出不自动升级。无法求解时允许显式 null，记为弃答。三组的调用、Token、工具、墙钟预算相同。',
        '', '| 原素数开发题验证 | 正确 | 最终交付 | 模型调用 |','|---|---:|---:|---:|']
    for arm,g in stage1['groups'].items():lines.append(f"| {arm} | {g['correct']}/3 | {g['submitted']}/3 | {g['calls']} |")
    lines+=['', '只使用原有 3 道开发题；未重跑历史测试池。该小样本用于排除系统性流程缺陷，不是能力增益结论。',
        '', '## 新 Benchmark 与独立核验','',
        '七类约束：符号次数、相邻不同、元素和取模、禁止子串、子串恰好次数（含重叠）、奇偶位置禁用符号、首尾不同。完整相同的公共规格提供给两个系统；两组均可临时编写 Python 算法，未提供参考 DP 源码。',
        '', '开发集 Easy/Medium/Hard 各 30 题；每层均有 n=8–12、16–24、28–36 三个规模，各 10 题。难度由约束类别数 1–2、3–4、5–7 定义，不能等同于长度。90 题在运行前整体冻结，未按模型正确率替换样本。',
        '', '参考求解器使用独立精确 DP；84 项小规模完整暴力枚举交叉检查和 32 项闭式公式检查全部通过，覆盖重叠模式、位置语义、无解以及长度 100 的大整数。开发集 1 道无解，不以参考答案为零与否筛题。',
        '', '## 开发校准结果','',
        '| 难度 | Baseline | Reflection |','|---|---:|---:|']
    for tier in ('Easy','Medium','Hard'):
        ga=diagnosis['baseline']['by_tier'][tier];gb=diagnosis['reflection']['by_tier'][tier]
        lines.append(f"| {tier} | {ga['correct']}/30（{ga['correct']/30:.1%}） | {gb['correct']}/30（{gb['correct']/30:.1%}） |")
    lines.append(f"| 总体 | {diagnosis['baseline']['correct']}/90（{diagnosis['baseline']['correct']/90:.1%}） | {diagnosis['reflection']['correct']}/90（{diagnosis['reflection']['correct']/90:.1%}） |")
    lines+=['','| 复杂度 × 规模 | Baseline | Reflection |','|---|---:|---:|']
    for tier in ('Easy','Medium','Hard'):
        for scale in ('small','medium','large'):
            ga=cal['groups']['baseline']['cells'][tier+'/'+scale];gb=cal['groups']['reflection']['cells'][tier+'/'+scale]
            lines.append(f"| {tier} / {scale} | {ga['correct']}/10 | {gb['correct']}/10 |")
    lines+=['','| 交付及失败分解 | Baseline | Reflection |','|---|---:|---:|']
    for label,key in [('最终交付','submitted'),('已交付但整数答案错误','wrong_final_integer'),('显式弃答','explicit_abstention'),('没有最终提交','no_final'),('基础设施故障','infrastructure_errors'),('预算违规','budget_violations')]:
        lines.append(f"| {label} | {diagnosis['baseline'][key]} | {diagnosis['reflection'][key]} |")
    lines+=['', f"逐题配对（探索性）：排除 {pairs['excluded_infrastructure_pairs']} 个存在连接/服务基础设施故障的配对后，有效 {pairs['n']} 对，Reflection {pairs['reflection_wins']} 胜、{pairs['reflection_losses']} 负、{pairs['ties']} 平。原始 90 题成功率保留全部分母；未替换题目或偷偷重试。没有将每个工具结果当作独立样本，也不使用正式确认性显著性表述。",'',
        '错误和工具失败原文、正确草稿是否被改变均在 audit.json 与逐题 observations 中保留。显式弃答、网络/执行问题和数学错答分别报告；不能把资源失败解释成已证明的数学机制。',
        '', '在前一版开发校准中得到一个独立核验的建模错误见证：Baseline 生成的程序先把后缀截为 2 位，再检查长为 3 的禁止子串，导致禁止约束失效。原题预测恰好等于删除该约束后的独立参考计数；将问题缩小到 n=3 后，DP 与完整暴力均为 6，该程序为 7，补齐常见 Python 权限后重放仍然如此。见 development_failure_witness.json。这是开发阶段事后诊断，不用于本轮调题，也不冒充本版本或正式机制确认。',
        '', '## 是否进入阶段 3','']
    base=diagnosis['baseline']['correct']/90;ref=diagnosis['reflection']['correct']/90
    if base==1:
        lines+=['Baseline 仍为满分，当前配置缺少能力区分空间。应仅在新的开发协议中提高组合建模复杂度或独立计算规模；不能根据正式结果挑题，也不应立即投入正式算子增益评测。']
    elif .5<=base<=.8 and .6<=ref<=.9:
        lines+=['开发总体正确率落入用户给定的参考区间，已观察到可研究的错误空间。可据此设计阶段 3 的算子生成与验证，但本轮没有执行该阶段；未来必须先冻结候选，再访问封存测试池。']
    else:
        lines+=['已存在错误空间，但总体正确率并非完全落入用户给定的参考区间。该区间不是筛题目标；保留全部结果，根据分格结果和失败轨迹判断是否需要新的开发校准版本。未经新协议，不扩大预算、不替换题目，也不据此宣称算子增益。']
    lines+=['',f"开发题逐项删除约束的事后审计发现：Easy {redundancy['Easy']['instances_with_unchanged_deletion']}/30、Medium {redundancy['Medium']['instances_with_unchanged_deletion']}/30、Hard {redundancy['Hard']['instances_with_unchanged_deletion']}/30 题存在至少一个约束删除后精确计数不变。该检查在其他约束仍保留的条件下逐项进行，不代表一个最小独立约束基。它说明约束类别数不等于有效建模难度；不能仅凭 Hard 标签认定任务更难。所有原题和结果均保留，未根据该审计筛题。",'',
        '建议进入新算子正式增益实验前，先在新开发协议中加强 Hard 的约束非冗余性和交互覆盖；若修改生成规则，保留本版封存池，另封独立测试池。当前 v1 是已完成校准的 Benchmark 原型，不应直接作为已充分验证的难度分级标准。']
    lines+=['', '本次只比较 Baseline 与 Reflection，不能宣称 Generated Operator 有能力增益。正式池的 Easy/Medium/Hard 各 60、OOD 60 已在开发运行前冻结。OOD 含 30 道 n=40–48 的长度外推和 30 道公共接口支持、开发组合池未出现的约束组合。校准入口没有读取正式内容，仅核验封存哈希。若任务生成规则需修改，应保留本版，另建完整协议和独立测试池。',
        '', '## 费用与复现','',
        f"本轮（包括一次旧题汇总类型兼容错误、两版小规模修复验证及前后两版完整开发校准）已报告用量按高峰未缓存单价折算 ¥{audit['cost_known_estimate_cny']:.4f}；包括未知用量预留合计 ¥{audit['cost_reserved_estimate_cny']:.4f}。实际扣费以服务商账单为准。本轮没有调用更贵的生成模型。",'',
        '前版开发结果为 Baseline 74/90、Reflection 75/90，均 90/90 交付。该版存在 print/next/popleft/cache_clear 等常用纯 Python 接口限制，不能把相关工具失败当成数学能力不足。本版统一开放这些操作（打印仅捕获，不作为答案），保留同一任务、相同预算、相同数学提示，并重新整体冻结校准。两版均保留，不按最终结果择优挑题或替换失败样本。', '',
        '模型沿用 deepseek-v4-flash 配置别名，返回名称、原始请求、Token 与 finish_reason 均留存；thinking disabled。三组共享受限 Python，支持精确大整数；每题最多 4 次模型调用、8192 生成 Token、10 秒累计工具墙钟时间、180 秒总执行预算。并发 3，随机交错两组执行。',
        '', '冻结文件：reflection_repair/manifest.json、calibration/manifest.json、各目录 frozen_code/ 和 inputs/。原始记录：requests/、observations/。Benchmark：../compositional_counting_20261014_v1/README.md。',
        '', '工具可用方法采用白名单，代码若使用未开放的 API、未定义 solve 或返回字典仍会失败；这类错误在逐题工具轨迹中可区分，不能直接归因为组合数学能力不足。Easy/Medium/Hard 是结构复杂度标签，经验正确率未必严格单调，不依据结果重新命名题目。局部白名单执行器不是通用恶意代码强安全沙箱。','']
    if cost_record.exists():
        actual=read_json(cost_record)
        index=lines.index('## 费用与复现')+1
        lines[index:index]=['',f"**用户核对 API 账单后的实际花费：¥{actual['amount_cny']:.2f}。本轮费用以此记录为准；以下 Token 折算及预算预留仅为保守估算，不是实际账单。**"]
    (ROOT/'report.md').write_text('\n'.join(lines))
    print({'report':str(ROOT/'report.md'),'diagnosis':diagnosis,'cost_estimate_cny':audit['cost_known_estimate_cny']})


if __name__=='__main__':report()
