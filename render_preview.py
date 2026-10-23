#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把对照版 PDF 的指定区域渲染成 PNG，用于人工/自动检查排版效果。

用法:
    python render_preview.py 输出.pdf [-o 预览目录] [-p 页号] [--dpi 120]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf


def render(
    pdf: Path,
    outdir: Path,
    pages: list[int] | None = None,
    dpi: int = 120,
    max_width: int = 2200,
) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    with pymupdf.open(pdf) as doc:
        targets = pages or list(range(doc.page_count))
        for pno in targets:
            if pno < 0 or pno >= doc.page_count:
                print(f"[跳过] 第 {pno + 1} 页不存在", file=sys.stderr)
                continue
            page = doc[pno]
            zoom = dpi / 72.0
            # 限制宽度，避免超宽页面渲染出巨大位图
            if page.rect.width * zoom > max_width:
                zoom = max_width / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            path = outdir / f"page-{pno + 1:03d}.png"
            pix.save(path)
            saved.append(path)
            print(f"[预览] 第 {pno + 1} 页 -> {path} ({pix.width}x{pix.height})")
    return saved


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染 PDF 页面为 PNG 预览")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("-p", "--pages", default=None, help="如 1,3 或 1-4")
    ap.add_argument("--dpi", type=int, default=120)
    ns = ap.parse_args()

    pdf = Path(ns.pdf).resolve()
    if not pdf.exists():
        print(f"[错误] 找不到 {pdf}", file=sys.stderr)
        return 2
    outdir = Path(ns.outdir).resolve() if ns.outdir else pdf.parent / (pdf.stem + "_preview")

    pages = None
    if ns.pages:
        pages = []
        for part in ns.pages.split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                pages.extend(range(int(a) - 1, int(b)))
            else:
                pages.append(int(part) - 1)

    render(pdf, outdir, pages, ns.dpi)
    print(f"\n预览目录: {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
