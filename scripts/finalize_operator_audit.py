import json,pathlib,hashlib,copy
from scripts.compositional_counting import exact_dp,brute_force
p=pathlib.Path('results/generated_operator_experiment_a_v1')
tasks={r['id']:r for r in json.loads((p/'inputs/formal_unsealed.json').read_text())}
rs=[json.loads(f.read_text()) for f in (p/'observations').glob('*.json')];rs=[r for r in rs if r.get('stage')=='formal']
i='e4d3bffc7350d308b0da';r=next(r for r in rs if r['id']==i and r['arm']=='reflection');q=json.loads(tasks[i]['problem']);reduced=copy.deepcopy(q);reduced['constraints']=[c for c in q['constraints'] if c['kind'] not in ('forbidden','pattern_count')]
review={'posthoc':True,'changes_primary_labels_or_scores':False,'selection':'one disclosed illustrative trace, not an estimated mechanism frequency','id':i,'problem':q,'expected':r['expected'],'answer':r['answer'],'without_two_pattern_constraints':exact_dp(reduced),'evidence':'All three Python programs truncate suffix to length 2 before comparing length-3 patterns; those matches cannot occur. Final answer equals exact count after removing forbidden and pattern_count constraints.','tool_traces':[{'step':e['step'],'sha256':e.get('tool_result',{}).get('trace',{}).get('code_sha256'),'value':e.get('tool_result',{}).get('value')} for e in r['events'] if e['function']=='python'],'operator_failures':[]}
for row in rs:
 if row['arm']=='operator' and not row['correct']:
  review['operator_failures'].append({k:row[k] for k in ('id','tier','expected','answer','error') }|{'events':[{'function':e['function'],'status':e.get('tool_result',{}).get('status'),'error':e.get('tool_result',{}).get('error'),'value':e.get('tool_result',{}).get('value')} for e in row['events']]})
(p/'formal_failure_review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2))
checks={f'config:{f}':hashlib.sha256((p/f).read_bytes()).hexdigest()==h for f,h in json.loads((p/'config_hashes.json').read_text()).items()}
a=json.loads((p/'analysis.json').read_text());checks.update({x['path']:x['passed'] for x in a['source_integrity']});checks['operator']=hashlib.sha256((p/'operator/solve.py').read_bytes()).hexdigest()==a['operator_source_sha256'];checks['formal_seal']=hashlib.sha256(pathlib.Path('results/compositional_counting_20261014_v1/sealed/formal.json').read_bytes()).hexdigest()=='25d26efb86aed49c65bc5de3df120fb2a0bab8c2f7d49cc5e7feb8f279cac0d0';checks['grid']=len(rs)==720 and len({(r['id'],r['arm']) for r in rs})==720
assert all(checks.values()),checks
(p/'final_integrity_audit.json').write_text(json.dumps(checks,indent=2))
marker='\n## 补充：正式失败证据与验证覆盖边界\n';report=(p/'report.md').read_text().split(marker)[0]
report+=marker+'''
以下审查发生于全部 720 条正式记录完成之后，未修改候选、源码、预算、正式分数或确认性统计。可复核记录见 formal_failure_review.json、posthoc_operator_checks.json。

正式 Reflection 案例 e4d3bffc7350d308b0da（Medium，n=33）输出 128184160768461，精确答案 36217669501952。三个临时程序都先把后缀截成两个元素，再比较长度为三的禁止模式 301 和计数模式 012，因此这两类匹配无法触发。独立精确求解器删除这两项约束后恰得该错误答案。这是实际轨迹支持的模式转移错误案例；单个说明性案例不用于推断总体机制比例，主分类仍保留原 Unknown。

算子组正式五次失败中，四次首次算子调用为 worker_exit_-14，后续通用 Python 未恢复正确答案（两次显式弃答、两次错误整数）；退出信号本身不足以确定数学原因。另一次 Easy 案例 f1acc88162d4f0a0627e 首次算子已返回正确的 502591113，但后续临时程序给出 0，Agent 最终提交 0。这揭示正确工具结果仍可能被后续推理覆盖。240 次算子初次调用有八次执行失败，三次在剩余预算内恢复，五次最终错误不等于五次内核数学错误。

**额外反例：有限准入测试没有证明算子对整个接口普遍正确。** 在正式评测完成后，用 n=3、字母表 [0,1,2,3]、禁止模式 ["2","3","01","11"]、首尾不同构造合法输入。精确 DP 和独立暴力枚举都得到 1（唯一序列 100），冻结算子得到 0。源码第一遍 DP 错将首尾条件应用为最后两个元素不同，并在状态空时提前返回，未进入后面的第二遍 DP。v1 生成器每题只有一个禁止模式，因此现有开发/边界/正式题未覆盖此情况。另一个已记录探针通过；两次针对性探针不是随机样本，不能解释为 50% 错误率。

因此 RQ5 只能支持本次长度/组合 OOD 的测量结果；RQ7 支持继续研究“可验证算子能否产生收益”，同时必须扩展多禁止模式、状态提前终止及属性测试的验证覆盖。预设 v1 强增益门槛已达到，但不能据此声称自动发现机制已有效，或此算子已获通用正确性保证。保持本轮冻结版本，后续改进须单独版本及新测试集。

最终完整性检查见 final_integrity_audit.json：720 个唯一任务–方法组合、冻结配置、算子源码与原正式题封存哈希全部通过。此次实际账单仍待用户核对；已知 Token 估算总计 ¥14.594722，有两次请求用量未知，含预留的预算台账为 ¥14.756154，本地计算与人工未定价。
'''
(p/'report.md').write_text(report)
print({'all_integrity_checks_pass':all(checks.values()),'pattern_omission_count':review['without_two_pattern_constraints'],'formal_wrong_answer':r['answer']})
