# MATH Operator Library

本目录是进化算子的持久档案库，独立于可回滚的
`workflows/template`。它保存算子版本、可执行 artifact、适用条件、失败边界和
按版本绑定的配对证据。

## 当前结论

当前 `CombinatorialSymmetryDecomposer` 是 **v2 candidate**，不是已准入算子。
v2 收窄了 prompt，但尚未完成同一 artifact 版本、等调用预算的直接重评，因此
库中没有任何可用于 v2 路由的证据。candidate 不参与运行时检索；在当前状态下，
`ContextAwareOperatorRouter` 对所有问题都必须使用 generic two-stage fallback，
包括满足 `finite_geometric_symmetry` 特征的题。

库中现有实验结果全部属于已退休的 v1：

- v1 的整体直接实验没有证明相对 generic two-stage 的全局优势；
- v1 的有限旋转—反射切片仅用于形成适用条件和失败边界；
- `experiment_contextual_router_replay.py` 是基于 v1 已保存输出的反事实重放，
  只说明“如果当时采用该规则，历史输出会如何组合”，不执行 v2，也不是 v2
  的有效性证据；
- v1 evidence 均绑定 v1 artifact，并设为 `routing_eligible=false`，不能用于
  提升或路由 v2。

因此目前可以声称的是：持久化、版本绑定、失败边界和 fallback 机制已经实现；
不能声称 v2 算子有效，或上下文路由已带来准确率提升。

## 开发集与独立评测口径

`math_validate` 的 102、118，以及 `math_test` 中用于特征和边界检查的 388、399、
401、405、410、418，均参与过规则开发或机制检查。即使文件名包含 `test`，也不再
把这些题称为独立 holdout。它们只能用于：

- 特征提取回归测试；
- v2 同版本的机制重评；
- artifact 安装、fallback 和证据写入流程检查。

若要报告独立泛化结果，应另行冻结一组在规则设计、prompt 收窄和阈值选择期间均未
查看答案与逐题结果的题目，并在运行前记录数据版本、索引和评测协议。

## 路由证据要求

一条 evidence 只有同时满足以下条件才进入路由统计：

- `operator_version` 与当前记录版本相同；
- `artifact_sha256` 与当前 artifact 相同；
- `routing_eligible=true`；
- scope 与命中的 applicability rule 完全一致；
- protocol 以 `paired_equal_call` 开头；
- baseline 是 `generic_two_stage`。

当前 `finite_geometric_symmetry` 规则的最低门槛为：

- 至少 6 次 scoped observations；
- 至少 2 个 net wins，即 `wins - losses >= 2`；
- 至少 2 个不同的 scoped problems；
- 至少 2 个 **unique winning problems**。

`unique winning problems` 指至少出现过一次配对胜利的不同题目 ID。对同一道题重复
三次并取得三次 win，仍只算 1 个 unique winning problem。这个门槛防止单题的重复
采样收益被误写成跨题泛化证据。当前 v1 有限对称切片只有 1 个 unique winning
problem；即使它与 v2 同版本，也不满足当前门槛。

生命周期状态本身不会绕过证据门槛。即使记录被设为 `experimental`、`admitted` 或
`active`，没有满足上述同版本证据时，运行路由仍会 fallback。

## v2 同版本、等预算直接重评

从仓库根目录执行。直评脚本会在发起 API 调用前安装并核验库中的 candidate v2
artifact（class、prompt 和 combined semantic hash），但不会改变 candidate 状态，也
不会让运行路由调用它。下面的独立预检命令是可选的：

```bash
cd /Users/verneywang/hilda/AFlow

PYTHONDONTWRITEBYTECODE=1 .venv-aflow/bin/python - <<'PY'
from scripts.operator_library import OperatorLibrary

library = OperatorLibrary(
    "workspace/MATH/operator_library/operators.json",
    auto_create=False,
)
library.install_artifact(
    "CombinatorialSymmetryDecomposer",
    "workspace/MATH/workflows/template",
)
record = library.get("CombinatorialSymmetryDecomposer")
print("installed version:", record["version"])
print("artifact_sha256:", record["artifact"]["artifact_sha256"])
print("status:", record["status"])
PY
```

