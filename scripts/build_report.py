from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


DISPLAY = {"bitget": "Bitget", "binance": "Binance", "okx": "OKX", "bybit": "Bybit"}
ORDER = ("bitget", "binance", "okx", "bybit")
BLUE = "17365D"
PALE = "EEF3F8"
BORDER = "D9D9D9"
# Heiti SC is bundled with current macOS releases and is reliably available to
# both Word and the headless LibreOffice renderer used for report QA.
FONT = "Heiti SC"


def fmt(value, digits=1, missing="-"):
    if value is None:
        return missing
    return f"{value:,.{digits}f}"


def wan(value):
    return fmt(value / 10_000, 1) if value is not None else "-"


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=90, start=90, bottom=90, end=90):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    margins = tc_pr.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        tc_pr.append(margins)
    for tag, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = margins.find(qn(f"w:{tag}"))
        if node is None:
            node = OxmlElement(f"w:{tag}")
            margins.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_borders(table):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), "4")
        node.set(qn("w:color"), BORDER)


def repeat_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    marker = OxmlElement("w:tblHeader")
    marker.set(qn("w:val"), "true")
    tr_pr.append(marker)


def prevent_row_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    marker = OxmlElement("w:cantSplit")
    marker.set(qn("w:val"), "true")
    tr_pr.append(marker)


def set_run_font(run, size=None, bold=None, color=None):
    run.font.name = FONT
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT)
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial")
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)


def add_table(doc, headers, rows, widths=None, font_size=8.5):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table)
    header = table.rows[0]
    repeat_header(header)
    for idx, label in enumerate(headers):
        cell = header.cells[idx]
        set_cell_shading(cell, BLUE)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        set_cell_margins(cell)
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_run_font(paragraph.add_run(str(label)), font_size, True, "FFFFFF")
        if widths:
            cell.width = Inches(widths[idx])
    for row_idx, values in enumerate(rows):
        new_row = table.add_row()
        prevent_row_split(new_row)
        cells = new_row.cells
        if row_idx % 2:
            for cell in cells:
                set_cell_shading(cell, PALE)
        for idx, value in enumerate(values):
            cell = cells[idx]
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            set_cell_margins(cell)
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            set_run_font(paragraph.add_run(str(value)), font_size, False, "000000")
            if widths:
                cell.width = Inches(widths[idx])
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    return table


def add_heading(doc, text, level=1):
    paragraph = doc.add_heading(text, level=level)
    paragraph.paragraph_format.keep_with_next = True
    for run in paragraph.runs:
        set_run_font(run, 15 if level == 1 else 12, True, "000000")
    return paragraph


def add_body(doc, text, bold_lead=None):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(7)
    paragraph.paragraph_format.line_spacing = 1.2
    if bold_lead and text.startswith(bold_lead):
        set_run_font(paragraph.add_run(bold_lead), 10.5, True, "000000")
        text = text[len(bold_lead):]
    set_run_font(paragraph.add_run(text), 10.5, False, "000000")
    return paragraph


def configure_document(doc):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.65)
    section.right_margin = Inches(0.65)
    styles = doc.styles
    for name, size in (("Normal", 10.5), ("Title", 24), ("Heading 1", 15), ("Heading 2", 12)):
        style = styles[name]
        style.font.name = FONT
        style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT)
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
    styles["Title"].font.bold = True
    title_properties = styles["Title"].element.get_or_add_pPr()
    title_border = title_properties.find(qn("w:pBdr"))
    if title_border is not None:
        title_properties.remove(title_border)


def depth_row(venue, result):
    metrics = result["metrics"]
    health = result["health"]
    d = metrics.get("depth", {})
    return [
        DISPLAY[venue],
        wan(d.get("bid1_usdt", {}).get("median")),
        wan(d.get("ask1_usdt", {}).get("median")),
        wan(d.get("bid10_usdt", {}).get("median")),
        wan(d.get("ask10_usdt", {}).get("median")),
        wan(d.get("bid20_usdt", {}).get("median")),
        wan(d.get("ask20_usdt", {}).get("median")),
        fmt(metrics.get("spread_bps", {}).get("median"), 3),
        f"{health.get('valid_rate_pct', 0):.1f}%" + ("*" if not health.get("rank_eligible") else ""),
    ]


def slip_cell(item):
    p90 = item.get("p90_bps")
    coverage = item.get("coverage_pct", 0)
    star = "*" if coverage < 80 else ""
    return f"{fmt(p90, 3)} / {coverage:.1f}%{star}"


def bucket_p10(item):
    coverage = item.get("coverage_pct", 0)
    if coverage >= 80 and item.get("p10") is not None:
        return wan(item.get("p10"))
    lower = item.get("observed_lower_bound", {}).get("p10")
    return f"≥{wan(lower)}" if lower is not None else "-"


