#!/usr/bin/env python3
"""把历史讨论导出为 PDF。

用法：
    ./venv/bin/python export_pdf.py                    # 导出最近一次讨论
    ./venv/bin/python export_pdf.py <文件名.json>       # 导出指定讨论（文件名在 discussions/ 下）
    ./venv/bin/python export_pdf.py --no-attachments   # 导出时不包含附件内容（只含话题文字）
"""
import json
import os
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DISC_DIR = os.path.join(BASE_DIR, "discussions")
OUT_DIR = os.path.join(BASE_DIR, "exports")

FONT = "CJK"


def register_font():
    """优先用系统 Arial Unicode（覆盖中文与常见符号），否则回退内置 CID 字体。"""
    global FONT
    for path in (
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ):
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("CJK", path))
                FONT = "CJK"
                return
            except Exception:
                pass
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    FONT = "STSong-Light"


def esc(text):
    """转义 XML 特殊字符，并把换行转成 <br/>。"""
    if text is None:
        return ""
    t = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return t.replace("\n", "<br/>")


def load_discussion(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def styles():
    return {
        "title": ParagraphStyle("title", fontName=FONT, fontSize=19, leading=27,
                                alignment=1, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", fontName=FONT, fontSize=12, leading=18,
                                   alignment=1, textColor=colors.HexColor("#333333"), spaceAfter=4),
        "meta": ParagraphStyle("meta", fontName=FONT, fontSize=9.5, leading=14,
                               alignment=1, textColor=colors.HexColor("#888888"), spaceAfter=6),
        "h1": ParagraphStyle("h1", fontName=FONT, fontSize=14.5, leading=21,
                             spaceBefore=16, spaceAfter=6,
                             textColor=colors.HexColor("#1a3d7c")),
        "h2": ParagraphStyle("h2", fontName=FONT, fontSize=11.5, leading=17,
                             spaceBefore=9, spaceAfter=3,
                             textColor=colors.HexColor("#444444")),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=16.5,
                               spaceAfter=6, textColor=colors.HexColor("#111111")),
        "speaker": ParagraphStyle("speaker", fontName=FONT, fontSize=11, leading=16,
                                  spaceBefore=8, spaceAfter=2,
                                  textColor=colors.HexColor("#0a5f3c")),
    }


def build_story(data, s, include_attachments=True):
    story = []
    story.append(Paragraph("多模型圆桌讨论", s["title"]))
    story.append(Paragraph("Claude · GPT · DeepSeek　辩论 → 起草 → 表决 → 定稿", s["subtitle"]))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 6))

    # 话题
    story.append(Paragraph("话题", s["h1"]))
    story.append(Paragraph(esc(data.get("topic", "")), s["body"]))
    tally = data.get("vote_tally") or {}
    tally_text = "同意{a}/部分同意{b}/不同意{c}".format(
        a=tally.get("同意", 0), b=tally.get("部分同意", 0), c=tally.get("不同意", 0))
    meta = "时间：{time}　|　讨论轮数：{n}　|　起草人：{d}　|　评委：{j}　|　表决：{t}".format(
        time=data.get("time", ""), n=data.get("num_rounds", ""),
        d=data.get("drafter", ""), j=data.get("judge", ""), t=tally_text)
    story.append(Paragraph(esc(meta), s["meta"]))
    story.append(Spacer(1, 4))

    # 附件（可选）
    if include_attachments:
        atts = data.get("attachments", [])
        if atts:
            story.append(Paragraph("附件", s["h1"]))
            for a in atts:
                if not isinstance(a, dict):
                    continue
                story.append(Paragraph(esc(a.get("name", "")), s["speaker"]))
                story.append(Paragraph(esc(a.get("text", "")) or "（无内容）", s["body"]))

    # 最终方案
    story.append(Paragraph("一、最终方案（评委裁决）", s["h1"]))
    story.append(Paragraph(esc(data.get("final", "")) or "（无内容）", s["body"]))

    # 方案草案
    story.append(Paragraph("二、方案草案", s["h1"]))
    story.append(Paragraph(esc(data.get("draft", "")) or "（无内容）", s["body"]))

    # 各方表决
    story.append(Paragraph("三、各方表决（盖章）", s["h1"]))
    votes = data.get("votes", [])
    if votes:
        for v in votes:
            story.append(Paragraph(esc(v.get("voter", "")), s["speaker"]))
            story.append(Paragraph(esc(v.get("content", "")) or "（无内容）", s["body"]))
    else:
        story.append(Paragraph("（无表决记录）", s["body"]))

    # 辩论过程
    story.append(Paragraph("四、辩论过程", s["h1"]))
    per_round = data.get("per_round", [])
    if per_round:
        for rnd in per_round:
            if not rnd:
                continue
            rn = rnd[0].get("round", "?")
            story.append(Paragraph("第 {} 轮".format(rn), s["h2"]))
            for u in rnd:
                story.append(Paragraph(esc(u.get("speaker", "")), s["speaker"]))
                story.append(Paragraph(esc(u.get("content", "")) or "（无内容）", s["body"]))
    else:
        story.append(Paragraph("（无辩论记录）", s["body"]))

    # 错误信息（如有）
    errors = data.get("errors", [])
    if errors:
        story.append(Paragraph("五、过程中的错误", s["h1"]))
        for e in errors:
            story.append(Paragraph(esc(str(e)), s["body"]))

    return story


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(colors.HexColor("#999999"))
    canvas.drawCentredString(A4[0] / 2, 10 * mm, "第 {} 页".format(doc.page))
    canvas.restoreState()


def main():
    register_font()

    include_attachments = "--no-attachments" not in sys.argv
    args = [a for a in sys.argv[1:] if a != "--no-attachments"]

    if args:
        src = args[0]
        if not os.path.isabs(src):
            src = os.path.join(DISC_DIR, src)
    else:
        files = sorted([f for f in os.listdir(DISC_DIR) if f.endswith(".json")], reverse=True)
        if not files:
            print("没有找到任何讨论存档。")
            return 1
        src = os.path.join(DISC_DIR, files[0])

    if not os.path.exists(src):
        print("找不到文件：{}".format(src))
        return 1

    data = load_discussion(src)
    os.makedirs(OUT_DIR, exist_ok=True)

    ts = data.get("time", "").replace(":", "").replace("-", "").replace(" ", "_") \
        or os.path.splitext(os.path.basename(src))[0]
    out_name = "圆桌讨论_{ts}.pdf".format(ts=ts)
    out_path = os.path.join(OUT_DIR, out_name)

    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=20 * mm,
        title="圆桌讨论：{}".format(data.get("topic", "")[:40]),
    )
    doc.build(build_story(data, styles(), include_attachments), onFirstPage=footer, onLaterPages=footer)
    print("已生成 PDF：{}".format(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
