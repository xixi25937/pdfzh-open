#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拼版模块自测：构造与原版页码对应的「假译文」，验证合并结果与版式判定。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pymupdf

from pdf_side_by_side import MergeOptions, analyze_columns, detect_layout, merge_side_by_side

WORK = Path(__file__).resolve().parent / ".test"
WORK.mkdir(exist_ok=True)


def make_single_column_pdf(path: Path, pages: int = 3) -> None:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # A4
        body = (
            f"Page {i + 1}. This paper studies embodied intelligence. "
            "We propose a hierarchical policy that maps visual observations to "
            "low-level motor commands. The method is evaluated on three benchmarks "
            "and outperforms the strongest baseline by a clear margin. "
        ) * 6
        page.insert_textbox(
            pymupdf.Rect(56, 72, 539, 780), body, fontsize=10.5, fontname="helv"
        )
    doc.save(path)
    doc.close()


def make_two_column_pdf(path: Path, pages: int = 3) -> None:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        col = ("Embodied agents must ground language in perception and action. " * 12)
        page.insert_textbox(
            pymupdf.Rect(40, 80, 290, 780), col, fontsize=8.5, fontname="helv"
        )
        page.insert_textbox(
            pymupdf.Rect(305, 80, 555, 780), col, fontsize=8.5, fontname="helv"
        )
        # 再加两栏正文，保证 span 数超过单栏判定阈值
        col2 = ("We evaluate the policy on manipulation and navigation tasks. " * 12)
        page.insert_textbox(
            pymupdf.Rect(40, 400, 290, 790), col2, fontsize=8.5, fontname="helv"
        )
        page.insert_textbox(
            pymupdf.Rect(305, 400, 555, 790), col2, fontsize=8.5, fontname="helv"
        )
    doc.save(path)
    doc.close()


def make_fake_translation(src: Path, dst: Path) -> None:
    """生成一个与原文同尺寸、同页数的「译文」，仅用于验证拼版。"""
    doc = pymupdf.open(src)
    for page in doc:
        # 清空原内容后写中文，模拟译文版式
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
        text = (
            f"第 {page.number + 1} 页。本文研究具身智能，提出一种分层策略，"
            "将视觉观测映射为底层运动指令。方法在三个基准上评测，"
            "明显优于最强基线。这是一段用于验证排版的占位中文文本。"
        ) * 8
        page.insert_textbox(
            pymupdf.Rect(56, 72, page.rect.width - 56, page.rect.height - 62),
            text,
            fontsize=10.5,
            fontname="china-s",
        )
    doc.save(dst)
    doc.close()