def build(summary_path: Path, output_path: Path):
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    doc = Document()
    configure_document(doc)

    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    direct_border = title._p.get_or_add_pPr().find(qn("w:pBdr"))
    if direct_border is not None:
        title._p.get_or_add_pPr().remove(direct_border)
    set_run_font(title.add_run("BTC USDT 永续流动性快速监控报告"), 24, True, "000000")
    subtitle = doc.add_paragraph()
    subtitle.paragraph_format.space_after = Pt(15)
    start = data.get("run", {}).get("start_beijing") or "数据未就绪"
    set_run_font(subtitle.add_run(f"Bitget Binance OKX Bybit | 启动时间 {start} | 5秒快照"), 10, False, "555555")

    windows = data.get("windows", {})
    final_key = "5m" if "5m" in windows else (list(windows)[-1] if windows else None)
    final = windows.get(final_key, {}) if final_key else {}
    conclusions = final.get("conclusions", ["本次公开行情未形成可比较数据，报告仅保留诊断信息。"])

    add_heading(doc, "执行摘要")
    add_body(doc, "本报告比较四家交易所 BTC USDT 永续的公开静态盘口。主动买入消耗卖盘，主动卖出消耗买盘；因此深度、滑点和子单建议均按方向分别判断。")
    for item in conclusions:
        paragraph = doc.add_paragraph(style=None)
        paragraph.style = doc.styles["List Bullet"]
        paragraph.paragraph_format.space_after = Pt(4)
        set_run_font(paragraph.add_run(item), 10.5, False, "000000")

    add_heading(doc, "指标定义")
    add_body(doc, "P10深度：有效对齐快照中深度的第10百分位，约90%的有效观察值不低于它，因此用于表示偏保守的可用深度。")
    add_body(doc, "P90滑点：仅在保存的前20档足以完成测试金额的样本中计算滑点第90百分位，约90%的已完成样本不高于它；未完成样本不会被假设价格。")
    add_body(doc, "覆盖率：完成样本数除以该窗口全部有效对齐快照数。滑点覆盖率表示前20档足以完成测试金额；七档完整率表示前20档跨过第七个聚合桶边界。")
    add_body(doc, "母单：用户计划在一段执行时间内完成的总金额，可以跨时间、交易所和订单类型拆分。子单：母单拆出的单次小额订单；报告中的子单上限是主动成交的保守起始值，不是保证成交额度。")

    add_heading(doc, "一分钟 三分钟 五分钟累计窗口")
    add_body(doc, "三个窗口都从本次启动时刻累计，使用同一批5秒对齐快照。金额为对应买卖侧累计名义金额的窗口中位数，单位为万USDT；买10和卖10均指第1至第10档累计，而不是单独第10档。")
    headers = ["交易所", "买一", "卖一", "买10", "卖10", "买20", "卖20", "价差bps", "有效率"]
    for key, label in (("1m", "一分钟累计窗口"), ("3m", "三分钟累计窗口"), ("5m", "五分钟累计窗口")):
        window = windows.get(key)
        if not window:
            continue
        add_heading(doc, label, level=2)
        rows = []
        for venue in ORDER:
            if venue in window.get("venues", {}):
                rows.append(depth_row(venue, window["venues"][venue]))
        add_table(doc, headers, rows, [0.72, 0.58, 0.58, 0.58, 0.58, 0.58, 0.58, 0.65, 0.65], 8)
    add_body(doc, "* 未通过横向排名健康门槛：有效率低于80%、陈旧率高于5%、出现交叉盘口或序列缺口。缺失盘口不向前填充。")

    add_heading(doc, "滑点与成交覆盖率")
    add_body(doc, "单元格格式为P90静态滑点bps / 前20档成交覆盖率。滑点以本所下单前最优对手价为基准；主动买入逐档消耗卖盘，主动卖出逐档消耗买盘。")
    headers = ["单笔金额", "Bitget 买/卖", "Binance 买/卖", "OKX 买/卖", "Bybit 买/卖"]
    rows = []
    for target in data.get("method", {}).get("slippage_targets_usdt", []):
        row = [f"{int(target)/10000:g}万"]
        for venue in ORDER:
            metrics = final.get("venues", {}).get(venue, {}).get("metrics", {})
            item = metrics.get("slippage", {}).get(str(target), {})
            row.append(f"{slip_cell(item.get('buy', {}))} / {slip_cell(item.get('sell', {}))}")
        rows.append(row)
    add_table(doc, headers, rows, [0.7, 1.35, 1.35, 1.35, 1.35], 7.5)
    add_body(doc, "* 覆盖率低于80%。P90仅基于前20档可完成样本，存在选择偏差，真实执行成本可能更高。零滑点只表示订单在保存的最佳价完成，不包含手续费、网络延迟、撤单、自身冲击和后续价格变化。")

    add_heading(doc, "0.1 USDT 七个聚合价位")
    add_body(doc, "买盘按0.1 USDT向下取整，卖盘向上取整；七档指从最优聚合桶起连续七个价格桶。表内P10是完整样本的保守深度；完整率低于80%时以≥标示已观察深度下限。")
    headers = ["交易所", "买盘P10 万", "买完整率", "卖盘P10 万", "卖完整率", "买入子单上限 万", "卖出子单上限 万"]
    rows = []
    for venue in ORDER:
        metrics = final.get("venues", {}).get(venue, {}).get("metrics", {})
        p01 = metrics.get("p01_seven", {})
        caps = metrics.get("child_order_caps", {})
        rows.append([
            DISPLAY[venue], bucket_p10(p01.get("bid", {})), f"{p01.get('bid', {}).get('coverage_pct', 0):.1f}%",
            bucket_p10(p01.get("ask", {})), f"{p01.get('ask', {}).get('coverage_pct', 0):.1f}%",
            wan(caps.get("active_buy", {}).get("recommended_usdt")),
            wan(caps.get("active_sell", {}).get("recommended_usdt")),
        ])
    add_table(doc, headers, rows, [0.7, 0.85, 0.75, 0.85, 0.75, 1.15, 1.15], 8)

    add_heading(doc, "1 USDT 七个聚合价位")
    add_body(doc, "计算方向与0.1 USDT相同，但每个价格桶宽1 USDT。由于只保存前20档，未跨过第七桶边界时只报告≥已观察下限，不参与完整深度排名。")
    rows = []
    for venue in ORDER:
        metrics = final.get("venues", {}).get(venue, {}).get("metrics", {})
        p1 = metrics.get("p1_seven", {})
        rows.append([
            DISPLAY[venue], bucket_p10(p1.get("bid", {})), f"{p1.get('bid', {}).get('coverage_pct', 0):.1f}%",
            bucket_p10(p1.get("ask", {})), f"{p1.get('ask', {}).get('coverage_pct', 0):.1f}%",
        ])
    add_table(doc, ["交易所", "买盘P10或下限 万", "买完整率", "卖盘P10或下限 万", "卖完整率"], rows,
              [0.9, 1.45, 1.05, 1.45, 1.05], 8.5)

    add_heading(doc, "执行建议")
    add_body(doc, "建议子单上限取两个约束中的较小值：一是满足成交覆盖率至少90%且P90静态滑点不超过0.10bps的最大测试金额；二是对手盘0.1七档P10深度的15%。母单是计划完成的总金额，子单是母单拆出的单次订单；该上限是当前盘口下的保守起点，不是保证成交额度。")
    add_body(doc, "对于25万USDT及以上母单，优先使用TWAP或POV拆单；对时效不敏感的部分以post only挂单为主，紧急余量使用带价格保护的IOC。持续比较主动买入侧的卖盘承载与主动卖出侧的买盘承载，避免用双边总深度替代方向判断。")

    add_heading(doc, "数据健康与结论边界")
    health_rows = []
    for venue in ORDER:
        health = final.get("venues", {}).get(venue, {}).get("health", {})
        health_rows.append([DISPLAY[venue], health.get("attempts", 0), health.get("valid", 0),
                            f"{health.get('valid_rate_pct', 0):.1f}%", f"{health.get('stale_rate_pct', 0):.1f}%",
                            health.get("crossed", 0), health.get("sequence_gaps", 0),
                            "合格" if health.get("rank_eligible") else "数据不足"])
    add_table(doc, ["交易所", "应采", "有效", "有效率", "陈旧率", "交叉", "序列缺口", "状态"], health_rows,
              [0.75, 0.55, 0.55, 0.7, 0.7, 0.55, 0.75, 0.8], 8)
    proxy = data.get("run", {}).get("proxy_used") or "未记录"
    add_body(doc, f"数据来自四所公开WebSocket，实际连接使用：{proxy}。采集器断线后清空旧盘口并等待新快照，不使用账户、API密钥或真实资金，也不安装或修改VPN。")
    add_body(doc, "本报告是公开静态盘口估算，不评价真实成交率、排队位置、拒单率或私有API性能。实盘还会受到手续费、撮合和网络延迟、撤单、其他订单竞争、自身市场冲击及markout影响。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    print(json.dumps({"report": str(output_path), "windows": list(windows)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.summary, args.output)


if __name__ == "__main__":
    main()
