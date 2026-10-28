"""Prespecified paired statistics, efficiency, failure and cost report for A."""
from __future__ import annotations
import csv
import json
import math
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
from scripts.generated_operator_experiment import ROOT,BENCH,ARMS,observations
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash
from scripts.compositional_counting import exact_dp


def paired(a,b,reps=10000,seed=2610142102):
    """A candidate minus B control. Exact McNemar, task-paired bootstrap."""
    a=np.asarray(a,dtype=np.int8);b=np.asarray(b,dtype=np.int8)
    if len(a)!=len(b) or not len(a):raise ValueError('Nonempty paired arrays required')
    diff=a-b;n=len(diff);rng=np.random.default_rng(seed)
    boot=diff[rng.integers(0,n,size=(reps,n))].mean(axis=1)
    wins=int(((a==1)&(b==0)).sum());losses=int(((a==0)&(b==1)).sum());k=wins+losses
    p=1.0 if k==0 else min(1.,2*sum(math.comb(k,i) for i in range(min(wins,losses)+1))/(2**k))
    return dict(n=n,operator_wins=wins,control_wins=losses,both_correct=int(((a==1)&(b==1)).sum()),both_wrong=int(((a==0)&(b==0)).sum()),
        difference=float(diff.mean()),ci95=[float(x) for x in np.quantile(boot,[.025,.975])],p_value=p,bootstrap_replicates=reps)


def infra(row):return any(x in row['error'] for x in ('APIConnectionError','InternalServerError','RateLimitError'))

def fmt(value,digits=2):return 'NA' if value is None else f'{value:.{digits}f}'


def metrics(rows):
    if not rows:return dict(n=0)
    def mean(key):
        known=[r[key] for r in rows if r.get(key) is not None]
        return sum(known)/len(known) if known else None
    costs=[(2*r['input_tokens']+8*r['output_tokens'])/1e6 for r in rows]
    times=[r.get('end_to_end_wall_seconds',r['seconds']) for r in rows]
    c=sum(r['correct'] for r in rows);usage_unknown=sum(r['unknown_usage'] for r in rows)
    return dict(n=len(rows),correct=c,accuracy=c/len(rows),submitted=sum(r['submitted'] for r in rows),infrastructure_errors=sum(infra(r) for r in rows),
        avg_input_tokens=mean('input_tokens'),avg_output_tokens=mean('output_tokens'),avg_total_tokens=mean('input_tokens')+mean('output_tokens'),
        avg_llm_calls=mean('calls'),avg_tool_calls=mean('tools'),avg_tool_runtime_seconds=mean('tool_seconds'),
        avg_latency_seconds=float(np.mean(times)),p95_latency_seconds=float(np.quantile(times,.95)),
        known_usage_cost_estimate_cny=sum(costs),avg_known_usage_cost_estimate_cny=sum(costs)/len(rows),
        cost_per_correct_estimate_cny=sum(costs)/c if c else None,unknown_usage_requests=usage_unknown,
        unknown_tool_count_rows=sum(r.get('tools') is None for r in rows),unknown_tool_time_rows=sum(r.get('tool_seconds') is None for r in rows),
        note='Costs are conservative known-token estimates, NOT a bill. Missing usage/time is counted separately, never asserted to be zero cost.')


def failure_label(r):
    if r['correct']:return dict(label='correct',evidence='final exact answer matches reference')
    if infra(r):return dict(label='infrastructure',evidence=r['error'])
    events=r.get('events',[]);tool_results=[e['tool_result'] for e in events if 'tool_result' in e]
    if any(t.get('status')=='success' and t.get('value')==r['expected'] for t in tool_results):
        return dict(label='final_submission',evidence='A correct tool result exists, but final answer was wrong, abstained, or not submitted. No claim about internal reasoning.')
    if r['error'] in ('global_wall_timeout','budget_exceeded','budget_exhausted'):
        return dict(label='tool_call',subtype='resource_limit',evidence=r['error'],limitation='Root algorithmic/implementation cause unknown; a limit alone does not prove complexity.')
    errs=[t.get('error','') for t in tool_results if t.get('status')=='error']
    if any(x in err for err in errs for x in ('worker_timeout','worker_exit_-24','memory_threshold','worker_exit_-14')):
        return dict(label='tool_call',subtype='resource_or_process_termination',evidence=errs,limitation='Root cause may be algorithm, implementation or environment; no forced math attribution.')
    if errs:return dict(label='tool_call',evidence=errs,limitation='may be syntax, return shape, allowed environment or implementation; no forced math attribution')
    return dict(label='unknown',evidence='No uniquely supported mechanism in recorded trace; an incorrect integer alone does not establish a state/transition/counting diagnosis.')