def main() -> int:
    ok = True

    # ---- 1. 单栏 ----
    src = WORK / "en_single.pdf"
    dst = WORK / "zh_single.pdf"
    make_single_column_pdf(src)
    make_fake_translation(src, dst)

    stats = analyze_columns(pymupdf.open(src)[0])
    mode, reason = detect_layout(pymupdf.open(src))
    print(f"[单栏] spans={stats.n_spans} 左={stats.left_ratio:.2f} 右={stats.right_ratio:.2f} "
          f"双栏={stats.two_column} -> 版式={mode}")
    if mode != "lr":
        print("  ✗ 单栏文档应判定为 lr")
        ok = False

    out = WORK / "out_single.pdf"
    info = merge_side_by_side(src, dst, out, MergeOptions(mode="lr"))
    with pymupdf.open(out) as d:
        p0 = d[0]
        print(f"[单栏] 输出 {d.page_count} 页, 首页 {p0.rect.width:.0f}x{p0.rect.height:.0f}pt "
              f"({p0.rect.width / 72:.1f}x{p0.rect.height / 72:.1f} inch)")
        # 默认版式（对齐 BabelDOC）：页宽 = 2 × 595 = 1190，页高 = 595 页高 842
        if abs(p0.rect.width - 1190) > 2:
            print(f"  ✗ 默认横向版宽度应为 2×原宽(1190)，实际 {p0.rect.width}")
            ok = False
        if abs(p0.rect.height - 842) > 2:
            print(f"  ✗ 默认横向版高度应等于原高(842)，实际 {p0.rect.height}")
            ok = False
        # 默认不加标签（用标签专属字样判断，避免与正文里的普通词混淆）
        txt = p0.get_text()
        if "English" in txt or "译 文" in txt or "原 文" in txt:
            print("  ✗ 默认版不应出现「原文/译文」标签")
            ok = False
        if "第" not in txt:
            print(f"  ✗ 译文中文字未进入输出, 实际文本片段: {txt[:80]!r}")
            ok = False
        if "This paper studies" not in txt:
            print("  ✗ 英文原文未进入输出")
            ok = False

    # ---- 1b. framed 模式：应有留白、分隔线、中英标签 ----
    out_framed = WORK / "out_single_framed.pdf"
    merge_side_by_side(src, dst, out_framed, MergeOptions(mode="lr", framed=True))
    with pymupdf.open(out_framed) as d:
        p0 = d[0]
        # 注意：insert_text 写出的空格可能是 U+00A0（不换行空格），
        # 直接比较普通空格会误判，这里先归一化空白。
        txt = re.sub(r"\s+", " ", p0.get_text())
        print(f"[边框] framed 输出 {p0.rect.width:.0f}x{p0.rect.height:.0f}pt")
        if not (1225 < p0.rect.width < 1240):
            print(f"  ✗ framed 横向宽度不符合预期: {p0.rect.width}")
            ok = False
        if "English" not in txt or "译 文" not in txt:
            print(f"  ✗ framed 模式应有中英标签，实际顶部: {txt[:60]!r}")
            ok = False

    # ---- 2. 双栏 ----
    src2 = WORK / "en_double.pdf"
    dst2 = WORK / "zh_double.pdf"
    make_two_column_pdf(src2)
    make_fake_translation(src2, dst2)

    stats2 = analyze_columns(pymupdf.open(src2)[0])
    mode2, reason2 = detect_layout(pymupdf.open(src2))
    print(f"[双栏] spans={stats2.n_spans} 左={stats2.left_ratio:.2f} 右={stats2.right_ratio:.2f} "
          f"双栏={stats2.two_column} -> auto 会建议 {mode2}")
    if mode2 != "tb":
        print("  ✗ 双栏文档的 auto 建议应为 tb")
        ok = False
    # 但默认版式是 lr（每半页宽度=原页宽，不缩放，因此双栏也不会变小）
    print(f"      自动判定理由: {reason2}")

    out2 = WORK / "out_double.pdf"
    merge_side_by_side(src2, dst2, out2, MergeOptions(mode="lr"))
    with pymupdf.open(out2) as d:
        p0 = d[0]
        print(f"[双栏] lr 输出 {d.page_count} 页, 首页 {p0.rect.width:.0f}x{p0.rect.height:.0f}pt")
        if abs(p0.rect.width - 1190) > 2:
            print(f"  ✗ 双栏 lr 宽度应为 2×原宽(1190)，实际 {p0.rect.width}")
            ok = False

    # 再验一次 tb
    out2b = WORK / "out_double_tb.pdf"
    merge_side_by_side(src2, dst2, out2b, MergeOptions(mode="tb"))
    with pymupdf.open(out2b) as d:
        p0 = d[0]
        print(f"[双栏] tb 输出 {d.page_count} 页, 首页 {p0.rect.width:.0f}x{p0.rect.height:.0f}pt")
        # 纵向：高 = 2×842 = 1684
        if abs(p0.rect.height - 1684) > 2:
            print(f"  ✗ 纵向版高度应为 2×原高(1684)，实际 {p0.rect.height}")
            ok = False

    # ---- 4. only_pages 只输出指定页 ----
    out3 = WORK / "out_pages.pdf"
    info3 = merge_side_by_side(
        src, dst, out3, MergeOptions(mode="lr"), only_pages=[0, 2]
    )
    with pymupdf.open(out3) as d:
        print(f"[选页] 只输出第 1、3 页 -> 结果 {d.page_count} 页, source_pages={info3['source_pages']}")
        if d.page_count != 2 or info3["source_pages"] != [1, 3]:
            print("  ✗ only_pages 未按预期裁剪页数")
            ok = False

    # ---- 3. 页数不一致必须报错 ----
    short = WORK / "zh_short.pdf"
    make_fake_translation(src, short)
    with pymupdf.open(short) as d:
        d.delete_page(0)
        d.save(WORK / "zh_short2.pdf")
    try:
        merge_side_by_side(src, WORK / "zh_short2.pdf", WORK / "bad.pdf", MergeOptions())
        print("  ✗ 页数不一致时应当抛错")
        ok = False
    except ValueError as e:
        print(f"[校验] 页数不一致已正确拦截：{e}")

    print("\n结论：" + ("全部通过 ✅" if ok else "存在失败项 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
