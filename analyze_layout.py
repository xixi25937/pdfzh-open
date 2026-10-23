#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排版量化分析：找出译文页里的「异常空白」与「样式丢失」。

对比原文页与译文页的文本行位置/字号/颜色，输出可复现的数字证据，
而不是靠肉眼感觉判断「排版烂」。

用法:
    python analyze_layout.py 原版.pdf 对照版.pdf [--page 1]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf


def page_lines(page: "pymupdf.Page", clip: "pymupdf.Rect | None" = None) -> list[dict]:
    """取出页面（或裁剪区）内的文本行信息。"""
    data = page.get_text("dict", clip=clip) if clip else page.get_text("dict")
    lines: list[dict] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            x0, y0, x1, y1 = line["bbox"]
            lines.append(
                {
                    "text": "".join(s["text"] for s in spans)[:60],
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "size": max(s["size"] for s in spans),
                    "color": spans[0]["color"],
                    "font": spans[0]["font"],
                }
            )
    lines.sort(key=lambda l: (round(l["y0"], 1), l["x0"]))
    return lines


def report_gaps(lines: list[dict], label: str, col_x0: float, col_x1: float) -> None:
    """统计「同一栏内相邻行间距」的分布，找出异常大的空隙。

    注意：必须先按栏分组再比较相邻行。双栏页面里若把所有行按 y 排序，
    会把「左栏底部 ↔ 右栏顶部」这类跨栏组合当成相邻行，算出负数"行距"，
    从而得到「文字重叠」的错误结论。
    """
    in_col = [l for l in lines if l["x0"] >= col_x0 - 2 and l["x1"] <= col_x1 + 40]
    if len(in_col) < 3:
        print(f"  [{label}] 该栏行数不足，跳过")
        return

    # 按 x0 聚类分栏（容差 12pt），再在每栏内按 y 排序比较相邻行
    columns: list[list[dict]] = []
    for line in sorted(in_col, key=lambda l: l["x0"]):
        if columns and abs(line["x0"] - columns[-1][0]["x0"]) <= 12:
            columns[-1].append(line)
        else:
            columns.append([line])

    sizes = [l["size"] for l in in_col]
    med_size = sorted(sizes)[len(sizes) // 2]

    all_gaps: list[tuple[float, dict, dict]] = []
    for col in columns:
        col.sort(key=lambda l: l["y0"])
        for a, b in zip(col, col[1:]):
            all_gaps.append((b["y0"] - a["y1"], a, b))

    if not all_gaps:
        print(f"  [{label}] 无有效行对")
        return

    gaps_sorted = sorted(g[0] for g in all_gaps)
    med_gap = gaps_sorted[len(gaps_sorted) // 2]
    # 重叠 = 行距为负；异常大空隙 = 超过 1.8 倍字号
    overlaps = [g for g in all_gaps if g[0] < -0.5]
    weird = [g for g in all_gaps if g[0] > med_size * 1.8]

    print(
        f"  [{label}] 行数={len(in_col)} 分栏={len(columns)} 中位字号={med_size:.1f}pt "
        f"中位行距={med_gap:.1f}pt 重叠={len(overlaps)} 大空隙={len(weird)}/{len(all_gaps)}"
    )
    for gap, a, b in sorted(weird, key=lambda t: -t[0])[:4]:
        print(
            f"      空隙 {gap:6.1f}pt ({gap / med_size:.1f}×字号) 于 "
            f"「{a['text'][:32]}」↔「{b['text'][:32]}」"
        )
    for gap, a, b in sorted(overlaps, key=lambda t: t[0])[:4]:
        print(
            f"      重叠 {gap:6.1f}pt 于 "
            f"「{a['text'][:32]}」↔「{b['text'][:32]}」"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="量化分析译文排版")
    ap.add_argument("original")
    ap.add_argument("bilingual")
    ap.add_argument("--page", type=int, default=1, help="页码，从 1 开始")
    ns = ap.parse_args()

    src = pymupdf.open(Path(ns.original).resolve())
    out = pymupdf.open(Path(ns.bilingual).resolve())
    pno = ns.page - 1

    sp, op = src[pno], out[pno]
    print("=" * 70)
    print(f"原版第 {ns.page} 页: {sp.rect.width:.0f}x{sp.rect.height:.0f}pt")
    print(f"对照版第 {ns.page} 页: {op.rect.width:.0f}x{op.rect.height:.0f}pt")

    # 对照版是左右两栏，译文在右半
    half = op.rect.width / 2
    right_clip = pymupdf.Rect(half, op.rect.y0, op.rect.x1, op.rect.y1)

    print("\n[1] 原文栏内行距")
    report_gaps(page_lines(sp), "原文", sp.rect.x0, sp.rect.width)

    print("\n[2] 译文（对照版右半）栏内行距")
    report_gaps(page_lines(op, right_clip), "译文", half, op.rect.x1)

    print("\n[3] 样式对比")
    src_lines = page_lines(sp)
    tgt_lines = page_lines(op, right_clip)

    def fontset(lines):
        d: dict[str, int] = {}
        for l in lines:
            d[l["font"]] = d.get(l["font"], 0) + 1
        return d

    print("  原文字体:", sorted(fontset(src_lines).items(), key=lambda x: -x[1])[:5])
    print("  译文字体:", sorted(fontset(tgt_lines).items(), key=lambda x: -x[1])[:5])

    def colorset(lines):
        d: dict[int, int] = {}
        for l in lines:
            d[l["color"]] = d.get(l["color"], 0) + 1
        return d

    print("  原文颜色(十进制):", colorset(src_lines))
    print("  译文颜色(十进制):", colorset(tgt_lines))

    print("\n[4] 字号分布（层级是否保留）")
    for label, lines in (("原文", src_lines), ("译文", tgt_lines)):
        sizes: dict[float, int] = {}
        for l in lines:
            key = round(l["size"], 1)
            sizes[key] = sizes.get(key, 0) + 1
        top = sorted(sizes.items(), key=lambda x: -x[0])[:6]
        print(f"  {label} 最大字号们: {top}")

    src.close()
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
