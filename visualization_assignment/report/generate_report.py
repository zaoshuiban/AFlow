#!/usr/bin/env python3
"""Generate the illustrated AFlow data-story PDF.

The report is produced with ReportLab so it can be rebuilt without browser or
office software. Dashboard screenshots are generated during visual QA.
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
DATA_PATH = ROOT / "data" / "dashboard.json"
ASSET_DIR = ROOT / "assets"
OUTPUT = PROJECT_ROOT / "output" / "pdf" / "AFlow数据故事.pdf"

NAVY = colors.HexColor("#13233F")
INK = colors.HexColor("#17223B")
MUTED = colors.HexColor("#657087")
PAPER = colors.HexColor("#F3F0E9")
CREAM = colors.HexColor("#FFFDF8")
LINE = colors.HexColor("#D8D3C8")
TEAL = colors.HexColor("#22A06B")
MINT = colors.HexColor("#61DDAA")
ORANGE = colors.HexColor("#F6903D")
GOLD = colors.HexColor("#F6BD60")
RED = colors.HexColor("#E86452")
PURPLE = colors.HexColor("#9661BC")


pdfmetrics.registerFont(TTFont("Heiti", "/System/Library/Fonts/STHeiti Medium.ttc"))
pdfmetrics.registerFont(TTFont("Arial", "/System/Library/Fonts/Supplemental/Arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"))


def load_data():
    with DATA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    name="CNBody", fontName="Heiti", fontSize=9.4, leading=15, textColor=INK,
    spaceAfter=7, wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="CNLead", parent=styles["CNBody"], fontSize=11, leading=18, textColor=MUTED,
    spaceAfter=12,
))
styles.add(ParagraphStyle(
    name="CNH1", fontName="Heiti", fontSize=24, leading=31, textColor=INK,
    spaceAfter=12, wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="CNH2", fontName="Heiti", fontSize=15, leading=21, textColor=INK,
    spaceBefore=5, spaceAfter=8, wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="Kicker", fontName="Arial-Bold", fontSize=7.5, leading=10, textColor=ORANGE,
    tracking=1.2, spaceAfter=5,
))
styles.add(ParagraphStyle(
    name="Small", fontName="Heiti", fontSize=7.6, leading=12, textColor=MUTED, wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="Tiny", fontName="Heiti", fontSize=6.6, leading=9.5, textColor=MUTED, wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="WhiteTitle", fontName="Heiti", fontSize=29, leading=37, textColor=colors.white,
    spaceAfter=10, wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="WhiteLead", fontName="Heiti", fontSize=11, leading=18, textColor=colors.HexColor("#D6DFE9"),
    spaceAfter=14, wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="Cell", fontName="Heiti", fontSize=7.5, leading=11, textColor=INK, wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="CellWhite", fontName="Heiti", fontSize=8, leading=12, textColor=colors.white, wordWrap="CJK",
))


def P(text, style="CNBody"):
    return Paragraph(text, styles[style])


def section(kicker, title, lead=None):
    items = [P(kicker, "Kicker"), P(title, "CNH1")]
    if lead:
        items.append(P(lead, "CNLead"))
    return items


def bullet(text):
    return P(f"<font color='#22A06B'>●</font>  {text}", "CNBody")


def callout(title, text, color=TEAL):
    table = Table([[P(title, "CNH2"), P(text, "CNBody")]], colWidths=[42 * mm, 125 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EEF4F1")),
        ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#C9DCD4")),
        ("LINEBEFORE", (0, 0), (0, 0), 4, color),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return table


def metric_cards(values, widths=None, dark=False):
    widths = widths or [42 * mm] * len(values)
    cells = []
    for value, label in values:
        style = "CellWhite" if dark else "Cell"
        color = "#F6BD60" if dark else "#17223B"
        cells.append(P(f"<font name='Arial-Bold' size='18' color='{color}'>{value}</font><br/>{label}", style))
    table = Table([cells], colWidths=widths)
    bg = colors.HexColor("#1C3150") if dark else CREAM
    border = colors.HexColor("#334A68") if dark else LINE
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), .6, border),
        ("INNERGRID", (0, 0), (-1, -1), .6, border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    return table


class PipelineFlow(Flowable):
    def __init__(self, width=500, height=96):
        super().__init__()
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        labels = [
            ("原始实验", "JSON / CSV / graph.py"),
            ("统一数据层", "82轮 + 119题"),
            ("联动视图", "曲线 / 矩阵 / 工作流"),
            ("数据故事", "证据 / 结论 / 局限"),
        ]
        box_w, gap, y = 108, 22, 22
        for i, (title, sub) in enumerate(labels):
            x = i * (box_w + gap)
            c.setFillColor([NAVY, colors.HexColor("#234B57"), TEAL, ORANGE][i])
            c.roundRect(x, y, box_w, 54, 8, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont("Heiti", 9)
            c.drawCentredString(x + box_w / 2, y + 34, title)
            c.setFillColor(colors.HexColor("#E7ECEA"))
            c.setFont("Heiti", 6.8)
            c.drawCentredString(x + box_w / 2, y + 17, sub)
            if i < len(labels) - 1:
                c.setStrokeColor(colors.HexColor("#A8B2B8"))
                c.setLineWidth(1.2)
                c.line(x + box_w + 4, y + 27, x + box_w + gap - 4, y + 27)
                c.line(x + box_w + gap - 8, y + 31, x + box_w + gap - 4, y + 27)
                c.line(x + box_w + gap - 8, y + 23, x + box_w + gap - 4, y + 27)


class ComparisonBars(Flowable):
    def __init__(self, experiments, width=500, height=236):
        super().__init__()
        self.experiments = experiments
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        left, right, top, bottom = 92, 12, 18, 24
        plot_w = self.width - left - right
        plot_h = self.height - top - bottom
        x0 = .72
        for tick in [.72, .76, .80, .84, .88]:
            x = left + (tick - x0) / (.88 - x0) * plot_w
            c.setStrokeColor(colors.HexColor("#E4E0D7"))
            c.line(x, bottom, x, bottom + plot_h)
            c.setFillColor(MUTED)
            c.setFont("Arial", 6.5)
            c.drawCentredString(x, 7, f"{tick*100:.0f}%")
        row_h = plot_h / len(self.experiments)
        for index, exp in enumerate(self.experiments):
            y = bottom + plot_h - (index + .72) * row_h
            score = exp["summary"]["best_score"]
            c.setFillColor(INK)
            c.setFont("Heiti", 7.2)
            c.drawRightString(left - 8, y + 1, exp["short"])
            bar_w = max(1, (score - x0) / (.88 - x0) * plot_w)
            c.setFillColor(colors.HexColor(exp["color"]))
            c.roundRect(left, y - 4, bar_w, 10, 5, fill=1, stroke=0)
            c.setFillColor(INK)
            c.setFont("Arial-Bold", 7)
            c.drawString(min(left + bar_w + 5, self.width - 30), y - 1, f"{score*100:.1f}")


class EvolutionFlow(Flowable):
    def __init__(self, width=500, height=128):
        super().__init__()
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        stages = [
            ("v4", "首次闭环", "验证型重复", PURPLE),
            ("v5", "Prompt引导", "策略型但脆弱", ORANGE),
            ("v6", "错误反馈", "生成成功但少采用", RED),
        ]
        x_positions = [12, 180, 348]
        for i, (version, title, result, color) in enumerate(stages):
            x = x_positions[i]
            c.setFillColor(colors.HexColor("#F7F3EB"))
            c.setStrokeColor(color)
            c.setLineWidth(1.6)
            c.roundRect(x, 24, 138, 82, 10, fill=1, stroke=1)
            c.setFillColor(color)
            c.setFont("Arial-Bold", 16)
            c.drawString(x + 13, 78, version)
            c.setFillColor(INK)
            c.setFont("Heiti", 9)
            c.drawString(x + 13, 58, title)
            c.setFillColor(MUTED)
            c.setFont("Heiti", 7.2)
            c.drawString(x + 13, 39, result)
            if i < 2:
                c.setStrokeColor(colors.HexColor("#A8B2B8"))
                c.line(x + 143, 65, x + 163, 65)
                c.line(x + 159, 69, x + 163, 65)
                c.line(x + 159, 61, x + 163, 65)


class TrajectoryGrid(Flowable):
    def __init__(self, data, qid="q073", width=500, height=184):
        super().__init__()
        self.data, self.qid = data, qid
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        left, top = 98, self.height - 18
        for row_index, exp in enumerate(self.data["experiments"]):
            y = top - row_index * 22
            c.setFillColor(INK)
            c.setFont("Heiti", 7.1)
            c.drawRightString(left - 8, y + 2, exp["short"])
            values = []
            for rnd in exp["rounds"]:
                outcome = next((x for x in rnd["outcomes"] if x["q"] == self.qid), None)
                if outcome is not None:
                    values.append((rnd["round"], outcome["score"] > 0))
            max_round = max((r for r, _ in values), default=1)
            for round_no, success in values:
                x = left + (round_no - 1) / 15 * (self.width - left - 12)
                c.setFillColor(TEAL if success else colors.HexColor("#E8C1B8"))
                c.roundRect(x, y - 5, 13, 13, 3, fill=1, stroke=0)
                c.setFillColor(colors.white if success else RED)
                c.setFont("Arial-Bold", 5.7)
                c.drawCentredString(x + 6.5, y - 1, str(round_no))
        c.setFillColor(MUTED)
        c.setFont("Heiti", 6.5)
        c.drawString(left, 4, "绿色：答对；浅红：答错；方格数字：真实轮次。")


def standard_table(rows, widths, header=True, font_size=7.3):
    converted = []
    for r_index, row in enumerate(rows):
        cell_style = "CellWhite" if header and r_index == 0 else "Cell"
        converted.append([P(str(value), cell_style) for value in row])
    table = Table(converted, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), .45, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if header:
        cmds.extend([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ])
    for row_index in range(1 if header else 0, len(rows)):
        if row_index % 2 == 0:
            cmds.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#F6F2EA")))
    table.setStyle(TableStyle(cmds))
    return table


def page_decor(canvas, doc):
    canvas.saveState()
    if doc.page == 1:
        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
    else:
        canvas.setFillColor(PAPER)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 11 * mm, A4[0], 11 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Arial-Bold", 6.5)
        canvas.drawString(18 * mm, A4[1] - 7 * mm, "AFLOW EVOLUTION OBSERVATORY")
        canvas.setFillColor(MUTED)
        canvas.setFont("Heiti", 6.5)
        canvas.drawString(18 * mm, 10 * mm, "数据可视化课程作业 · MATH 119题 · 自有研究实验")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"{doc.page:02d}")
    canvas.restoreState()


def build_story(data):
    story = []
    exps = {item["id"]: item for item in data["experiments"]}

    # Page 1: cover
    story += [
        Spacer(1, 11 * mm),
        P("DATA STORY · AFLOW / MATH", "Kicker"),
        P("更多算子，<font color='#F6BD60'>不一定更聪明</font>", "WhiteTitle"),
        P("AFlow 工作流自动进化的收益、失败与瓶颈", "WhiteLead"),
        Image(str(ASSET_DIR / "dashboard_hero.jpg"), width=174 * mm, height=97.9 * mm),
        Spacer(1, 7 * mm),
        metric_cards([
            ("7", "组对照实验"), ("82", "个真实时间点"), ("69", "轮完整逐题结果"), ("119", "道 Level 5 数学题"),
        ], widths=[43.5 * mm] * 4, dark=True),
        Spacer(1, 7 * mm),
        P("一个可交互的研究剖面：从固定算子库，到代码安全网，再到自动生成新算子。故事不追逐单一最高分，而是追问系统的瓶颈如何一步步移动。", "WhiteLead"),
        PageBreak(),
    ]

    # Page 2: question and data
    story += section("01 · RESEARCH QUESTION", "我们到底在观察什么？", "AFlow 用搜索自动生成 Agentic Workflow。本项目进一步尝试让算子本身也能进化。可视化的核心问题是：扩展、验证和进化分别改变了什么？")
    story += [
        PipelineFlow(), Spacer(1, 4 * mm),
        metric_cards([
            ("119", "题目，全部 Level 5"), ("4", "题型"), ("7", "实验版本"), ("0", "题目关联遗漏"),
        ], widths=[43.5 * mm] * 4),
        Spacer(1, 5 * mm),
        P("数据口径", "CNH2"),
        standard_table([
            ["数据层", "文件", "在故事中的作用"],
            ["题目元数据", "math_validate.jsonl", "题型、难度、题目与标准解"],
            ["轮次汇总", "results.json", "真实 round 字段与总体准确率"],
            ["逐题证据", "每轮 CSV", "119题预测、评分与切片统计"],
            ["工作流结构", "graph.py", "算子组成和调用顺序"],
            ["进化摘要", "analysis.json", "新算子、拒绝与最终算子池"],
        ], [30 * mm, 47 * mm, 97 * mm]),
        Spacer(1, 5 * mm),
        callout("审计结果", "逐题关联 0 遗漏；69轮 CSV 均值与 results.json 总分一致。v5/v6 的有效轮并不连续，因此全程使用真实 round 字段，而不是数组下标。"),
        PageBreak(),
    ]

    # Page 3: experiment map
    story += section("02 · EXPERIMENT MAP", "七组实验，是一条逐步暴露问题的路线", "实验不是简单的排行榜。每个版本都在修补上一个版本暴露的缺口，同时制造新的约束。")
    rows = [["阶段", "版本", "关键变化", "Best", "零分点"]]
    for exp in data["experiments"]:
        zero = sum(1 for r in exp["rounds"] if r["score"] == 0)
        rows.append([
            exp["stage"], exp["short"], " / ".join(exp["features"]),
            f"{exp['summary']['best_score']*100:.1f}%", str(zero),
        ])
    story += [
        standard_table(rows, [22 * mm, 37 * mm, 78 * mm, 18 * mm, 18 * mm]),
        Spacer(1, 6 * mm),
        P("三段式阅读", "CNH2"),
        metric_cards([
            ("01", "扩展：更多能力，也带来更多生成故障"),
            ("02", "验证：先保证工作流可运行，再谈搜索上限"),
            ("03", "进化：生成方向改善后，采用率成为瓶颈"),
        ], widths=[58 * mm] * 3),
        Spacer(1, 7 * mm),
        bullet("固定3算子基线已经达到 83.2%，因此“增加算子”面对的是一个很强的参照。"),
        bullet("B1 的最佳分数反而降到 82.4%，并出现4个零分点；搜索空间扩大不等于有效能力扩大。"),
        bullet("B2/B3 把固定算子体系推到 84.0%，成为整个实验族的最高值。"),
        bullet("v4-v6 跑通自动进化，但尚未稳定超过最佳固定算子配置。"),
        Spacer(1, 4 * mm),
        callout("阅读提示", "折线中的零分并不总表示模型完全不会做题。很多零分来自工作流生成或冒烟阶段失败，它们刻画的是系统工程风险。", ORANGE),
        PageBreak(),
    ]

    # Page 4: expansion
    story += section("03 · EXPANSION", "第一幕：更多算子先带来了更多失败", "从3个算子扩展到6个算子后，理论搜索上限变高；但没有安全网时，代码生成质量成为主导变量。")
    story += [
        ComparisonBars(data["experiments"]),
        Spacer(1, 4 * mm),
        standard_table([
            ["对照", "算子/机制", "Best", "观察"],
            ["A 基线", "3算子，无验证", "83.2%", "11轮全部有有效结果"],
            ["B1", "6算子，无验证", "82.4%", "4个零分点，扩展未兑现"],
            ["B2", "6算子 + AST", "84.0%", "零分点降为1"],
            ["B3", "6算子 + AST + 冒烟", "84.0%", "11轮全部完成评测"],
        ], [27 * mm, 55 * mm, 20 * mm, 72 * mm]),
        Spacer(1, 6 * mm),
        callout("核心发现", "搜索空间扩大后，优化器不仅要找到更好的工作流，还必须跨过“生成出的代码能否运行”这道门槛。没有验证时，工程失败足以吞掉理论收益。", RED),
        Spacer(1, 4 * mm),
        bullet("B1 的失败推翻了“算子越多越好”的直觉。"),
        bullet("B2 又推翻了“扩展算子本身无效”的过早结论：加入 AST 后，6算子超过3算子基线。"),
        PageBreak(),
    ]

    # Page 5: validation
    story += section("04 · VALIDATION", "第二幕：安全网让搜索重新可用", "AST 检查阻止语法错误，冒烟测试阻止运行时错误。两者的价值不只是提高分数，更是让一次优化轨迹不再被灾难性失败切断。")
    story += [
        Image(str(ASSET_DIR / "dashboard_chart.jpg"), width=174 * mm, height=97.9 * mm),
        Spacer(1, 5 * mm),
        metric_cards([
            ("44%", "B1 报告中的无验证零分轮占比"),
            ("9%", "B2 加 AST 后的零分轮占比"),
            ("0", "B3 去退火后的零分轮"),
        ], widths=[58 * mm] * 3),
        Spacer(1, 6 * mm),
        P("安全不等于保守", "CNH2"),
        bullet("AST 和冒烟测试过滤不可运行方案，是“准入安全网”。"),
        bullet("退火最优保留会拒绝暂时退化的探索，曾把 best 从 84.0% 压到 79.8%。"),
        bullet("删除退火保留后，B3 恢复到 84.0%，说明安全网与强制保守不是一回事。"),
        callout("设计含义", "一个好的搜索系统需要允许分数波动，但不能允许候选方案在语法或运行层面失效。前者是探索，后者只是噪声。"),
        PageBreak(),
    ]

    # Page 6: evolution
    story += section("05 · EVOLUTION", "第三幕：生成方向变对了，瓶颈却继续移动", "v4 首次跑通“检测瓶颈 - 生成算子 - 验证 - 安装”闭环。随后两轮改进分别处理生成方向和代码质量。")
    story += [
        EvolutionFlow(), Spacer(1, 3 * mm),
        standard_table([
            ["版本", "生成倾向", "代表算子", "存活/成功", "冒烟拒绝率", "Best"],
            ["v4", "验证型", "AlgebraicSanityCheck", "2/4", "高", "79.0%"],
            ["v5", "策略型", "GeometricDecomposer", "1/4", "37.5%", "83.2%"],
            ["v6", "策略型", "Trig / Geometry / CSP", "4/4*", "20.0%*", "81.5%"],
        ], [16 * mm, 25 * mm, 57 * mm, 23 * mm, 27 * mm, 20 * mm]),
        P("* v6 成功率与拒绝率采用包含续跑阶段的实验汇总；交互页面的逐题视图仅使用有原始 CSV 的第一阶段。", "Tiny"),
        Spacer(1, 5 * mm),
        bullet("Prompt 的 anti-pattern 明确改变了生成方向：v4 的 check/verify 转向 v5 的 reformulate/decompose。"),
        bullet("把冒烟错误反馈给 LLM 后，v6 的算子生成成功率明显提高。"),
        bullet("但新算子经常没有被后续 graph.py 采用；“生成成功”仍不等于“产生贡献”。"),
        Spacer(1, 4 * mm),
        callout("新的研究问题", "当算子池持续扩张时，优化器如何针对题型动态路由，并给新算子足够的试用机会？这是比继续堆算子更关键的问题。", ORANGE),
        PageBreak(),
    ]

    # Page 7: evidence and case
    q73 = next(q for q in data["questions"] if q["id"] == "q073")
    story += section("06 · PROBLEM-LEVEL EVIDENCE", "总分背后：一个始终没有被解决的三角求和题", "总体准确率会掩盖持久性失败。q073 恰好对应进化系统反复试图补齐的三角策略能力。")
    story += [
        P("<b>q073 · Precalculus</b><br/>求最小正有理数 r，使从 k=1 到 35 的 sin(5k°) 之和等于 tan(r°)。", "CNBody"),
        TrajectoryGrid(data),
        Spacer(1, 4 * mm),
        standard_table([
            ["证据", "观察"],
            ["固定基线", "偶尔答对，但跨轮不稳定；R6/R8/R10 为正确。"],
            ["v5", "生成 TrigonometricIdentityReformulator 等策略算子，但有逐题结果的轮次全部答错。"],
            ["v6", "生成 TrigonometricIdentitySolver，第一阶段各轮仍全部答错。"],
            ["工作流", "新算子被安装不代表后续候选 workflow 实际调用它。"],
        ], [36 * mm, 138 * mm]),
        Spacer(1, 5 * mm),
        callout("案例结论", "这不是“新算子一定无效”的证据，而是“生成 - 安装 - 采用 - 贡献”四个环节必须分开测量的证据。当前系统主要完成了前两步。", PURPLE),
        Spacer(1, 5 * mm),
        P("交互页面还允许点击任意题目，查看预测、标准解和跨轮正确/错误轨迹；这让读者可以从聚合统计回到原始证据。", "Small"),
        PageBreak(),
    ]

    # Page 8: conclusions
    story += section("07 · CONCLUSION", "答案不是“进化有效”或“进化失败”", "更准确的结论是：每一次修复都让瓶颈向后移动。系统已经从“能否运行”推进到“能否生成”，下一步是“能否被正确采用”。")
    story += [
        metric_cards([
            ("1", "更多算子不会自动转化为更高准确率"),
            ("2", "AST + 冒烟是有效搜索的基础设施"),
            ("3", "进化算子的采用率是当前核心瓶颈"),
        ], widths=[58 * mm] * 3),
        Spacer(1, 7 * mm),
        P("局限与边界", "CNH2"),
        standard_table([
            ["局限", "影响", "后续方案"],
            ["多数配置只有一次完整运行", "难以区分机制收益与采样随机性", "多次运行、置信区间和显著性检验"],
            ["验证子集全部为 Level 5", "不能形成难度梯度对照", "加入完整 MATH level 分层"],
            ["v6 续跑缺少逐题 CSV", "续跑只能用于汇总叙事", "统一实验产物格式与事件日志"],
            ["算子安装与使用没有独立指标", "难以归因新算子的真实贡献", "记录选择率、调用率和题型条件收益"],
        ], [38 * mm, 66 * mm, 70 * mm]),
        Spacer(1, 7 * mm),
        P("交互系统怎么读", "CNH2"),
        bullet("点击折线节点：切换实验轮次，联动工作流和逐题证据。"),
        bullet("点击题型 × 轮次矩阵：让所有实验曲线按该题型重新计算。"),
        bullet("点击算子或题目：高亮采用轮次，打开预测与标准解。"),
        Spacer(1, 5 * mm),
        callout("最终判断", "AFlow 的自动进化闭环已经成立，但性能闭环仍未成立。下一阶段应优先研究动态路由、强制试用和逐算子贡献估计，而不是继续扩大算子池。", TEAL),
        Spacer(1, 5 * mm),
        P("数据与实现：本地 AFlow 项目实验产物。可视化使用原生 HTML/CSS/JavaScript/SVG，离线运行；报告使用 ReportLab 生成。", "Tiny"),
    ]
    return story


def main():
    data = load_data()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=17 * mm,
        title="更多算子，不一定更聪明：AFlow 工作流自动进化的数据故事",
        author="AFlow Project",
        subject="数据可视化课程作业",
    )
    doc.build(build_story(data), onFirstPage=page_decor, onLaterPages=page_decor)
    print(OUTPUT)


if __name__ == "__main__":
    main()
