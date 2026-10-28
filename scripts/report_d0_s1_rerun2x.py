"""Report only; never calls models or runs candidate programs."""
import json,hashlib
from pathlib import Path
from collections import Counter
P=Path('results/experiment_d0_s1_20261019_rerun2x_v1')
def read(n):return json.loads((P/n).read_text())
def write(n,x):(P/n).write_text(json.dumps(x,ensure_ascii=False,indent=2))
def main():
 rows=read('results.json');ledger=read('ledger.json');prot=read('protocol.json');calls=ledger['calls'];done=[r for r in rows if r['status']=='SUBMITTED'];unknown=[c for c in calls if c['status']!='COMPLETED'];known=[c for c in calls if c['status']=='COMPLETED']
 pi=sum(c['usage']['prompt_tokens'] for c in known);po=sum(c['usage']['completion_tokens'] for c in known);hits=sum(c['usage'].get('prompt_cache_hit_tokens',0) for c in known)
 counts=Counter(r.get('verdict',r['status']) for r in rows)
 protected=read('protected_after.json') if (P/'protected_after.json').exists() else {'changed':['INTEGRITY_CHECK_NOT_COMPLETED']}
 bad=['INFRASTRUCTURE_ERROR','JUDGE_ERROR','EVALUATOR_BUDGET_EXHAUSTED']
 accepted=len(done)==13 and all(r.get('self_tests',0)>0 and r.get('verdict') not in bad for r in done) and not protected['changed'] and not unknown and ledger['committed_upper_cny']<=4
 status='S1_ACCEPTED' if accepted else 'S1_NOT_ACCEPTED'
 cost={'requests':len(calls),'input_tokens':pi,'output_tokens':po,'cache_hits':hits,'peak_no_cache_committed_upper_cny':ledger['committed_upper_cny'],'peak_cache_aware_known_cny':((pi-hits)*2+hits*.04+po*8)/1e6,'offpeak_cache_aware_known_cny':((pi-hits)+hits*.02+po*4)/1e6,'unknown_delivery_reservation_cny':sum(c['reserved_cny'] for c in unknown),'provider_invoice':'NOT_VERIFIED','stage_cap_cny':4}
 write('cost_summary.json',cost);write('acceptance_gate.json',{'status':status,'registered':13,'submitted':len(done),'counts':dict(counts),'accuracy_used_for_gate':False,'history_changed':protected['changed'],'later_stages':'NOT_RUN'})
 # Stratified extrapolation: TACO has only one observation, no confidence claims.
 sources={}
 for r in rows:
  if r.get('calls',0):sources.setdefault(r['source'],[]).append(r)
 estimates={s:{'n':len(v),'mean_cny':sum(r['cost_upper_cny'] for r in v)/len(v),'mean_input_tokens':sum(r['input_tokens'] for r in v)/len(v),'mean_output_tokens':sum(r['output_tokens'] for r in v)/len(v)} for s,v in sources.items()}
 forecast90=sum(30*v['mean_cny'] for v in estimates.values()) if len(estimates)==3 else None
 forecast540=forecast90*6 if forecast90 is not None else None
 write('forecast.json',{'by_source':estimates,'90_source_balanced_cny':forecast90,'540_source_balanced_cny':forecast540,'90_budget_max_cny':90*.211072,'540_budget_max_cny':540*.211072,'warning':'Small engineering sample, TACO n=1; changed context policy; excludes diagnosis/retries/unknown charges and local/manual costs. Not authorization.'})
 lines=['# D0-S1 翻倍预算重跑报告','',f'结论：**{status}**。本轮预注册13题，提交{len(done)}题。没有启动S2-Prepare或S2–S4。','', '## 范围、授权与冻结','',f"Run ID：{P.name}。用户要求‘增大两杯预算重新跑一遍13题’，按预算翻倍执行：本轮阶段上限4元（不包含旧轮费用）；每题最多8次调用、累计输入40000/输出16384 Token、公开自测20秒、在线360秒，隐藏评测独立150秒。原13题及顺序保持不变；不按正确率筛选。旧轮记录保留，全部样本保持ENGINEERING_SMOKE身份。",'', '沿用d0-programming-v2-s01原生操作系统沙箱与资源限制。新增驱动独立冻结，不修改历史运行器。模型请求仍使用deepseek-v4-flash别名、temperature=0、thinking disabled、SDK max_retries=0。每次返回模型标识、usage、时间及原始响应保存。别名不能证明权重不可变。','', '## 修复与离线验收','', '提交阶段只暴露submit工具；首次程序诊断使用支持的diagnostic角色；评分前保存检查点。每轮模型上下文保留公共任务和最新完整源码/工具反馈对，旧消息仍完整保存在逐请求文件，避免重复源码导致字节预算估计提前耗尽。该上下文策略与旧轮不同，因此两轮不能作为单纯“预算翻倍”的因果对照。输入仍以UTF-8字节+2048保守预留，若实际usage超界即停止。未知交付保留正费用预留，不重试。','', '离线验收使用模拟模型响应和真实原生沙箱，完整验证自测→提交→隐藏PASS→首次诊断PASS、提交后关闭、失败请求不重试且保留费用预留。离线验收没有模型API费用。','', '## 逐题结果','', '|任务|来源|状态|首次程序|最终判题|调用|Token 输入/输出|保守费用(元)|','|---|---|---|---|---|---:|---|---:|']
 for r in rows:lines.append(f"|{r['id']}|{r.get('source','—')}|{r['status']}|{r.get('first_verdict','未评测')}|{r.get('verdict','未评测')}|{r.get('calls',0)}|{r.get('input_tokens',0)}/{r.get('output_tokens',0)}|{r.get('cost_upper_cny',0):.6f}|")
 lines+=['','判题现象统计：'+str(dict(counts))+'。首次与最终结果是程序评分，不自动等于可信根因、机制复现或算子价值。超时、执行策略拒绝、接口错误、基础设施及评分器异常保持独立，不合并为语义Wrong Answer。','', '## 费用与后续估算','',f"本轮物理请求{len(calls)}次，输入{pi}、输出{po} Token，缓存命中{hits}。峰时无缓存保守账本（含未知交付预留）**{ledger['committed_upper_cny']:.6f}元**，上限4元；已知用量按缓存字段计价峰时{cost['peak_cache_aware_known_cny']:.6f}元、谷时{cost['offpeak_cache_aware_known_cny']:.6f}元。服务商实际账单待核对，不将此计算值称为已核实账单。价格基准见[DeepSeek官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。",'',f'未知交付请求{len(unknown)}次；不删除失败单元、不额外重跑。费用未使用旧实验4元/4.1元比例。工具、本地判题与人工时间不混为API账单，逐题online/tool/hidden时间见results.json；模型时延逐请求见ledger.json。','',f'按来源等权外推90题：{forecast90}元，540题：{forecast540}元（峰时无缓存用量记账）。仅为本轮小样本情景估计，TACO最多1题，不能代表更难任务的Token或诊断费用。每题用满翻倍预算的算术上限为90题18.99648元、540题113.97888元；另需预留未知交付、任何未来授权的重试及诊断费用。任务池尚未完成全面准入，后续规模仍需单独放行。','', '## 隔离、证据与边界','',f"历史保护清单核验：{protected.get('checked','未完成')}文件，变化{len(protected['changed'])}。原S0.1与旧S1全部保留。模型只接收s1_public_tasks.json及自己可观察的工具输出，工具不返回隐藏期望或判题结果。提交后锁定源码并关闭在线会话，隐藏与首次程序诊断不回传模型。",'',f'本轮工程判断为{status}。通过与否仅对应真实模型、自测、提交、隔离评分、预算和记录链路；不证明540题已可信准入，不评价失败驱动机制，也不证明已观察错误具有研究价值。首次程序与最终差异仅作为探索线索，因果审查及跨程序复现未开展。','', '## 复现与文件','', '协议见protocol.json，离线验收见offline_acceptance.json，原请求/响应/每版源码/工具输出/冻结提交/两类评分见tasks目录，费用账本见ledger.json。源码快照与哈希清单附后。离线重生成报告：`.venv/bin/python -m scripts.report_d0_s1_rerun2x`。真实入口检测已有ledger后拒绝复跑；本报告不调用任何模型。']
 
 if 'amendment' in prot:
  lines+=['','## 工程补充协议与配对边界','',prot['amendment'],'','前5题结果与轨迹位于 '+prot['prior_run']+'；本目录保存后8题轨迹。共享账本已包含前5题调用，不重复求和。原轮在第5题后按原停止规则退出；继续前另行冻结本补充协议。故13题合计是授权工程描述性汇总，不是原首9题门槛已通过，也不是未经修改的确认性实验。所有未提交题仍计入13题分母，不自动提交最后一次自测代码，不删除或重跑失败题。','', '部分请求在仅暴露submit并指定submit后仍返回self_test，证明工具选择约束没有形成可靠交付保证。当前观察不能区分供应商工具约束兼容问题与模型行为，须离线/独立接口验收，不直接归因于算法。上下文压缩只保留最近一次源码和反馈，未动态提醒剩余次数，可能影响循环与交付；本轮没有对这些因素作消融，因此不能把差异仅归于预算。']
 (P/'D0-S1_翻倍预算重跑报告.md').write_text('\n'.join(lines)+'\n')
 for f in ['scripts/experiment_d0_s1_rerun2x.py','scripts/test_d0_s1_rerun2x.py','scripts/report_d0_s1_rerun2x.py']:(P/Path(f).name).write_bytes(Path(f).read_bytes())
 write('artifact_manifest.json',{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in P.rglob('*') if f.is_file() and f.name!='artifact_manifest.json'})
 print(json.dumps({'status':status,'counts':dict(counts),'cost':cost,'forecast90':forecast90,'forecast540':forecast540},ensure_ascii=False))
if __name__=='__main__':main()
