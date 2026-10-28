"""Descriptive B0 analysis; no hidden feedback to synthesis, no significance claim."""
import csv,json,time
from pathlib import Path
from collections import Counter
from scripts.experiment_b0 import ROOT,verify,relevant
from scripts.offline_loop_protocol import read_json,atomic_json,file_hash


def mean(v):
    x=[n for n in v if n is not None];return sum(x)/len(x) if x else None


def metric(rows):
    return dict(n=len(rows),correct=sum(r['correct'] for r in rows),submitted=sum(r['submitted'] for r in rows),
        errors=dict(Counter(r['error'] or 'wrong_integer' for r in rows if not r['correct'])),
        mean_input_tokens=mean([r.get('input_tokens') for r in rows]),mean_output_tokens=mean([r.get('output_tokens') for r in rows]),
        mean_calls=mean([r.get('calls') for r in rows]),mean_tool_calls=mean([r.get('tools') for r in rows]),
        mean_tool_seconds=mean([r.get('tool_seconds') for r in rows]),mean_latency_seconds=mean([r.get('actual_latency_seconds') for r in rows]),
        unknown_usage=sum(r.get('unknown_usage',0) for r in rows),budget_violations=sum(r.get('budget_violation',False) for r in rows))


def paired(a,b):
    assert set(a)==set(b)
    return dict(n=len(a),both_correct=sum(a[k]['correct'] and b[k]['correct'] for k in a),both_wrong=sum(not a[k]['correct'] and not b[k]['correct'] for k in a),
        wins=sum(a[k]['correct'] and not b[k]['correct'] for k in a),losses=sum(not a[k]['correct'] and b[k]['correct'] for k in a))


