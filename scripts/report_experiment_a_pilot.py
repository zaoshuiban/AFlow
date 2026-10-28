"""Read-only artifact audit and Chinese result report for Experiment A pilot."""
import json
from collections import Counter
from pathlib import Path
from scripts.offline_loop_protocol import read_json, atomic_json, file_hash, digest


def report(root=Path('results/experiment_a_pilot_20261014_v4')):
    summary=read_json(root/'summary.json')
    if not summary['complete'] or read_json(root/'state.json')['status']!='pilot_complete':
        raise ValueError('Pilot is not complete')
    manifest=read_json(root/'manifest.json')
    obs=[read_json(p) for p in root.glob('observations/*.json')]
    integrity=[]
    for field in ('source_hashes','input_hashes','candidate_hashes'):
        for rel,sha in manifest[field].items():
            path=Path(rel) if field=='source_hashes' else root/rel
            integrity.append(dict(path=str(path),passed=file_hash(path)==sha))
    inputs=read_json(root/'inputs/pilot.json')
    ids={r['id'] for r in inputs}
    expected={(i,a) for i in ids for a in ('baseline','reflection','operator')}
    actual={(r['id'],r['arm']) for r in obs}
    assert actual==expected and len(obs)==90
    assert all(c['passed'] for c in integrity)
    assert not (root/'inputs/formal.json').exists()
    violations=[r['id']+'/'+r['arm'] for r in obs if r['calls']>4 or r['output_tokens']>8192 or r['tools']>4 or r['tool_wall_seconds']>10 or r['accounted_seconds']>180]
    versions=[]
    for name in ('experiment_a_pilot_20261014','experiment_a_pilot_20261014_v2','experiment_a_pilot_20261014_v3','experiment_a_pilot_20261014_v4'):
        rs=[read_json(p) for p in (root.parent/name).glob('requests/*.json')]
        inp=sum((r.get('usage') or {}).get('input_tokens',0) for r in rs)
        out=sum((r.get('usage') or {}).get('output_tokens',0) for r in rs)
        versions.append(dict(version=name,requests=len(rs),input_tokens=inp,output_tokens=out,
            known_estimate_cny=(2*inp+8*out)/1e6,
            reserved_estimate_cny=sum(r['charged_budget_cny'] for r in rs),
            unknown_usage_requests=sum(r.get('usage') is None for r in rs)))
    details={}
    for arm in ('baseline','reflection','operator'):
        rows=[r for r in obs if r['arm']==arm]
        drafts=[r for r in rows if any(e['action'].get('action')=='answer' and e['action'].get('value')==r['expected'] for e in r['events'])]
        tool_correct=[r for r in rows if any(e.get('tool_result',{}).get('status')=='success' and e['tool_result']['value']==r['expected'] for e in r['events'])]
        details[arm]=dict(correct_answer_in_trace=len(drafts),correct_tool_result_in_trace=len(tool_correct),
            action_patterns=dict(Counter(','.join(e['action']['action'] for e in r['events']) for r in rows)),
            tool_errors=dict(Counter(e['tool_result'].get('observation','') for r in rows for e in r['events'] if e.get('tool_result',{}).get('status')!='success' and 'tool_result' in e)),
            tiers={str(t):dict(n=sum(r['tier']==t for r in rows),correct=sum(r['tier']==t and r['correct'] for r in rows)) for t in range(3)})
    selection=read_json(root/'selection.json')
    winner=read_json(root/'candidates'/f"candidate_{selection['candidate']}.json")
    operator_events=[e for r in obs if r['arm']=='operator' for e in r['events'] if e['action']['action']=='operator']
    hashes={e['tool_result']['tool_trace']['code_sha256'] for e in operator_events}
    assert hashes=={selection['code_hash']}
    assert all(r['events'][0]['action']['action']=='operator' for r in obs if r['arm']=='operator')
    cost=sum(v['known_estimate_cny'] for v in versions)
    reserve=sum(v['reserved_estimate_cny'] for v in versions)
    audit=dict(complete=True,formal_evaluation_started=False,integrity=integrity,budget_violations=violations,
        exact_observation_grid=True,frozen_operator_hashes=sorted(hashes),versions=versions,
        total_known_estimate_cny=cost,total_reserved_estimate_cny=reserve,
        total_unknown_usage_requests=sum(v['unknown_usage_requests'] for v in versions),details=details)
    atomic_json(root/'audit.json',audit)
    lines=['# 实验 A：生成、验证与预实验结果','',
        '**已完成生成、验证与 30 题三组预实验；没有生成或执行正式评测。**','',
        '## 生成与验证','',
        '首轮因 JSON 解析权限与输入格式冲突，两次生成均被导入检查拒绝。修正三组共同权限后重新冻结并生成两个候选。候选 1 未通过数学验证；候选 2 通过全部 99 项检查，原始源码未经人工修正。',
        '',f"最终算子源码哈希：`{selection['code_hash']}`。验证覆盖 30 道开发题、60 道随机小规模验证题、6 道边界题、3 道无效输入。参考答案在 66 道验证/边界题上由两种独立方法交叉核对。另有 24 项选择后性质审计全部通过，不用于重新选择候选。",'',
        '## 冻结预实验结果','',
        '受限数字素数精确枚举，30 题、每组一次。模型返回名称为 deepseek-flash，非思考模式。所有组均有相同通用 Python 权限；算子组首次固定执行同一冻结算子，不测试路由发现。',
        '', '| 系统 | 预算内正确 | 模型调用 | 输入 Token | 输出 Token | 平均耗时 |',
        '|---|---:|---:|---:|---:|---:|']
    labels={'baseline':'Baseline','reflection':'Baseline + Reflection','operator':'Baseline + Generated Operator'}
    for arm,g in summary['groups'].items():
        lines.append(f"| {labels[arm]} | {g['correct']}/{g['n']} | {g['calls']} | {g['input_tokens']:,} | {g['output_tokens']:,} | {g['mean_seconds']:.2f} 秒 |")
    baseline_tokens=sum(summary['groups']['baseline'][k] for k in ('input_tokens','output_tokens'))
    operator_tokens=sum(summary['groups']['operator'][k] for k in ('input_tokens','output_tokens'))
    lines+=['',f'相对 Baseline，算子组在线总 Token 变化为 {(operator_tokens/baseline_tokens-1)*100:+.2f}%。这只描述在线效率；生成、验证和调试成本另计，不能把在线节省当成已证明的端到端经济收益。']
    lines+=['',f"算子实际调用 {len(operator_events)} 次，全部使用同一源码哈希；共同预算违规 {len(violations)} 条。",'',
        '反思组失败分类：'+json.dumps(summary['groups']['reflection']['errors'],ensure_ascii=False)+'。',
        f"反思组 {details['reflection']['correct_answer_in_trace']}/30 条轨迹出现过正确答案，{details['reflection']['correct_tool_result_in_trace']}/30 条出现过正确工具结果；这些不自动计为最终答案。",'',
        '## 科学解释','',
        '不能将反思组未在预算内交付最终答案解释为数学能力不足，也不能将流程失败造成的胜差当成算子的推理能力增益。先按预注册交付规则报告原始成功率，再单独报告正确草稿、工具结果和失败原因。',
        '', f"本轮原始基线为 {summary['groups']['baseline']['correct']}/30，算子组为 {summary['groups']['operator']['correct']}/30：两组均满分，本任务规模没有准确率区分空间；目前只能描述在线执行效率。本轮没有通过“生成算子稳定超过等预算推理”的科学门槛，不宜直接照搬为正式评测。正式评测前应先解决反思最终交付规则，并独立预注册具有基线错误空间的任务规模；不能把本轮题目重新当成未见确认题。",
        '', '本实现是具有共同工具接口的隔离结构化 Agent，不是原始 AFlow 完整工作流复现。只有单任务族、单模型和每题一次预实验，不计算确认性显著性或宣称跨种子稳定性。实际模型名称已保存，但服务商没有返回更精确的不可变权重版本。',
        '', '## 费用与留痕','',
        f'四个版本累计已报告用量按高峰未缓存价格估算 ¥{cost:.4f}；包括中断未知用量请求的保守预留合计 ¥{reserve:.4f}。未知用量请求 {audit["total_unknown_usage_requests"]} 次，实际扣费以供应商账单为准。所有接口调试、失败生成和中断请求都计入，没有只计算最终版本。',
        '', 'v2 文本 JSON 动作冒烟、v3 原生接口冒烟均主动中断，原文保留。v4 统一明确函数返回规范，固定首次算子干预，复用已验证候选，并排除此前两个完整预实验池的参数题族；未根据预实验选择新的算法或挑选最优题目。',
        '', '产物：manifest.json、inputs/、frozen_code/、candidates/、selection.json、requests/、observations/、summary.json、audit.json、supplemental_property_audit.json。正式评测保持未启动。','']
    (root/'report.md').write_text('\n'.join(lines))
    print(json.dumps(dict(report=str(root/'report.md'),cost_estimate_cny=cost,reserved_estimate_cny=reserve,groups=summary['groups']),ensure_ascii=False))


if __name__=='__main__':report()
