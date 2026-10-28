"""Reproducible offline engineering acceptance with immutable per-test artifacts."""
import argparse,hashlib,json,platform,sys,time,unittest,uuid
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from scripts.offline_loop_protocol import atomic_json,read_json,file_hash
from scripts.operator_runtime.history import replay,TaskRoles

BASE=Path('engineering_stabilization')

class RecordedResult(unittest.TextTestResult):
 def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.items=[];self.started={}
 def startTest(self,test):self.started[test.id()]=time.monotonic();super().startTest(test)
 def addSuccess(self,test):self.items.append(dict(test=test.id(),status='PASSED',duration_seconds=time.monotonic()-self.started[test.id()]));super().addSuccess(test)
 def addFailure(self,test,err):self.items.append(dict(test=test.id(),status='FAILED',trace=self._exc_info_to_string(err,test),duration_seconds=time.monotonic()-self.started[test.id()]));super().addFailure(test,err)
 def addError(self,test,err):self.items.append(dict(test=test.id(),status='ERROR',trace=self._exc_info_to_string(err,test),duration_seconds=time.monotonic()-self.started[test.id()]));super().addError(test,err)
 def addSkip(self,test,reason):self.items.append(dict(test=test.id(),status='SKIPPED',reason=reason));super().addSkip(test,reason)


def run(full=True):
 run_id='acceptance_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8];out=BASE/'runs'/run_id;out.mkdir(parents=True,exist_ok=False)
 atomic_json(BASE/'latest_run.json',dict(run_id=run_id,path=str(out)))
 before=read_json(BASE/'runs/20261015_v1/history_before.json')
 # This acceptance run never instantiates an unmocked external API client.
 suite=unittest.defaultTestLoader.discover('tests') if full else unittest.defaultTestLoader.loadTestsFromName('tests.test_engineering_stabilization')
 with (out/'tests.log').open('w') as stream,patch('openai.AsyncOpenAI',side_effect=AssertionError('Real API disabled for engineering acceptance')):
  result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=RecordedResult).run(suite)
 atomic_json(out/'test_results.json',result.items)
 details,diff=replay(out)
 changed=[p for p,h in before.items() if not Path(p).exists() or file_hash(Path(p))!=h]
 now_files={str(p) for root in ['results/generated_operator_experiment_a_v1','results/experiment_b0_20261014_v1','results/compositional_counting_20261014_v1','results/experiment_a_calibration_20261014_v3'] for p in Path(root).rglob('*') if p.is_file()}
 extra=sorted(now_files-set(before));atomic_json(out/'historical_integrity.json',dict(original_file_count=len(before),changed=changed,added=extra,unchanged=not changed and not extra))
 roles=TaskRoles(out/'task_roles.json')
 # Discovery reuse is explicit, all historical hidden tasks marked already used.
 for source in ['results/generated_operator_experiment_a_v1/inputs/formal_unsealed.json','results/experiment_b0_20261014_v1/sealed/hidden.json']:
  for r in read_json(Path(source)):roles.add(r['problem'],'HISTORICAL_TEST','historical',source)
 roles.add(read_json(Path('results/experiment_b0_20261014_v1/discovery/task.json'))['problem'],'DISCOVERY','B0','A historical failure')
 files=list(Path('scripts/operator_runtime').glob('*.py'))+[Path('scripts/engineering_acceptance.py'),Path('tests/test_engineering_stabilization.py')]
 atomic_json(out/'source_hashes.json',{str(p):file_hash(p) for p in files})
 atomic_json(out/'environment.json',dict(python=sys.version,platform=platform.platform(),external_api_disabled=True))
 costs=dict(real_api_requests=0,known_real_api_tokens=0,unknown_real_api_usage_requests=0,actual_api_cost_cny=0,estimated_api_cost_cny=0,mock_attempts_not_billable=True,local_compute_cost_cny=None)
 atomic_json(out/'costs.json',costs)
 faults=[r for r in result.items if 'test_engineering_stabilization.' in r['test']]
 success=result.wasSuccessful() and not changed and not extra and not diff and not result.skipped
 summary=dict(run_id=run_id,passed=success,tests_run=result.testsRun,successes=sum(r['status']=='PASSED' for r in result.items),failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),fault_injection_tests=len(faults),fault_injection_passed=sum(r['status']=='PASSED' for r in faults),history_files_unchanged=len(before)-len(changed),historical_discrepancies=len(diff),costs=costs)
 atomic_json(out/'summary.json',summary)
 report(summary,out)
 print(json.dumps(summary,ensure_ascii=False));return 0 if success else 1