然后直接比较当前 v2 与 generic two-stage。两边均为两次顺序 LLM 调用，不使用
ScEnsemble。下列题目属于开发/机制集，不是独立 holdout：

```bash
.venv-aflow/bin/python -u experiment_operator_direct_v1.py \
  --data data/datasets/math_validate.jsonl \
  --indices 102 118 \
  --configs generic_two_stage evolved_combo \
  --num-runs 3 \
  --concurrency 2 \
  --timeout 300 \
  --output-dir workspace/MATH/operator_v2_direct_validate

.venv-aflow/bin/python -u experiment_operator_direct_v1.py \
  --data data/datasets/math_test.jsonl \
  --indices 399 \
  --configs generic_two_stage evolved_combo \
  --num-runs 3 \
  --concurrency 2 \
  --timeout 300 \
  --output-dir workspace/MATH/operator_v2_direct_test
```

要满足两个 unique winning problems 的最低门槛，v2 必须在至少两个不同题目上产生
配对净胜信号。仅反复运行 index 399 永远不够。如果上述开发/机制集没有产生两个
不同 winning problem，应保持 candidate，并扩充新的有限旋转—反射题集；不能降低
门槛或把重复运行当作新题。

## 将同版本结果写入 evidence

确认两组实验均无 timeout/error 且调用预算一致后，执行下面的导入命令。它只导入
当前 v2 直接实验的逐题配对结果；相同 `run_id` 重复导入是幂等的。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-aflow/bin/python - <<'PY'
import csv
import json
from pathlib import Path

from scripts.operator_library import OperatorLibrary, stable_problem_id

NAME = "CombinatorialSymmetryDecomposer"
LIBRARY_PATH = Path("workspace/MATH/operator_library/operators.json")
SOURCES = [
    (
        Path("workspace/MATH/operator_v2_direct_validate"),
        Path("data/datasets/math_validate.jsonl"),
        {102, 118},
    ),
    (
        Path("workspace/MATH/operator_v2_direct_test"),
        Path("data/datasets/math_test.jsonl"),
        {399},
    ),
]

library = OperatorLibrary(LIBRARY_PATH, auto_create=False)
record = library.get(NAME)
if record["version"] != 2 or record["status"] != "candidate":
    raise SystemExit(
        f"expected v2 candidate, got version={record['version']} "
        f"status={record['status']}"
    )

written = 0
for output_dir, dataset_path, allowed_indices in SOURCES:
    dataset = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
    ]
    for run in range(1, 4):
        with (output_dir / "generic_two_stage" / f"run_{run}.csv").open(
            encoding="utf-8"
        ) as handle:
            generic = {int(row["index"]): row for row in csv.DictReader(handle)}
        with (output_dir / "evolved_combo" / f"run_{run}.csv").open(
            encoding="utf-8"
        ) as handle:
            evolved = {int(row["index"]): row for row in csv.DictReader(handle)}

        for index in sorted(allowed_indices & generic.keys() & evolved.keys()):
            base_row = generic[index]
            candidate_row = evolved[index]
            if base_row["error"] or candidate_row["error"]:
                print("skip errored pair:", dataset_path, run, index)
                continue

            problem = dataset[index]["problem"]
            expected_problem_id = stable_problem_id(problem)
            if (
                base_row.get("problem_id") != expected_problem_id
                or candidate_row.get("problem_id") != expected_problem_id
            ):
                raise SystemExit(
                    f"problem identity mismatch: {dataset_path} run={run} "
                    f"index={index}"
                )
            if (
                int(base_row.get("call_count", -1)) != 2
                or int(candidate_row.get("call_count", -1)) != 2
                or base_row.get("budget_compliant", "").lower() != "true"
                or candidate_row.get("budget_compliant", "").lower() != "true"
            ):
                print("skip non-comparable budget pair:", dataset_path, run, index)
                continue

            context = library.extractor.extract(problem)
            matching_rules = [
                rule
                for rule in record["applicability_rules"]
                if library._rule_match(rule, context)
            ]
            blocked = any(
                boundary.get("hard_block", True)
                and library._boundary_match(boundary, context)
                for boundary in record["failure_boundaries"]
            )
            if not matching_rules or blocked:
                print("skip out-of-scope pair:", dataset_path, run, index)
                continue

            scope = max(matching_rules, key=library._rule_weight)["name"]
            baseline_correct = int(base_row["score"]) == 1
            operator_correct = int(candidate_row["score"]) == 1
            library.record_evidence(
                NAME,
                {
                    "source": str(output_dir),
                    "run_id": f"{dataset_path.name}:run-{run}:index-{index}",
                    "scope": scope,
                    "baseline": "generic_two_stage",
                    "protocol": "paired_equal_call_v2_direct",
                    "routing_eligible": True,
                    "wins": int(operator_correct and not baseline_correct),
                    "ties": int(operator_correct == baseline_correct),
                    "losses": int(baseline_correct and not operator_correct),
                    "problem_id": stable_problem_id(problem),
                    "problem_features": sorted(context.features),
                },
            )
            written += 1