def report(root=ROOT):
    root=Path(root);m=read_json(root/'manifest.json');state=read_json(root/'state.json');freeze_path=root/'operator/freeze.json'
    if not freeze_path.exists() or not (root/'formal_start.json').exists():
        attempts=[dict(path=str(p.relative_to(root)),**read_json(p)) for p in root.glob('candidates/*/*/validation.json')]
        atomic_json(root/'analysis.json',dict(status=state,formal_started=False,attempts=attempts,usage=read_json(root/'usage.json') if (root/'usage.json').exists() else {}))
        text='# Experiment A：未进入正式评测\n\n状态：'+state['status']+'。\n\n'
        text+='严格准入未通过或工程冒烟未通过；没有解封正式题，没有放宽标准，没有人工替换算法。\n\n'
        text+='候选/修复检查：\n\n'
        for a in attempts:text+=f"- {a['path']}：passed={a['passed']}，{a['pool_counts']}，静态错误={a['static_error']}\n"
        text+='\nRQ1–RQ7：正式证据不足，不能判断准确率、效率或泛化增益，也不能据此支持进入失败驱动自动发现阶段。\n'
        text+='\n所有源码、生成响应、修复差异、验证和用量见 candidates/、requests/、usage.json。\n\n复现：`.venv/bin/python -m scripts.generated_operator_experiment run --allow-api`（只复用已有记录，不突破冻结预算）。\n'
        (root/'report.md').write_text(text);return
    rows=observations(root,'formal');tasks=read_json(root/'inputs/formal_unsealed.json')
    if len(rows)!=720 or state['status']!='formal_complete':
        atomic_json(root/'partial_analysis.json',dict(status=state,completed=len(rows),planned=720))
        (root/'report.md').write_text(f'# Experiment A：正式评测未完成\n\n状态：{state["status"]}；完成 {len(rows)}/720 次。所有已完成结果保留，不更改配置、候选或样本，不以部分结果作确认性结论。\n\n详见 usage.json、observations/、requests/。\n')
        return
    by={(r['id'],r['arm']):r for r in rows};ids={r['id'] for r in tasks}
    assert len(by)==720 and set(by)=={(i,a) for i in ids for a in ARMS}
    partitions=dict(ID=[r['id'] for r in tasks if r['tier']!='OOD'],OOD=[r['id'] for r in tasks if r['tier']=='OOD'],
        Easy=[r['id'] for r in tasks if r['tier']=='Easy'],Medium=[r['id'] for r in tasks if r['tier']=='Medium'],Hard=[r['id'] for r in tasks if r['tier']=='Hard'],
        OOD_Length=[r['id'] for r in tasks if r.get('ood')=='parameter'],OOD_Combination=[r['id'] for r in tasks if r.get('ood')=='combination'])
    assert [len(partitions[k]) for k in ('ID','OOD','OOD_Length','OOD_Combination')]==[180,60,30,30]
    results={name:{a:metrics([by[i,a] for i in part]) for a in ARMS} for name,part in partitions.items()}
    results['All']={a:metrics([r for r in rows if r['arm']==a]) for a in ARMS}
    stats={}
    for name,part in partitions.items():
        stats[name]={}
        for control in ('baseline','reflection'):
            stats[name][control]=paired([by[i,'operator']['correct'] for i in part],[by[i,control]['correct'] for i in part])
            valid=[i for i in part if not infra(by[i,'operator']) and not infra(by[i,control])]
            stats[name][control]['infrastructure_sensitivity']=paired([by[i,'operator']['correct'] for i in valid],[by[i,control]['correct'] for i in valid]) if valid else None
    failures=[dict(id=r['id'],arm=r['arm'],tier=r['tier'],**failure_label(r)) for r in rows]
    atomic_json(root/'failure_classification.json',failures)
    # Mechanism attribution remains conservative: Unknown unless trace evidence exists.
    atomic_json(root/'paired_cases.json',[dict(id=i,tier=by[i,'operator']['tier'],outcome=('both_correct' if by[i,'operator']['correct'] and by[i,'reflection']['correct'] else 'operator_win' if by[i,'operator']['correct'] else 'reflection_win' if by[i,'reflection']['correct'] else 'both_wrong'),
        reflection=by[i,'reflection'],operator=by[i,'operator']) for i in partitions['ID']])
    integrity=[]
    for rel,h in m['source_hashes'].items():integrity.append(dict(path=rel,passed=file_hash(root/'frozen_code'/rel)==h))
    for rel,h in m['input_hashes'].items():integrity.append(dict(path=rel,passed=file_hash(root/rel)==h))
    frozen=read_json(freeze_path)
    assert file_hash(root/'operator/solve.py')==frozen['sha256']
    assert file_hash(BENCH/'sealed/formal.json')==m['formal_seal_sha256']
    assert all(c['passed'] for c in integrity)
    op_events=[e for r in rows if r['arm']=='operator' for e in r['events'] if e['function']=='operator']
    assert all(e['tool_result']['trace']['code_sha256']==frozen['trace_code_digest'] for e in op_events)
    allreq=[read_json(p) for p in root.glob('requests/*.json')]
    build=[r for r in allreq if r['key'].startswith('generation/')]
    build_cost=sum((2*(r.get('usage') or {}).get('input_tokens',0)+8*(r.get('usage') or {}).get('output_tokens',0))/1e6 for r in build)
    build_unknown=sum(r.get('usage') is None for r in build)
    cpu_wall=sum(read_json(p).get('seconds',0) for p in root.glob('candidates/*/*/validation.json'))
    avg_op=results['All']['operator']['avg_known_usage_cost_estimate_cny'];avg_ref=results['All']['reflection']['avg_known_usage_cost_estimate_cny']
    amort=[dict(N=n,operator_amortized_estimate=avg_op+build_cost/n,reflection_estimate=avg_ref,advantage_estimate=avg_ref-avg_op-build_cost/n) for n in (10,50,100,500,1000)]
    break_even=math.ceil(build_cost/(avg_ref-avg_op)) if avg_ref>avg_op else None
    # Post-formal-only redundancy analysis, all tasks retained regardless of outcome.
    redundancy=[]
    for r in tasks:
        p=json.loads(r['problem']);unchanged=[]
        for j,c in enumerate(p['constraints']):
            v=exact_dp({**p,'constraints':p['constraints'][:j]+p['constraints'][j+1:]})['answer']
            assert v>=r['expected']
            if v==r['expected']:unchanged.append(c['kind'])
        redundancy.append(dict(id=r['id'],unchanged_deletions=unchanged,has_redundancy=bool(unchanged)))
    atomic_json(root/'formal_redundancy.json',dict(postformal_only=True,used_for_selection=False,rows=redundancy))
    explore={}
    for dim,fn in [('constraint_count',lambda r:str(len(json.loads(r['problem'])['constraints']))),('length',lambda r:str(json.loads(r['problem'])['n']))]:
        groups=defaultdict(list)
        for r in tasks:groups[fn(r)].append(r['id'])
        explore[dim]={g:{a:metrics([by[i,a] for i in part]) for a in ARMS} for g,part in groups.items()}
    explore['redundancy']={str(flag):{a:metrics([by[r['id'],a] for r in redundancy if r['has_redundancy']==flag]) for a in ARMS} for flag in (False,True)}
    violations=[dict(id=r['id'],arm=r['arm'],error=r['error']) for r in rows if r['budget_violation']]
    main=stats['ID']['reflection']
    operator_rows=[r for r in rows if r['arm']=='operator']
    noninfra=[r for r in operator_rows if not infra(r)]
    unreached=sum(not any(e['function']=='operator' for e in r['events']) for r in noninfra)/max(1,len(noninfra))
    resource_rate=sum(any(x in str(e.get('tool_result',{}).get('error','')) for e in r['events'] if e['function']=='operator' for x in ('timeout','worker_exit','memory_threshold')) for r in operator_rows)/240
    strong=main['difference']>=.08 and main['ci95'][0]>0 and not violations and unreached<=.01 and resource_rate<=.05
    latency_ratio=results['All']['operator']['avg_latency_seconds']/results['All']['reflection']['avg_latency_seconds']
    analysis=dict(state=state,results=results,paired_statistics=stats,exploratory=explore,source_integrity=integrity,operator_source_sha256=frozen['sha256'],
        operator_invocations=len(op_events),operator_exec_success=sum(e['tool_result']['status']=='success' for e in op_events),
        operator_tool_correct=sum(e['tool_result'].get('value')==r['expected'] for r in rows if r['arm']=='operator' for e in r['events'] if e['function']=='operator'),
        budget_violations=violations,strong_accuracy_gain=strong,integration_checks=dict(unreached_noninfra_rate=unreached,operator_resource_failure_rate=resource_rate),
        failure_counts={a:dict(Counter(f['label'] for f in failures if f['arm']==a)) for a in ARMS},
        build=dict(requests=len(build),known_token_cost_estimate_cny=build_cost,unknown_usage_requests=build_unknown,local_validation_wall_seconds=cpu_wall,
            local_compute_monetary_cost=None,note='No local compute price available; monetary amortization covers known API tokens only, not full economic TCO.'),
        amortization=amort,api_token_estimate_break_even_N=break_even,actual_invoice_cost_cny=None,usage=read_json(root/'usage.json'))
    atomic_json(root/'analysis.json',analysis)
    with (root/'statistics.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=['partition','control','n','difference','ci_low','ci_high','p_value','operator_wins','control_wins','both_correct','both_wrong']);writer.writeheader()
        for part,cs in stats.items():
            for c,v in cs.items():writer.writerow(dict(partition=part,control=c,**{k:v[k] for k in ('n','difference','p_value','operator_wins','control_wins','both_correct','both_wrong')},ci_low=v['ci95'][0],ci_high=v['ci95'][1]))
    lines=['# Experiment A：Generated Operator Capability Gain','',
        '## 1. Experiment Setup','',
        '固定干预对照：同一基础模型、公共任务语义和通用 Python；算子组第一轮固定调用冻结程序，随后仍由模型真实提交最终答案。不是自动发现、自动路由或经验驱动进化实验。',
        '', '## 2. Benchmark','',
        '保留 v1 全部 90 道开发题与原封存 240 道正式题：180 ID（Easy/Medium/Hard 各 60）及 60 OOD（长度与组合外推各 30）。原题、标签、参考、划分没有修改；Hard 标签只表示约束类别数，不保证经验难度递增。',
        '', '## 3. Generated Operator','',f"选定 candidate={frozen['candidate']}，repair={frozen['repair']}；源码 SHA256：`{frozen['sha256']}`。源码见 operator/solve.py。没有人工修改源码，没有复制参考实现。",'',
        '生成器仅获公共规格、30 道独立 n≤7 训练样例及答案、工具环境，以及自己候选的验证反馈。未提供参考求解器源码、任何旧 Baseline/Reflection 失败轨迹、已诊断机制或正式/OOD 测试题。最多 3 候选、各 3 修复，首个准入者即停止。',
        '', '## 4. Validation','',f"准入结果：{json.dumps(frozen['validation'],ensure_ascii=False)}，全部正确且在每题执行限制内。另有 9 次三组独立工程冒烟，所有配置在正式解封前冻结。生成、修复差异与逐项验证保存在 candidates/。",'',
        '## 5. Baseline Configuration','', '复用已有 Baseline，可规划、临时写 Python 和求解任何合法算法，不能调用冻结算子。',
        '', '## 6. Reflection Configuration','', '复用已修复控制器：自己的有效工具结果进入复核；预留最终调用和 512 生成 Token；最后只开放 submit。正确草稿不自动计分，无法求解允许显式弃答。',
        '', '## 7. Operator Configuration','', '公共工具权限相同；首次工具强制执行同一冻结 solve(problem)。错误/异常/超时只作为观测返回，允许在共同剩余预算内使用通用工具；无参考答案纠正。实际最终 submit 才计成功。',
        '', '## 8. Fairness and Budget Control','',
        '每题最多 4 次模型调用、8192 累计生成 Token、10 秒累计工具墙钟、180 秒实际端到端墙钟；并发 3、随机交错、独立会话、零自动重试。算子时间计入工具预算。使用 deepseek-v4-flash 配置、thinking disabled、temperature=0，实际服务模型名称与指纹保存在请求中。',
        '',f"共 720 次正式作答，全部原分母保留。算子调用 {len(op_events)}/240 次；执行成功 {analysis['operator_exec_success']} 次。预算违规 {len(violations)} 条。缺失资源用量另外计数，不冒充免费或零时延。",'',
        '## 9. Main Results','', '| Method | Easy | Medium | Hard | Overall ID | OOD Length | OOD Combination |','|---|---:|---:|---:|---:|---:|---:|']
    for a in ARMS:
        vals=[f"{results[k][a]['correct']}/{results[k][a]['n']} ({results[k][a]['accuracy']:.1%})" for k in ('Easy','Medium','Hard','ID','OOD_Length','OOD_Combination')]
        lines.append('| '+a+' | '+' | '.join(vals)+' |')
    lines+=['','| Outcome (180 ID, Operator vs Reflection) | Count |','|---|---:|']
    for k in ('both_correct','both_wrong','control_wins','operator_wins'):lines.append(f'| {k} | {main[k]} |')
    lines+=['','## 10. Cost Analysis','', '| Method (all 240 tasks) | Avg Input/Output Tokens | Avg Total Tokens | LLM Calls | Tool Calls | Tool Runtime | Avg/P95 Latency | Known-token Cost Estimate |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for a in ARMS:
        v=results['All'][a];lines.append(f"| {a} | {v['avg_input_tokens']:.1f}/{v['avg_output_tokens']:.1f} | {v['avg_total_tokens']:.1f} | {fmt(v['avg_llm_calls'])} | {fmt(v['avg_tool_calls'])} | {fmt(v['avg_tool_runtime_seconds'],3)}s | {v['avg_latency_seconds']:.2f}/{v['p95_latency_seconds']:.2f}s | ¥{v['known_usage_cost_estimate_cny']:.4f} |")
    lines+=['',f'构建 API 已知 Token 成本保守估算 ¥{build_cost:.4f}；本地验证墙钟累计 {cpu_wall:.2f}s，本地计算未定价，不能称其为零成本。没有取得本轮真实账单；上轮用户核对的 ¥4 不属于本轮，不用于固定折扣。', '',
        '| N | Operator API Amortized Estimate / task | Reflection Estimate / task | Advantage |','|---|---:|---:|---:|']
    for v in amort:lines.append(f"| {v['N']} | ¥{v['operator_amortized_estimate']:.6f} | ¥{v['reflection_estimate']:.6f} | ¥{v['advantage_estimate']:.6f} |")
    lines+=['',f'已知 Token 估算下的 API 成本回收点：{break_even if break_even is not None else "不存在（在线成本未降低）"} 次；不包含未知用量、本地计算定价和人工工程成本，不是完整经济 TCO。',
        '', '## 11. OOD Generalization','', '长度与组合外推的结果及独立配对检验见 analysis.json。两类分别报告，不从 ID 成功推断 OOD 成功；正式失败后没有修复或换候选。',
        '', '## 12. Failure Analysis','',f"保守轨迹归因：{json.dumps(analysis['failure_counts'],ensure_ascii=False)}。逐题类别和证据见 failure_classification.json、paired_cases.json，无法确定的状态/转移/重叠/约束机制保留 Unknown，不用开发案例推断正式频率。",'',
        '正式评测全部结束后才做完整正式池冗余审计，见 formal_redundancy.json。按约束类别数、长度及冗余性的分析见 analysis.json exploratory，均为解释性分析，不改变主样本或原分层。',
        '', '## 13. Statistical Significance','', '| ID Comparison | Accuracy Difference | Paired 95% CI | Exact McNemar p |','|---|---:|---:|---:|']
    for c in ('baseline','reflection'):
        v=stats['ID'][c];lines.append(f"| Operator - {c} | {100*v['difference']:+.2f} pp | [{100*v['ci95'][0]:.2f}, {100*v['ci95'][1]:.2f}] pp | {v['p_value']:.6g} |")
    lines+=['',f'每题为配对重采样单位，10,000 次 Bootstrap，种子事先冻结。主要确认性比较仅为 180 ID 上 Operator–Reflection；8 pp 且区间下界>0 的强增益门槛是否达到：{strong}。其他比较及 OOD 检验分别报告，不事后改主要指标。基础设施有效配对敏感性见 analysis.json。',
        '', '## 14. Threats to Validity','',
        '单模型、单任务族、每组每题一次；服务商无不可变权重版本。结构类别数并非真实难度，约束可能冗余。固定调用不能证明自主路由或发现。Python 白名单/进程资源错误与数学错答分开；资源终止不能强推数学机制。费用仅为保守 Token 估算，存在未知用量；本地计算未定价。基础设施异常保留全分母，另报有效配对，不静默重试。',
        '', '## 15. Conclusion / Research Questions','']
    for rq,c in [('RQ1','baseline'),('RQ2','reflection')]:
        v=stats['ID'][c];lines.append(f"- {rq}：ID 正确率差 {v['difference']:+.2%}，95% CI {v['ci95']}，p={v['p_value']:.6g}；是否存在正差统计证据：{v['ci95'][0]>0}。强增益预设仅以 RQ2 判定，不混淆成本收益。")
    lines += [f"- RQ3：ID 中修复 Reflection 失败 {main['operator_wins']} 次，同时引入 {main['control_wins']} 次新失败。机制只依据真实轨迹证据，Unknown 不强推；不能把全体失败视为数学能力不足。",
        f"- RQ4：在线平均总 Token，Operator={results['All']['operator']['avg_total_tokens']:.1f}，Reflection={results['All']['reflection']['avg_total_tokens']:.1f}；平均端到端时延比={latency_ratio:.3f}。效率与准确率独立评价。",
        f"- RQ5：长度 OOD={results['OOD_Length']['operator']['correct']}/30，组合 OOD={results['OOD_Combination']['operator']['correct']}/30；范围只限本次未见参数/组合测试。",
        f"- RQ6：API 已知 Token 成本估算回收点={break_even}；真实账单和完整本地成本未知，不能断言普遍长期经济性。",
        '- RQ7：'+('本轮支持继续研究失败驱动的自动发现，但只建立了冻结算子的能力/效率前提；未证明发现、抽象、动态扩展或路由能力。' if strong else '未达到预设强准确率增益门槛；先按准确率、效率和泛化分别审视结果，不能据此宣称完整失败驱动自动发现已成立。'),
        '', '复现命令：', '', '```sh', '.venv/bin/python -m scripts.generated_operator_experiment prepare', '.venv/bin/python -m scripts.generated_operator_experiment run --allow-api', '.venv/bin/python -m scripts.report_generated_operator', '```', '',
        '已有目录不可覆盖；恢复仅复用记录，不静默重试未知请求。冻结后改源码将拒绝继续。产物：operator/solve.py、operator/freeze.json、candidates/、requests/、observations/、analysis.json、statistics.csv、failure_classification.json、formal_redundancy.json、usage.json。','']
    (root/'report.md').write_text('\n'.join(lines))
    print(json.dumps(dict(status=state['status'],main=main,operator_sha256=frozen['sha256'],strong_gain=strong,actual_invoice_cost_cny=None),ensure_ascii=False))


if __name__=='__main__':report()