def report():
    m=verify();state=read_json(ROOT/'state.json');assert state['status']=='complete','Evaluation incomplete'
    hidden=read_json(ROOT/'sealed/hidden.json');tasks={r['id']:r for r in hidden}
    methods=['reflection','direct','mechanism'];allrows={}
    for method in methods:
        rows=[read_json(p) for p in (ROOT/f'evaluation_{method}').glob('observations/*.json')];assert len(rows)==44 and {r['id'] for r in rows}==set(tasks)
        allrows[method]={r['id']:r for r in rows}
    results={};pairs={}
    for split in ('transfer','control','natural','all'):
        ids=[r['id'] for r in hidden if split=='all' or r['tier']==split]
        results[split]={method:metric([allrows[method][i] for i in ids]) for method in methods}
        pairs[split]={a+'_vs_'+b:paired({i:allrows[a][i] for i in ids},{i:allrows[b][i] for i in ids}) for a,b in [('mechanism','reflection'),('direct','reflection'),('mechanism','direct')]}
    diag=read_json(ROOT/'diagnosis.json');generation={g:read_json(ROOT/f'generation_{g}/freeze.json') for g in ('F','M')}
    sources={g:file_hash(ROOT/f'generation_{g}/operator.py')==generation[g]['sha256'] for g in ('F','M')};assert all(sources.values())
    # Independently supplied split-role labels, not tool-result correctness labels.
    applicability=[]
    for r in hidden:
        if r['tier'] not in ('transfer','control'):continue
        truth=r['tier']=='transfer';pred=relevant(json.loads(r['problem']))
        applicability.append(dict(id=r['id'],label=truth,prediction=pred,label_source='sealed split construction: long-pattern transfer vs short/no-pattern control',meaning='target relevance, NOT full-solver input validity',cell='TP' if truth and pred else 'FN' if truth else 'FP' if pred else 'TN'))
    costs={}
    for p in ROOT.glob('*/usage.json'):costs[p.parent.name]=read_json(p)
    total=dict(requests=sum(c['requests'] for c in costs.values()),input_tokens=sum(c['input_tokens'] for c in costs.values()),output_tokens=sum(c['output_tokens'] for c in costs.values()),
        known_api_estimate_cny=sum(c['conservative_known_cost_cny'] for c in costs.values()),reserved_or_reported_cny=sum(c['reserved_or_reported_cost_cny'] for c in costs.values()),unknown_usage_requests=sum(c['unknown_usage_requests'] for c in costs.values()),actual_invoice_cost_cny=None)
    validations={g:[read_json(p) for p in (ROOT/f'generation_{g}').glob('attempt_*/validation.json')] for g in ('F','M')}
    local_seconds=dict(program_replays=sum(r[k]['elapsed_seconds'] for r in read_json(ROOT/'program_replays.json') for k in ('original','repair')),
        operator_validation={g:sum(c['result'].get('elapsed_seconds',0) for v in vs for c in v['checks']) for g,vs in validations.items()},monetary_cost=None)
    first_delivered={g:next((v for v in sorted(vs,key=lambda x:x['attempt']) if (ROOT/f'generation_{g}'/f"attempt_{v['attempt']}"/'operator.py').stat().st_size>0),None) for g,vs in validations.items()}
    initial_exec={g:sum(c['result']['status']=='success' for c in v['checks']) if v else None for g,v in first_delivered.items()}
    standalone={};failures=[];local_errors=[]
    for method in methods:
        rows=allrows[method]
        for r in rows.values():
            bad=[dict(step=e['step'],value=e['tool_result'].get('value'),code_sha256=e['tool_result'].get('trace',{}).get('code_sha256')) for e in r['events'] if e.get('tool_result',{}).get('status')=='success' and e['tool_result'].get('value')!=r['expected']]
            if bad:local_errors.append(dict(method=method,id=r['id'],final_correct=r['correct'],recovered=bool(r['correct']),wrong_full_task_tool_values=bad))
            if not r['correct']:
                failures.append(dict(method=method,id=r['id'],tier=tasks[r['id']]['tier'],expected=r['expected'],answer=r['answer'],error=r['error'],
                    tool_results=[dict(function=e['function'],status=e.get('tool_result',{}).get('status'),value=e.get('tool_result',{}).get('value'),error=e.get('tool_result',{}).get('error')) for e in r['events'] if 'tool_result' in e],mechanism='Unknown unless separately manually audited'))
        if method!='reflection':
            inv=[(r,next((e['tool_result'] for e in r['events'] if e['function']=='operator' and 'tool_result' in e),None)) for r in rows.values()]
            standalone[method]={s:dict(n=len(z:=[(r,t) for r,t in inv if s=='all' or tasks[r['id']]['tier']==s]),invoked=sum(t is not None for r,t in z),execution_success=sum(t is not None and t['status']=='success' for r,t in z),correct=sum(t is not None and t['status']=='success' and t['value']==r['expected'] for r,t in z)) for s in ('transfer','control','natural','all')}
    # Recommendation derives from observed paired gains; never requires M to win.
    target=pairs['transfer']['mechanism_vs_direct'];net=pairs['all']['mechanism_vs_reflection'];reg=pairs['natural']['mechanism_vs_reflection']
    if target['wins']>target['losses'] and net['wins']>net['losses'] and reg['losses']==0:direction='Direction 1（有限探索证据）：继续验证机制规格的增量价值；须增加独立生成种子和机制。'
    elif reg['losses']>reg['wins']:direction='Direction 3：优先适用条件与选择性调用，先解释负迁移。'
    elif diag['recovered_local']>0:direction='Direction 2：优先研究可验证局部错误；显式机制指导的增量价值尚待证明。'
    else:direction='Direction 4：当前证据不足以支持扩大机制合成；先寻找更合适的任务边界或局部验证算子。'
    infrastructure_confounded=any(read_json(ROOT/f'generation_{g}/attempt_0/generation.json').get('error','').startswith('RequestFailure:') for g in ('F','M'))
    if infrastructure_confounded:
        direction='优先 Direction 2：研究成功轨迹中的可验证局部失败及反事实诊断。当前生成对照被两组初始连接失败和未通过验证的唯一候选混杂，无法判断显式机制指导是否有增量价值；工程上先将传输故障与真实代码修复机会分开计数，再预注册独立小规模重复，暂不扩大规模。'
    data=dict(exploratory=True,generation_infrastructure_confounded=infrastructure_confounded,seed_replicates=1,results=results,paired=pairs,diagnosis=diag,generation=generation,initial_executable_tasks=initial_exec,
        standalone=standalone,applicability=dict(rows=applicability,confusion=dict(Counter(r['cell'] for r in applicability))),costs=costs,total_usage=total,local_validation=local_seconds,
        failures=failures,verified_local_errors=local_errors,recommended_direction=direction,integrity=dict(operator_hashes=sources,sealed_inputs_unchanged=True,execution_source_unchanged=True),limitations=['one mechanism','one candidate-generation replicate per group','20 natural tasks insufficient for safety','assisted mechanism definition','same model aliases not immutable weights','sequential arms allow service-time confounding','target relevance predictor is structural rule, not learned routing'])
    atomic_json(ROOT/'analysis.json',data);atomic_json(ROOT/'failure_analysis.json',failures);atomic_json(ROOT/'applicability.json',data['applicability'])
    with (ROOT/'results.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=['split','method','n','correct','submitted','mean_calls','mean_tool_seconds','mean_latency_seconds','mean_input_tokens','mean_output_tokens']);w.writeheader()
        for split,ms in results.items():
            for method,v in ms.items():w.writerow(dict(split=split,method=method,**{k:v[k] for k in w.fieldnames if k in v}))
    lines=['# Experiment B0：失败机制发现与机制指导算子合成（探索性）','',
        '## 1. Research Questions','检验具体机制是否可证伪、反事实诊断能否迁移、显式机制规格是否超越原始失败轨迹、算子相对等预算 Reflection 是否有净收益。单机制、每组一个生成试验，所有结果仅为探索。','',
        '## 2. Selected Failure',f'Discovery 来源为 Experiment A Reflection {m["discovery_id"]}。保存完整原始代码、参数、工具结果、最终答案和资源记录，未读取隐藏内部推理。历史任务只作发现材料，不计入本轮独立结果。详见 selected_failure.md、discovery/。','',
        '## 3. Mechanism Hypothesis','观察事实：长度3的模式检查前已截断为2元素。解释：匹配条件永远不可达。可检验假设：保留历史不足会遗漏匹配；新 Agent 是否重复出现这类缺陷是另一个假设。人工辅助记录在 human_interventions.json，不能称为自主发现。','',
        '## 4. Counterfactual Diagnostics',f'10组配对、20道诊断任务。程序级结果：`{diag["program"]}`。新 Reflection 最终失败 {diag["agent_final_errors"]}/20，已验证中间错误后最终恢复 {diag["recovered_local"]}/20；触发错/对照对的脆弱配对 {sum(r["counterfactual_fragility"] for r in diag["paired_diagnostic"])}/10，反向配对 {sum(r["reverse"] for r in diag["paired_diagnostic"])}/10。','',
        '机制支持：原题的单行保留历史干预消除错误，独立触发探针也检验同一预测。反对/限制：不可达模式和短于模式的序列不应被长模式条件一概判错；对照失败会削弱特异性。程序缺陷复现不等于新 Agent 稳定重复此机制。具体触发、未触发、工具失败与无法判断均保存在 program_replays.json、diagnosis.json。错误整数只证明局部错误，不能直接证明同一种后缀缺陷。','',
        '## 5. Operator Specification','采用相同 Full Solver 接口 solve(JSON STRING)->精确整数。M 额外获得由诊断证据形成的输入输出、行为不变量、边界与验证要求；没有提供 oracle 源码或正确求解算法。规格见 operator_specification.json。目标相关性（长模式）与完整求解器输入有效性分开。','',
        '## 6. Direct vs Mechanism-Guided Synthesis','两组相同模型、采样参数、工具、16道独立开发验证题、1候选、最多1修复。F 获原始失败及公共规格；M 获相同材料加诊断规格。未运行可选 S，不能排除规格本身足够的天花板效应。','',
        '| Group | First delivered executable /16 | First delivered correct /16 | Final correct /16 | Used second request slot | Offline known API estimate | Diagnostic API overhead |','|---|---:|---:|---:|---:|---:|---:|']
    for g in ('F','M'):
        f=generation[g];us=costs['generation_'+g];ds=costs['diagnostic_agent']['conservative_known_cost_cny'] if g=='M' else 0
        lines.append(f'| {g} | {initial_exec[g]} | {first_delivered[g]["correct"] if first_delivered[g] else "NA"} | {f["attempts"][-1]["correct"]} | {f["selected_attempt"]} | ¥{us["conservative_known_cost_cny"]+ds:.4f} | ¥{ds:.4f} |')
    lines+=['','两组首次请求均为 APIConnectionError，没有生成可评估源码，初始请求代码质量为 NA，不能计作算法0分。该故障占用了初始槽位；第二槽位产生各组唯一实际候选，未再修复，所以上表的第二槽位次数不等于一次成功代码修复。没有因连接失败额外重试或重启种子；本轮生成对照受到明显基础设施混杂，不能据此判断机制指导优劣。初始空文件与开发0/16日志是占位执行结果，非模型代码质量。','',
        '总开发外部 API 预算上限每组 ¥3；M 的20题 Agent 诊断全部计入其上限（保守分摊），F 可直接用于分析/代码生成和修复。相同上限不意味着实际花费相同；候选次数上限限制了可花出的预算，本试验没有声称等实际成本。辅助研究者分析、任务构造和规格撰写成本未知，未假装免费。所有初始失败及修复保留，源码在解封前冻结。','',
        '## 7. Unseen Transfer Results','全部44题在合成前创建并封存；转移12、对照12、自然20。每题同预算、通用 Python、最终交付机制，算子组固定首调用，实际提交才计分。未按 Reflection 是否失败筛选。','',
        '| Split | Reflection | Direct | Mechanism | M vs Reflection wins/losses | M vs Direct wins/losses |','|---|---:|---:|---:|---:|---:|']
    for s in ('transfer','control','natural','all'):
        z=results[s];a=pairs[s]['mechanism_vs_reflection'];b=pairs[s]['mechanism_vs_direct'];lines.append(f'| {s} | {z["reflection"]["correct"]}/{z["reflection"]["n"]} | {z["direct"]["correct"]}/{z["direct"]["n"]} | {z["mechanism"]["correct"]}/{z["mechanism"]["n"]} | {a["wins"]}/{a["losses"]} | {b["wins"]}/{b["losses"]} |')
    lines+=['',f'独立算子内核首调用结果（与 Agent 最终正确率分开）：`{standalone}`。完整题目及答案在 sealed/hidden.json；冻结记录在 generation_F/freeze.json、generation_M/freeze.json。','',
        '## 8. Regression and Applicability Analysis',f'自然回归净收益配对：`{pairs["natural"]}`。完整适用性混淆：`{data["applicability"]["confusion"]}`。标签来自提前固定的转移/对照构造角色，预测只看公开长模式结构，不看答案。此规则仅判断目标机制相关性，完整求解器对所有有效输入均有功能职责；完整输入域适用性在24题上均为真，没有域外阴性样本，无法估计域边界特异性。上述 TP/TN 是目标相关性而非完整求解器“该不该调用”的安全性标签，不能用其高正确率宣称已解决路由。固定调用结果单独报告。','',
        '## 9. Cost Analysis','所有费用使用继承的输入¥2/百万、输出¥8/百万的保守估算，不是真实账单。此前 A 的实际¥4.10 仅属于A，不按比例折算B0。未知用量保留预留，非零成本。','',
        '| Phase | Requests | Known estimate CNY | Unknown usage |','|---|---:|---:|---:|']
    for phase,u in costs.items():lines.append(f'| {phase} | {u["requests"]} | {u["conservative_known_cost_cny"]:.6f} | {u["unknown_usage_requests"]} |')
    lines+=['',f'总计：`{total}`。本地程序复现及候选验证工具墙钟：`{local_seconds}`；未定价。在线资源：','',
        '| Method | Avg input/output tokens | Calls | Tool time(s) | End-to-end(s) |','|---|---:|---:|---:|---:|']
    for method in methods:
        r=results['all'][method];lines.append(f'| {method} | {r["mean_input_tokens"]:.1f}/{r["mean_output_tokens"]:.1f} | {r["mean_calls"]:.2f} | {r["mean_tool_seconds"]:.3f} | {r["mean_latency_seconds"]:.3f} |')
    lines+=['','## 10. Negative Findings',
        '本轮两组初始生成均被连接错误打断，唯一后续候选又未通过完整验证，故生成优劣证据不足。最终 Agent 正确也可能来自通用 Python 回退，而非算子内核本身。必须保留没有触发、对照失败、自行恢复、生成失败、算子引入错误与天花板现象。F/M 若同分，仅说明本次没有测出显式机制规格的正确率增量；不能通过追加候选直到 M 获胜。生成多题正确不代表跨生成种子稳定。完整终局失败与局部错误在 failure_analysis.json 和 analysis.json。',
        f'本轮 M–F 未见迁移胜/负={target["wins"]}/{target["losses"]}，M–Reflection 全体胜/负={net["wins"]}/{net["losses"]}。','',
        '## 11. Limitations','一个人工辅助机制、每方法一个生成试验、单模型、44道隐藏题、20道自然题；不能推出方法显著优越、自然失败率或低误伤保证。顺序运行三个组，可能有服务时段混杂。高程序复现率不等于 Agent 级广泛缺口。预算上限相同但实际支出和诊断信息不等；这是机制信息增量的探索，不是严格成本匹配确认实验。隐藏题未用于修复，全部分母保留，零自动重试。','',
        '## 12. Recommended Next Research Direction',direction,'',
        '八项回答：',
        f'1. 可复现机制：程序级证据见诊断，原题可由单行干预修复；不能泛化为所有长模式都会错。',
        f'2. 程序缺陷还是 Agent 缺口：前者有直接证据；新 Agent {diag["agent_final_errors"]}/20终局错、{diag["recovered_local"]}/20局部恢复，只有经源码核实的同机制错误才支持后者。',
        '3. 反事实能否证伪：配对含短模式、不可达模式及短序列，触发与反向结果均保留。',
        f'4. 机制规格改善生成：首次质量、修复成本及 M–F 配对见上，单次结果不足以证明稳定改善。',
        f'5. 未见机制修复：M 相对 Reflection 的转移胜/负 {net_t["wins"]}/{net_t["losses"]}。' if (net_t:=pairs['transfer']['mechanism_vs_reflection']) else '',
        f'6. 新错误/负迁移：自然集 M 相对 Reflection 新错 {reg["losses"]}/20，同时修复 {reg["wins"]}/20。',
        f'7. 净收益：全体 M 修复 {net["wins"]}、引入 {net["losses"]}，并分别比较在线资源和前置诊断成本；样本不足以保证长期净收益。',
        '8. 优先投入：采用本节给出的数据驱动建议，不默认扩大机制合成。','',
        '复现（已存在根目录不覆盖；run复用已保存请求，不静默重试）：','```sh','.venv/bin/python -m scripts.experiment_b0 prepare','.venv/bin/python -m scripts.experiment_b0 run --allow-api','.venv/bin/python -m scripts.report_experiment_b0','```','']
    (ROOT/'experiment_b0_report.md').write_text('\n'.join(lines))
    for name,lo,hi in [('operator_generation_report.md','## 5.','## 7.'),('evaluation_report.md','## 7.','## 10.'),('research_direction_report.md','## 12.',None)]:
        text='\n'.join(lines);start=text.index(lo);end=text.index(hi) if hi else len(text);(ROOT/name).write_text(text[start:end])
    atomic_json(ROOT/'integrity_audit.json',data['integrity']);print(json.dumps({'results':results,'paired':pairs,'total_usage':total,'direction':direction},ensure_ascii=False),flush=True)

if __name__=='__main__':report()