record = library.get(NAME)
summary = library._evidence_summary(record, "finite_geometric_symmetry")
print("evidence rows processed:", written)
print("routing evidence summary:", summary)
PY
```

## Promotion

只有同版本 evidence 达到规则中的全部门槛时才提升。下面的命令会先检查门槛；任一
条件不足就终止并保持 candidate。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-aflow/bin/python - <<'PY'
from scripts.operator_library import OperatorLibrary

NAME = "CombinatorialSymmetryDecomposer"
SCOPE = "finite_geometric_symmetry"
library = OperatorLibrary(
    "workspace/MATH/operator_library/operators.json",
    auto_create=False,
)
record = library.get(NAME)
rule = next(
    item for item in record["applicability_rules"] if item["name"] == SCOPE
)
summary = library._evidence_summary(record, SCOPE)

checks = {
    "observations": summary["observations"] >= rule["min_observations"],
    "net_wins": (
        summary["wins"] - summary["losses"] >= rule["min_net_wins"]
    ),
    "unique_problems": (
        summary["unique_problems"] >= rule["min_unique_problems"]
    ),
    "unique_winning_problems": (
        summary["unique_winning_problems"]
        >= rule["min_unique_winning_problems"]
    ),
}
print("summary:", summary)
print("checks:", checks)
if record["version"] != 2 or not all(checks.values()):
    raise SystemExit("promotion denied; keep v2 as candidate")

library.set_status(
    NAME,
    "experimental",
    "v2 passed revision-bound paired equal-call contextual evidence gates",
)
library.install_artifact(NAME, "workspace/MATH/workflows/template")
print("promoted:", library.get(NAME)["status"])
PY
```

提升后再运行 `contextual_router`，用于验证真实运行入口确实选择 specialist 或按失败
边界 fallback：

```bash
.venv-aflow/bin/python -u experiment_operator_direct_v1.py \
  --data data/datasets/math_test.jsonl \
  --indices 388 399 401 405 410 418 \
  --configs generic_two_stage contextual_router \
  --num-runs 3 \
  --concurrency 2 \
  --timeout 300 \
  --output-dir workspace/MATH/operator_v2_router_mechanism
```

这仍是参与过特征开发的机制集，不是独立效果实验。最终论文结果应在另行冻结、未用于
规则开发的评测集上报告，并同时给出逐题 W/T/L、unique winning problems、调用数、
token 成本和错误数。

## 基础检查

```bash
./.venv-aflow/bin/python -m unittest discover -s tests -v
./.venv-aflow/bin/python experiment_contextual_router_replay.py
```

第二条命令只重放 v1 历史输出；它不应被写成 v2 的实验结果。
