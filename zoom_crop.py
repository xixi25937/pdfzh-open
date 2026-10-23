#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""放大渲染 PDF 的指定矩形区域，用于检查细节（如标签、公式、字形）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf


def main() -> int:
    ap = argparse.ArgumentParser(description="放大渲染 PDF 局部区域")
    ap.add_argument("pdf")
    ap.add_argument("-p", "--page", type=int, default=1, help="页码，从 1 开始")
    ap.add_argument(
        "-r", "--rect", required=True,
        help="裁剪区域 x0,y0,x1,y1（单位 pt）",
    )
    ap.add_argument("-o", "--output", required=True, help="输出 PNG 路径")
    ap.add_argument("--zoom", type=float, default=4.0, help="放大倍数")
    ns = ap.parse_args()

    x0, y0, x1, y1 = (float(v) for v in ns.rect.split(","))
    pdf = Path(ns.pdf).resolve()
    with pymupdf.open(pdf) as doc:
        page = doc[ns.page - 1]
        clip = pymupdf.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(ns.zoom, ns.zoom), clip=clip, alpha=False
        )
        out = Path(ns.output).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(out)
        print(f"已保存 {out} ({pix.width}x{pix.height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
