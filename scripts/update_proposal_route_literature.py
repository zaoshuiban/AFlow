from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import _Cell


SOURCE = Path(
    "/Users/verneywang/Downloads/研究生学位（毕业）论文开题报告_马佳彤_聚焦修订_含探索性实验版.docx"
)
OUTPUT = Path(
    "/Users/verneywang/Downloads/研究生学位（毕业）论文开题报告_马佳彤_技术路线与国内文献补充版.docx"
)


def set_paragraph_text(paragraph, text):
    """Replace text while retaining the first run's formatting and paragraph properties."""
    rpr = None
    for run in paragraph.runs:
        if run._r.rPr is not None:
            rpr = deepcopy(run._r.rPr)
            break
    p = paragraph._p
    for child in list(p):
        if child.tag != qn("w:pPr"):
            p.remove(child)
    run = paragraph.add_run(text)
    if rpr is not None:
        run._r.insert(0, rpr)
    return run


def set_cell_text(cell, text):
    paragraph = cell.paragraphs[0] if cell.paragraphs else cell.add_paragraph()
    set_paragraph_text(paragraph, text)
    for extra in list(cell.paragraphs)[1:]:
        cell._tc.remove(extra._p)
    for nested in list(cell.tables):
        cell._tc.remove(nested._tbl)
    return paragraph


def find_row(table, exact_text):
    for row in table.rows:
        if row.cells[0].text.strip() == exact_text:
            return row
    raise ValueError(f"row not found: {exact_text}")


def insert_cloned_row_before(table, reference_tr, template_tr, text):
    new_tr = deepcopy(template_tr)
    reference_tr.addprevious(new_tr)
    cell = _Cell(new_tr.tc_lst[0], table)
    set_cell_text(cell, text)
    return new_tr


doc = Document(SOURCE)

# 1) Scope the preliminary result so it cannot be mistaken for a natural-distribution
#    benchmark result. Keep every other sentence and all formatting unchanged.
abstract_cell = doc.tables[0].rows[3].cells[-1]
old = (
    "前期已在MATH参数化问题族上完成两类自动生成专项算子及双算子联合验证："
    "在全新隐藏种子实验中，系统整体正确率由66.7%提高至91.7%，两类专项任务均达到100%。"
)
new = (
    "前期已在MATH参数化问题族上完成两类自动生成专项算子及双算子联合验证："
    "在包含两类目标机制及普通题、近似反例的构造混合隐藏集上，系统整体正确率由66.7%提高至91.7%，"
    "两类专项任务均达到100%。"
)
if old not in abstract_cell.text:
    raise ValueError("expected abstract sentence not found")
set_cell_text(abstract_cell, abstract_cell.text.replace(old, new))

# 2) Add a compact domestic-team discussion to the existing platform/evaluation
#    subsection. The four additions are formally published conference papers.
lit = doc.tables[1]
domestic_row = find_row(
    lit,
    "AutoGen、MetaGPT和ChatDev分别从多智能体对话、软件工程标准流程和角色化协作角度验证了复杂工作流的工程可行性[23-25]。AgentBench和AgentBoard进一步提供多环境、长程交互和细粒度过程评价[26-27]。这些工作说明，仅报告最终答案正确率不足以解释智能体系统的行为；对于动态扩展的算子库，还需要记录路由决定、完整轨迹、执行时间、失败类型和版本变化。",
)
set_cell_text(
    domestic_row.cells[0],
    domestic_row.cells[0].text
    + " 此外，国内团队已在工具平台、多智能体协同与自动生成方面形成正式成果[35-38]，但尚缺少面向稳定失败机制的算子生成、隐藏晋级与风险接入闭环。",
)

# Append references using the existing final reference row as a formatting template.
ref_template = find_row(
    lit,
    "[34] JIMENEZ C E, YANG J, WETTIG A, et al. SWE-bench: Can language models resolve real-world GitHub issues?[C]//International Conference on Learning Representations. 2024.",
)._tr
new_refs = [
    "[35] LI C, CHEN H, YAN M, et al. ModelScope-Agent: Building your customizable agent system with open-source large language models[C]//Proceedings of the 2023 Conference on Empirical Methods in Natural Language Processing: System Demonstrations. Singapore: Association for Computational Linguistics, 2023: 566-578.",
    "[36] CHEN W, SU Y, ZUO J, et al. AgentVerse: Facilitating multi-agent collaboration and exploring emergent behaviors[C]//International Conference on Learning Representations. 2024.",
    "[37] ZHANG C, YANG K, HU S, et al. ProAgent: Building proactive cooperative agents with large language models[C]//Proceedings of the AAAI Conference on Artificial Intelligence. 2024, 38(16): 17591-17599.",
    "[38] YUAN S, SONG K, CHEN J, et al. EvoAgent: Towards automatic multi-agent generation via evolutionary algorithms[C]//Proceedings of the 2025 Conference of the Nations of the Americas Chapter of the Association for Computational Linguistics: Human Language Technologies. Albuquerque: Association for Computational Linguistics, 2025: 6192-6217.",
]
for ref in new_refs:
    new_tr = deepcopy(ref_template)
    lit._tbl.append(new_tr)
    set_cell_text(_Cell(new_tr.tc_lst[0], lit), ref)

# Keep the appended literature from making the final approval form start halfway
# down a page and pushing its note onto an otherwise empty page.
for paragraph in doc.paragraphs:
    if paragraph.text.strip() == "华南理工大学研究生学位论文定密审批表":
        paragraph.paragraph_format.page_break_before = True
        break
else:
    raise ValueError("confidentiality approval form heading not found")

# 3) Explicitly enforce the requested wording boundary.
forbidden = ("人工修改次数", "人工代码修改次数", "人工修改了多少次")
full_text = "\n".join(p.text for p in doc.paragraphs)
full_text += "\n" + "\n".join(
    cell.text for table in doc.tables for row in table.rows for cell in row.cells
)
present = [phrase for phrase in forbidden if phrase in full_text]
if present:
    raise ValueError(f"forbidden wording remains: {present}")

doc.core_properties.comments = (
    "限定摘要实验范围，补充国内团队正式发表成果；未改变其余内容和模板格式。"
)
doc.save(OUTPUT)
print(OUTPUT)
