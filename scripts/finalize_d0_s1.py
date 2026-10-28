"""Offline-only recovery/report; never imports an API client."""
import asyncio,json,hashlib
from pathlib import Path
from scripts.d0_s01_runtime.evaluation import evaluate_task
P=Path('results/experiment_d0_s1_20261019_v1');O=Path('results/experiment_d0_s01_20261019_v1')
def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
async def main():
 ledger=load(P/'ledger.json');backend=load(O/'s1_backend_tasks.json');public=load(O/'s1_public_tasks.json');order=load(P/'protocol.json')['order'];rows=[]
 for tid in order:
  d=P/'tasks'/tid;calls=[c for c in ledger['calls'] if c['task']==tid]
  if not calls:rows.append({'id':tid,'status':'NOT_RUN_ENGINEERING_STOP','split':'ENGINEERING_SMOKE'});continue
  row=load(d/'result.json') if (d/'result.json').exists() else {'id':tid,'status':'SUBMITTED_REPORT_INTERRUPTED','source':public[tid]['source'],'split':'ENGINEERING_SMOKE','calls':len(calls),'input_tokens':sum(c['usage']['prompt_tokens'] for c in calls),'output_tokens':sum(c['usage']['completion_tokens'] for c in calls),'cost_upper_cny':sum(c['cost_upper_cny'] for c in calls),'self_tests':len(list(d.glob('self_test_*.json')))}
  if (d/'hidden_final.json').exists():row['verdict']=load(d/'hidden_final.json')['verdict']
  r=await evaluate_task((d/'program_0.py').read_text(),backend[tid],role='diagnostic',log_dir=d/'offline_recovery',task_wall=75);save(d/'offline_first_recovered.json',r);row['first_verdict']=r['verdict']
  rows.append(row)
 save(P/'recovered_results.json',rows)
 before=load(P/'protected_before.json');changed=[p for p,h in before.items() if not Path(p).exists() or sha(Path(p))!=h];save(P/'protected_after.json',{'checked':len(before),'changed':changed})
 pin=sum(c['usage']['prompt_tokens'] for c in ledger['calls']);pout=sum(c['usage']['completion_tokens'] for c in ledger['calls']);hits=sum(c['usage'].get('prompt_cache_hit_tokens',0) for c in ledger['calls'])
 stats={'physical_requests':len(ledger['calls']),'input_tokens':pin,'output_tokens':pout,'cache_hit_tokens':hits,'conservative_peak_no_cache_cny':ledger['committed_upper_cny'],'peak_cache_aware_cny':((pin-hits)*2+hits*.04+pout*8)/1e6,'offpeak_cache_aware_cny':((pin-hits)+hits*.02+pout*4)/1e6,'unknown_delivery_requests':sum(c['status']!='COMPLETED' for c in ledger['calls']),'invoice':'PENDING_USER_PROVIDER_BILL','90_task_protocol_max_cny':90*.105536,'540_task_protocol_max_cny':540*.105536,'empirical_forecast':'NOT_IDENTIFIABLE: only 2 tasks attempted, no TACO, delivery workflow interrupted'}
 save(P/'cost_summary.json',stats)
 save(P/'acceptance_gate.json',{'status':'S1_NOT_ACCEPTED','reason':['conservative input estimate stopped first task','forced submit tool choice not followed in first task','offline_first role rejected; reporting interrupted'],'registered':13,'attempted':2,'submitted':1,'unrun':11,'S2_Prepare':'NOT_RUN','S2_S4':'NOT_RUN','API_retry':0})
 report=f'''# D0-S1 真实 API 冒烟报告\n\n结论：**S1_NOT_ACCEPTED**。本轮已停止，不进入任务池构建或 S2–S4。工程失败不能解释为算法语义失败，也不能据此评价失败驱动机制。\n\n## 协议与范围\n\nRun ID：{P.name}。使用 S0.1 已准入的13题及冻结顺序，预注册分母13；首9题工程门槛未到达。2题启动、1题最终提交、11题因工程阻断未运行。原隔离任务没有恢复或替换。所有任务保持 ENGINEERING_SMOKE 身份。预算为每题4次调用、输入20000/输出8192 Token、自测10秒、在线180秒，阶段费用上限2元；SDK重试0。首调用强制公开自测，属于工程路径，不能用于自然失败率估计。隐藏评分75秒单独计时。\n\n历史 S0.1 验证器核验2198产物通过；本轮保护清单核验{len(before)}文件，变更{len(changed)}。旧驱动及冻结结果未修改。\n\n## 逐题结果\n\n|任务|状态|最终隐藏评测|首次程序离线评测|\n|---|---|---|---|\n'''
 for row in rows:report+=f"|{row['id']}|{row['status']}|{row.get('verdict','未提交/未执行')}|{row.get('first_verdict','未执行')}|\n"
 report+=f'''\n## 工程阻断与证据\n\n1. 首题 d0-count-v2-5cd14c2da1860d18：第2次请求已要求 submit，但 API 返回 self_test。驱动没有将其自动视为最终交付；随后 UTF-8 字节加2048的保守输入预留超出剩余输入额度，提前停止。实际仅使用4551输入Token，故这是预算估计和最终交付控制缺陷，不是已证实的数学能力不足。该返回的第二份源码未实际自测，反馈记录可复核。\n2. lcb-v6-3768：真实自测—反馈—最终锁定—隐藏判题链路完成，最终 PASS。随后研究者侧首次程序诊断传入 offline_first，而冻结运行器只接受 self_test/hidden/diagnostic，触发 ValueError 并中断主报告。属于新驱动角色适配问题；既有执行隔离未关闭。首次程序已使用合法 diagnostic 角色离线恢复评分，不重跑模型。\n3. v1对首题预算停止未立即全局停止，继续启动了第二题。这是停止控制缺陷，不能称为完整工程验收。第二题后的异常已终止整个进程。\n\n## 修复与验证边界\n\n保留v1源文件与协议哈希；另存 scripts/experiment_d0_s1_v2.py 修复草稿及逐行差异：最终阶段仅暴露 submit 工具，诊断使用 diagnostic，评分前保存任务检查点，任何未提交单元立即停止。v2入口明确禁止直接真实运行。语法检查通过；既有沙箱公开自测、无判题反馈、重复提交拒绝、提交后工具关闭等离线检查通过。**没有完成预算估计器与强制提交的新真实集成验收，v2不视为已接受**；重新运行需要新协议和阶段放行，不能续跑或覆盖v1原单元。\n\n模型仅接收 s1_public_tasks.json；工具参数仅传模型自造输入，评分期望留在父进程。公开工具返回执行输出，不返回隐藏期望或PASS判定。隐藏评分发生于锁定后；恢复评分独立保存于 offline_recovery，不回传模型。该证据支持已运行路径隔离，但尚未覆盖TACO真实API与全部13题。\n\n## Token、时间与费用\n\n共{len(ledger['calls'])}次物理请求，输入{pin}、输出{pout} Token，缓存命中{hits}输入Token；无重试、无未知交付请求。逐请求时延、模型标识、完整usage见ledger.json及response文件。保守峰时无缓存账本合计 **{stats['conservative_peak_no_cache_cny']:.6f}元**；按缓存字段计算峰时约{stats['peak_cache_aware_cny']:.6f}元、谷时约{stats['offpeak_cache_aware_cny']:.6f}元。此处是用量计价，**服务商实际账单尚待核对**，不引用历史4元/4.1元比例。价格依据[DeepSeek官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，模型实际返回标识逐次保存，别名不代表权重不可变。\n\n仅2题启动且没有TACO，无法据此可靠外推90/540题实测成本。若按每题预算全部用满、峰时无缓存计价：90题模型上限约 **9.50元**，540题约 **56.99元**，不含新协议下重试/未知用量的额外预留、参考求解、本地资源、人工审查及机制诊断。它是现有单元上限的算术推演，不是540题授权或真实费用预测。任务池准入与工程时限尚未充分校准。\n\n## 判断与后续门槛\n\n已证明：真实 API 可产生代码并调用原生隔离自测；其中一题能锁定提交并通过隐藏评测；请求、用量、源码与工具输出可保存。\n\n已反驳：当前新驱动已具备可靠最终交付和完整诊断报告能力。\n\n未知：三来源端到端稳定性、可靠的90/540题经验成本、自然失败率及可复用机制。\n\n因此当前不能安全宣布进入规模化运行。先离线改进预算估计与提交控制，并以新协议重新验收；本轮到此停止。没有运行S2-Prepare、S2、S3或S4。\n\n## 复现与产物\n\n历史核验：`.venv/bin/python -m scripts.verify_d0_s01`。离线报告恢复：`.venv/bin/python -m scripts.finalize_d0_s1`（不调用API）。v1入口若发现ledger会拒绝再次执行。协议、原请求、原响应、每次源码、自测输出、最终锁定源码、隐藏评测与离线恢复均保存在本目录。protected_before/after记录历史完整性，artifact_manifest记录本轮文件哈希。\n'''
 (P/'D0-S1_真实API冒烟报告.md').write_text(report)
 save(P/'artifact_manifest.json',{str(p):sha(p) for p in P.rglob('*') if p.is_file() and p.name!='artifact_manifest.json'})
 print(json.dumps(stats,ensure_ascii=False));print('S1_NOT_ACCEPTED; no additional API')
if __name__=='__main__':asyncio.run(main())
