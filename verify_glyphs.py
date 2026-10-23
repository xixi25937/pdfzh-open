#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""像素级验收：检测「文本层有字、渲染却是空白」的字形缺失。

为什么需要这个检查（实测踩过的坑）：
    字体沿用补丁一度把数字交给了粗体 ``NimbusRomNo9L-Medi``，结果这些字
    在文本层里存在（复制搜索都正常），但**渲染上完全不可见**（墨迹占比 0.000）。
    纯文本层的验收脚本完全发现不了这类问题，必须逐 span 量像素。

用法:
    python verify_glyphs.py 对照版.pdf [--zoom 6] [--threshold 0.004]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pymupdf

# 控制字符：图片内部文字常被提取成这种乱码碎片（Type3 字体），
# 它们不是正文，其"渲染空白"不代表缺陷。
_CTRL_PAT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Type3 字体通常是图内矢量文字，排除在判定之外
_TYPE3_HINT = "T3"


def ink_ratio(page: "pymupdf.Page", rect: "pymupdf.Rect", zoom: float) -> float:
    """统计矩形区域内有色像素占比。"""
    if rect.width < 0.8 or rect.height < 0.8:
        return 1.0                      # 太小，无法判断，视为正常
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect, alpha=False)
    if pix.width == 0 or pix.height == 0:
        return 1.0
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    return float((arr.mean(axis=2) < 128).mean())


def main() -> int:
    ap = argparse.ArgumentParser(description="像素级字形渲染验收")
    ap.add_argument("pdf")
    ap.add_argument("--zoom", type=float, default=6.0)
    ap.add_argument("--threshold", type=float, default=0.004,
                    help="墨迹占比低于此值视为「渲染空白」")
    ap.add_argument("--layout", choices=["lr", "tb"], default="lr")
    ns = ap.parse_args()

    path = Path(ns.pdf).resolve()
    doc = pymupdf.open(path)
    print("=" * 66)
    print(f"像素级字形验收: {path.name}  （zoom={ns.zoom} 阈值={ns.threshold}）")
    print("=" * 66)

    blanks: list[tuple[int, str, str, float]] = []
    checked = 0
    total_spans = 0

    for pno in range(doc.page_count):
        page = doc[pno]
        r = page.rect
        clip = (
            pymupdf.Rect(r.x0 + r.width / 2, r.y0, r.x1, r.y1)
            if ns.layout == "lr"
            else pymupdf.Rect(r.x0, r.y0 + r.height / 2, r.x1, r.y1)
        )
        for b in page.get_text("dict", clip=clip).get("blocks", []):
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                for s in line.get("spans", []):
                    txt = s.get("text", "")
                    if not txt.strip():
                        continue
                    # 排除图内乱码与 Type3 图内矢量文字
                    if _CTRL_PAT.search(txt):
                        continue
                    if _TYPE3_HINT in str(s.get("font", "")) and "Unnamed" in str(s.get("font", "")):
                        continue
                    total_spans += 1
                    rect = pymupdf.Rect(s["bbox"])
                    ratio = ink_ratio(page, rect, ns.zoom)
                    checked += 1
                    if ratio < ns.threshold:
                        blanks.append((pno + 1, txt.strip()[:30], s.get("font", ""), ratio))

    print(f"\n检查 span 总数: {total_spans}")
    print(f"渲染为空白（字形缺失）: {len(blanks)}")
    if blanks:
        print()
        print("--- 空白 span 清单（前 25 条）---")
        from collections import Counter

        by_font = Counter(f for _, _, f, _ in blanks)
        for pno, txt, font, ratio in blanks[:25]:
            print(f"  第{pno:>2}页 {txt!r:34} font={font[:30]:30} 墨迹={ratio:.4f}")
        print()
        print("按字体汇总:")
        for font, n in by_font.most_common():
            print(f"  {n:>4}  {font}")

    doc.close()
    ok = not blanks
    print()
    print("结论：" + ("所有文本字形渲染正常 ✅" if ok else "存在字形缺失 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
