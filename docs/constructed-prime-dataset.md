# 构造素数题：校准、双重核验、正式冻结

实现入口 `scripts.constructed_prime_dataset`。本模块只实现数据与基线难度校准；不把构造题描述为自然 MATH，也不假称后续可执行算子生成和 operational 审核已完成。

## 数据及核验

三个候选难度：small=4 个可用数字、最多 2 位；medium=5 个数字、最多 3 位；large=6 个数字、最多 4 位。长度从 1 开始，禁止前导零。主要任务禁止数字重复并计数素数；边界任务分别允许重复计数素数、改问偶数、改问合数。

每道题采用两个独立实现核对完整答案集合，而非只比数量：

1. 枚举排列（重复边界采用笛卡尔积），试除法判断素数。
2. 埃氏筛生成素数表，扫描正整数并按其数字集合、长度和重复条件过滤。

不调用已安装专项工具。0 不为正整数，1 既不是素数也不是合数；前导零、数值去重及重复边界都有已知答案测试。两种实现不一致即拒绝生成。答案集合哈希、参数及双重核验方法写入每题记录，参考解答只给最终 boxed 答案，不向后续生成器提供旧算法实现。

## 分组隔离

以排序后的数字集合为 group/family ID。相同数字集合即使不同最大位数、题面表述或边界条件，也不跨校准、发现、开发、确认及边界池使用。按种子打乱组合，排除 oracle 答案为 0 的平凡题，再按配额选择；不按模型结果挑选正式题。

默认检查 workspace/MATH 和 results 中已知 JSONL 的题面和参数，排除历史数字集合。可通过重复 --exclude-data 补充外部历史。此检查不是所有历史格式的完备识别，也不代表不同集合之间没有共享数学结构；所有题本来就属于同一构造任务族。

## 两阶段流程

第一阶段 prepare-calibration：每档 2 题，每题重复 2 次，共 6 题、12 次两阶段作答、最多 24 个 API 请求。题目、答案、模型配置、资源限制、选档规则、源码副本及哈希在第一条请求之前冻结。

第二阶段 freeze：必须先有完整、可核对原始请求的校准记录。每档至少 4 次有效观察，正确完成率在 25%～75% 之间，且至少一题失败；选择符合条件的最难档。答错、输出截断、超时算 operational failure；网络等基础设施故障不计入题目难度，导致有效观察不足时该档不能选中。全对、全错都不自动合格；无合格档则拒绝正式冻结，需另开明确的新校准，不能篡改原配置或强行选档。

这只是很小样本的难度筛选，不是统计充分的能力估计，也不证明同一种数学推理错误。

选档后生成 20 道新的组间无重复题：发现 6、开发 4、确认 4、开发边界 3、确认边界 3。两组边界各包含上述三种类型。正式数据与校准题隔离，不能利用正式确认结果调难度。

## 冻结的资源和判定规则

本次配置：DeepSeek 别名 deepseek-v4-flash，thinking disabled，每次请求最多输出 8192 Token，每次两阶段求解最多 180 秒。模型 temperature/top_p/endpoint 也保存并核对；正式冻结必须保持校准的执行模型和资源设置。校准 max_requests=24；正式计划 max_requests=500，当前两候选、三重复方案保守规划上界 419 请求。请求上限不是美元上限。

正式发现门槛：至少 3 道不同题，每道至少 2 次 operational failure，网络故障不充当失败证据。开发题选择一个候选，再冻结该候选进行一次确认；不允许确认失败后改试第二名。确认要求至少 4 道目标题、至少 2 道不同题获胜、净胜至少 2 次、边界正确率不下降，且所有计划配对没有基础设施故障。候选最多 2 次 LLM 调用加最多 2 次回退调用，后续 operational runner 必须实际执行并检查这些预算/门槛，不能只引用 manifest。

这里冻结的是正式数据协议；当前旧的语义错答审核入口不能直接用它将超时升级为数学机制证据。可执行算子生成和 operational 闭环执行是后续实现，不在本次数据构造步骤中伪造完成。

## 运行

本次校准已准备在 `results/constructed_prime_20261009_setup/calibration`。下一条命令会产生真实调用费用：

```sh
.venv/bin/python -m scripts.constructed_prime_dataset run-calibration \
  --output-dir results/constructed_prime_20261009_setup/calibration --allow-api
```

免费查看并验证校准结果：

```sh
.venv/bin/python -m scripts.constructed_prime_dataset status \
  --output-dir results/constructed_prime_20261009_setup/calibration
```

实际校准合格后，免费生成并冻结正式数据（当前未运行）：

```sh
.venv/bin/python -m scripts.constructed_prime_dataset freeze \
  --calibration results/constructed_prime_20261009_setup/calibration \
  --config results/constructed_prime_20261009_setup/formal_config.json \
  --output-dir results/constructed_prime_20261009_setup/formal
```

正式 manifest 绑定校准原始请求快照、题池、答案、预算、规则、模型配置及代码。所有 prepare/freeze 拒绝覆盖；恢复时检验哈希，完成的请求不重复执行。确认题在本地文件中隔离，属于输入可见性边界，不是操作系统权限沙箱。