def report(summary,out):
 ok=summary['passed'];n=summary['fault_injection_tests'];p=summary['fault_injection_passed']
 text=f'''# Engineering Stabilization 验收报告

工程范围：仅基础设施修复，无研究算子生成、Benchmark 扩展或 A/B0 评测重跑。验收运行 `{summary['run_id']}`。完整逐项记录：[test_results.json]({out.resolve()}/test_results.json)，详细日志：[tests.log]({out.resolve()}/tests.log)。

## 1. 修复摘要

新增 `scripts/operator_runtime/`，复用已有模型配置、native tools、AST 白名单及协议；历史冻结脚本保留原样。稳定入口提供兼容的 Transport.call / execute / solve 形状，算子模式额外强制明确注册身份。未默认改写任何历史实验路径；后续新运行须显式使用稳定入口。

- API：最多2次可恢复基础设施重试、指数退避、Retry-After、唯一物理请求ID、独立逻辑操作记录；未知交付持有非零预留，恢复时不会盲目重发未完成操作。
- 生成：初始真实源码、解析成功响应总数、源码修复次数和物理请求分别计数；基础设施中断不伪装算法失败。
- 准入：静态签名/依赖检查，强制八类协议测试全部通过，状态由执行证据驱动，拒绝/隔离版本不能进入可信加载；冻结源码、验证器、测试协议、生成来源哈希。
- 工具：执行状态与输出验证状态分离，未做独立验证默认 NOT_CHECKED；确定性类型/资源错误不转换为整数。每任务新进程，答案来源保留局部错误与后续恢复。

## 2. P0 验收情况

| 项目 | 验证方式 | 状态 |
|---|---|---|
| 请求/生成/修复计数分离 | 初次连接失败、真实错误代码后修复 | 见逐项测试 |
| 有界重试/未知状态/预算 | 连接耗尽、超时、取消、Retry-After、限额、并发 | 见逐项测试 |
| 准入与注册 | 全对、全错、部分错、超时、缺失覆盖、重复版本 | 见逐项测试 |
| 执行与正确性分离 | 正常错误整数不标 VERIFIED，错误类型拒绝 | 见逐项测试 |
| 错误传播与回退 | Mock Agent + 真实准入算子，覆盖/恢复 | 见逐项测试 |
| 冻结与隔离 | 源码篡改、quarantine、注册入口拒绝 | 见逐项测试 |

工程故障注入 **{p}/{n}** 通过；总体验收状态 **{'通过' if ok else '未通过，保留失败日志，不放宽验收'}**。

## 3. P1 验收情况

已实现阶段事件、统一错误枚举、确定事实与机制假设分离、答案来源、分阶段费用、成本/正确答案、历史适配器及任务角色账本。源码/配置/验证器标识可复现。UNKNOWN 保留；不将未知进程信号推定为数学或内存机制。

执行隔离：macOS sandbox-exec 真正进程策略，禁止未授权文件内容读取、所有文件写入、网络和 fork；只读解释器/系统运行时/隔离 worker，环境不继承 API 密钥。无 OS 隔离时 fail closed。内存为定时 RSS 检测（macOS 非硬地址空间保证）；CPU RLIMIT、外部墙钟超时、递归和有界输出另行限制。白名单不是完整安全沙箱。

## 4. 故障注入与回归结果

完整自动化测试 {summary['tests_run']} 项，成功 {summary['successes']}，失败 {summary['failures']}，错误 {summary['errors']}，跳过 {summary['skipped']}。故障注入 {p}/{n}；每项结果/耗时/失败堆栈保存在本次运行目录，不覆盖早期 red baseline / iteration 日志。

原生 OS 隔离测试绕过 AST 白名单，以受信测试代码尝试读取合成隐藏文件、写合成历史文件、连接本地端口；所有操作必须被 OS 拒绝。没有访问真实隐藏答案来调试生成代码。

## 5. 历史兼容性

原 {summary['history_files_unchanged']} 个历史文件哈希保持不变；A 720 条、B0 132 条原独立评测记录只读解析，分母和正确数重建到本次工程目录；差异 {summary['historical_discrepancies']} 项。旧 UNKNOWN 分类保留。历史差异报告和重建CSV均不写回实验目录。

## 6. 预算与成本

外部 API 请求0，已知 Token0，未知用量0，API实际/估算费用均¥0。Mock 物理请求只作故障注入，不计真实账单。当地计算与工程分析时间未定价，不宣称完整成本为零。

新运行按请求/Token/墙钟/保守费用预留硬限额；未知用量保持预留。供应商真实账单无法从客户端单方面精确保证，实际费用字段保持独立。若供应商报告用量超出预留，停止后续请求并保存超限事实，不倒填或抹去请求。

## 7. 已知剩余边界

- 仅实现并验证 macOS 原生隔离；其他系统拒绝执行，须配置成熟隔离后再使用。
- macOS RSS限制为监测型；严格对抗性内存安全还需容器/VM等平台。本轮未建设新沙箱平台。
- 服务商幂等支持未经确认，不发送自创幂等保证；本地 UUID 仅用于关联，未知交付重试仍可能产生重复费用。
- 准入仅保证通过所配置测试，不能证明全部输入正确。样例/性质/变形与交叉校验质量由具体协议负责；新版本需重验。
- 可选运行时验证器必须由公开协议声明、计费并在各组同等提供，不能偷偷传入隐藏参考。本轮未接入研究 oracle 或 LLM可信度判定。
- Archived A/B0 驱动保留旧行为供历史复现；新实验只能使用稳定入口。未迁移/重跑历史研究。

## 8. 后续准备状态（A–G）

A. 新入口中基础设施失败不消耗源码修复次数；未知交付/预算耗尽为 Infrastructure Interrupted。
B. 未通过全部配置验证的版本不能可信注册/加载；不提供隐式 unsafe 绕过。
C. SUCCESS 仅代表执行；本次结果未独立检查为 NOT_CHECKED，不自动宣称正确。
D. 可追踪算子值、验证状态、通用工具重算、覆盖、最终提交及评测后正确性。
E. 网络、模型响应、代码、参数、资源、交付和未知错误分别记录，可同时保留多阶段错误。
F. 历史文件未覆盖；独立哈希、只读重建和差异报告已保存。
G. {'P0工程条件已具备；后续探索仍须新协议、新运行ID、任务隔离、准入验证和预算冻结。' if ok else '尚不具备；先修复当前失败验收项，不启动新研究。'}

复现：`.venv/bin/python -m scripts.engineering_acceptance`。macOS 需允许测试父进程应用原生沙箱；桌面外层限制若拒绝嵌套沙箱，应授权本工程测试命令执行，子程序隔离保持开启。首次失败基线在 runs/20261015_v1/red_baseline.log。
'''
 (BASE/'engineering_stabilization_report.md').write_text(text)
 (out/'engineering_stabilization_report.md').write_text(text)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--targeted',action='store_true');args=parser.parse_args();raise SystemExit(run(not args.targeted))
