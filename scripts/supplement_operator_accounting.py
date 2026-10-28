"""Post-run descriptive exports; never changes formal inputs or primary tests.

Includes engineering smoke in a conservative pre-formal cost sensitivity so
generation-only accounting cannot understate validation deployment overhead.
"""
import csv
import math
from pathlib import Path
from collections import Counter
from scripts.generated_operator_experiment import ROOT,ARMS
from scripts.offline_loop_protocol import read_json,atomic_json


def run():
    root=ROOT;a=read_json(root/'analysis.json')
    if a['state']['status']!='formal_complete':raise ValueError('Formal must complete first')
    smoke_ids={r['id'] for r in read_json(root/'inputs/smoke.json')}
    requests=[read_json(p) for p in root.glob('requests/*.json')]
    groups={k:[] for k in ('generation_repair','engineering_smoke','formal')}
    for r in requests:
        phase='generation_repair' if r['key'].startswith('generation/') else 'engineering_smoke' if r['key'].split('/')[1] in smoke_ids else 'formal'
        groups[phase].append(r)
    phase_costs={}
    for phase,rs in groups.items():
        inp=sum((r.get('usage') or {}).get('input_tokens',0) for r in rs);out=sum((r.get('usage') or {}).get('output_tokens',0) for r in rs)
        phase_costs[phase]=dict(requests=len(rs),input_tokens=inp,output_tokens=out,known_api_estimate_cny=(2*inp+8*out)/1e6,
            reserved_or_reported_estimate_cny=sum(r['charged_budget_cny'] for r in rs),unknown_usage_requests=sum(r.get('usage') is None for r in rs))
    core=phase_costs['generation_repair']['known_api_estimate_cny']
    full=core+phase_costs['engineering_smoke']['known_api_estimate_cny']
    op=a['results']['All']['operator']['avg_known_usage_cost_estimate_cny'];ref=a['results']['All']['reflection']['avg_known_usage_cost_estimate_cny']
    table=[dict(N=n,core_build_operator_estimate=op+core/n,full_preformal_operator_estimate=op+full/n,
        reflection_estimate=ref,full_preformal_advantage_estimate=ref-op-full/n) for n in (10,50,100,500,1000)]
    supplementary=dict(primary_results_unchanged=True,costs_are_estimates_not_invoice=True,phase_costs=phase_costs,
        core_api_build_estimate_cny=core,all_preformal_api_estimate_cny=full,
        amortization_sensitivity=table,all_preformal_estimate_break_even_N=math.ceil(full/(ref-op)) if ref>op else None,
        local_compute_monetary_cost=None,preparation=read_json(root/'preparation.json'))
    atomic_json(root/'cost_accounting.json',supplementary)
    with (root/'stratified_results.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=['axis','stratum','method','n','correct','accuracy']);writer.writeheader()
        for axis,groups in a['exploratory'].items():
            for stratum,methods in groups.items():
                for method,v in methods.items():writer.writerow(dict(axis=axis,stratum=stratum,method=method,**{k:v[k] for k in ('n','correct','accuracy')}))
    tasks=read_json(root/'inputs/formal_unsealed.json')
    rows={(r['id'],r['arm']):r for r in (read_json(p) for p in root.glob('observations/*.json')) if r.get('stage')=='formal'}
    labels={(r['id'],r['arm']):r for r in read_json(root/'failure_classification.json')}
    profiles={};pairs=[]
    for r in tasks:
        i=r['id'];oprow=rows[i,'operator'];rr=rows[i,'reflection']
        outcome='both_correct' if oprow['correct'] and rr['correct'] else 'operator_win' if oprow['correct'] else 'reflection_win' if rr['correct'] else 'both_wrong'
        pair=dict(id=i,partition='ID' if r['tier']!='OOD' else 'OOD',tier=r['tier'],ood=r.get('ood'),outcome=outcome,
            reflection_failure_label=labels[i,'reflection']['label'],operator_failure_label=labels[i,'operator']['label'])
        pairs.append(pair)
    for part in ('ID','OOD'):
        profiles[part]=dict(operator_wins_by_control_failure=dict(Counter(r['reflection_failure_label'] for r in pairs if r['partition']==part and r['outcome']=='operator_win')),
            operator_losses_by_operator_failure=dict(Counter(r['operator_failure_label'] for r in pairs if r['partition']==part and r['outcome']=='reflection_win')))
    atomic_json(root/'repair_profiles.json',profiles)
    with (root/'pair_outcomes.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(pairs[0]));writer.writeheader();writer.writerows(pairs)
    marker='\n## 补充：费用口径与完整探索性导出\n'
    text=(root/'report.md').read_text().split(marker)[0]
    lines=[marker,'以下补充不更改正式题、任何主要指标、正确率、配对检验或候选选择。费用均为已报告 Token 的保守估算，不是实际账单。','',
        '| 阶段 | API Requests | 已知 Token 费用估算 | 含未知用量预留 | 未知用量请求 |','|---|---:|---:|---:|---:|']
    for phase,c in phase_costs.items():lines.append(f"| {phase} | {c['requests']} | ¥{c['known_api_estimate_cny']:.4f} | ¥{c['reserved_or_reported_estimate_cny']:.4f} | {c['unknown_usage_requests']} |")
    lines+=['',f'核心生成/修复及本地候选验证口径的已知 API 估算为 ¥{core:.4f}；若将三组共用工程冒烟也全部计入正式前验证，保守口径为 ¥{full:.4f}。本地计算未定价，完整经济成本不能由这两项 API 估算替代。', '',
        '| N | 含全部正式前验证的 Operator 估算/题 | Reflection 估算/题 | 估算优势 |','|---|---:|---:|---:|']
    for r in table:lines.append(f"| {r['N']} | ¥{r['full_preformal_operator_estimate']:.6f} | ¥{r['reflection_estimate']:.6f} | ¥{r['full_preformal_advantage_estimate']:.6f} |")
    lines+=['',f"含共用冒烟验证的 API 估算回收点：{supplementary['all_preformal_estimate_break_even_N']} 次；不代表含本地计算、未知用量或人工成本的实际回收点。",'',
        '| Method | 已知 Token 费用 / 正确答案 | 已知 Tokens / 正确答案 | 未知用量请求 |','|---|---:|---:|---:|']
    for method in ARMS:
        r=a['results']['All'][method];c=r['cost_per_correct_estimate_cny'];tokens=r['avg_total_tokens']*r['n']/r['correct'] if r['correct'] else None
        lines.append(f"| {method} | {('¥'+format(c,'.6f')) if c is not None else 'NA'} | {format(tokens,'.1f') if tokens is not None else 'NA'} | {r['unknown_usage_requests']} |")
    lines+=['',f'按轨迹证据划分的改善/退化：`{profiles}`。Unknown 不强行升级为遗漏、状态不足或重叠处理错误。', '',
        '完整原始分层见主表；约束类别数、每个具体 n、逐项删除约束后的冗余性分层见 stratified_results.csv。所有 240 题配对分类见 pair_outcomes.csv；没有根据探索性结果重选主样本。', '',
        '生成输入与自动源码来源的逐调用重建审计见 provenance_audit.json。主运行与报告命令之外，复现本补充：`.venv/bin/python -m scripts.supplement_operator_accounting`。','']
    (root/'report.md').write_text(text+'\n'.join(lines))
    print({'cost_accounting_written':True,'full_preformal_api_estimate_cny':full,'phases':phase_costs})


if __name__=='__main__':run()
