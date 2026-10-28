# AFlow Evolution Observatory

数据可视化课程作业：用三个联动视图讲述 AFlow 工作流从固定算子、验证机制到自动进化的实验过程。

## 运行方式

本项目没有前端依赖，也不需要联网。

```bash
cd visualization_assignment
python3 -m http.server 8000
```

浏览器访问 `http://localhost:8000`。

不要直接双击 `index.html`，浏览器的本地文件安全策略可能阻止读取 JSON。

## 交互说明

1. 点击实验标签可将它设为当前实验；按住 Shift 点击可隐藏或显示一条曲线。
2. 点击折线节点，题型热力图、工作流和逐题表会切换到对应轮次。
3. 点击“题型 × 轮次”格子，选中对应轮次，并让所有实验曲线按该题型重新计算。
4. 点击工作流中的算子，会高亮所有使用该算子的轮次。
5. 点击逐题表中的题目，可查看预测、标准解以及跨轮正确/错误轨迹。

## 重新生成数据

数据已经打包在 `data/dashboard.json` 中。若项目原始实验文件发生变化，可运行：

```bash
python3 scripts/build_data.py
```

脚本只读取已有文件，不调用模型，也不重新执行实验。

## 重新生成 PDF（可选）

提交包已经包含生成好的独立 PDF，不运行此步骤也能查看作业。若需要修改文字并重建：

```bash
python3 -m pip install -r requirements.txt
python3 report/generate_report.py
```

报告脚本使用 macOS 自带的 `STHeiti` 中文字体；在其他系统重建时需要将脚本顶部的字体路径替换为本机中文字体。

## 数据来源与口径

- `data/datasets/math_validate.jsonl`：119 道题的题型、难度、题目与标准解。
- `workspace/MATH/experiment_ablation/*/results.json`：轮次和总体分数。
- 每轮 CSV：逐题预测与评分。
- 每轮 `graph.py`：算子组成与调用顺序。
- `analysis.json`：进化算子、验证统计和最终算子列表。

数据优先级为：原始逐轮记录 > 汇总分析 > 报告中的叙事注释。没有逐题 CSV 的零分轮标记为“被拒绝”，不会参与题型和难度统计。v6 续跑缺少对应逐题 CSV，因此主交互使用拥有完整原始数据的第一阶段。

本次 MATH 验证子集的119道题全部为 Level 5，因此能力切片没有强行制造无信息量的“难度对比”，而是改为题型 × 轮次矩阵。

数据审计结果见 `data/audit.json`。

## 文件结构

```text
visualization_assignment/
├── index.html
├── css/style.css
├── js/app.js
├── data/
│   ├── dashboard.json
│   └── audit.json
├── assets/                 # PDF 中使用的网页截图
├── scripts/build_data.py
├── report/generate_report.py
├── requirements.txt
└── README.md
```

## 技术说明

页面使用原生 HTML、CSS、JavaScript 和 SVG 实现，没有使用 D3、ECharts 或在线 CDN。这样可以确保 `code.zip` 在离线环境中直接运行。
