#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：为什么小节标题没有被翻译？

用版面检测模型（doclayout ONNX）跑一页，看标题区域被分到哪个类别。
converter 会把 vcls = [abandon, figure, table, isolate_formula, formula_caption]
这些类别当作「保护区域」跳过翻译；若标题被误判成其中一类，就会原样保留英文。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pdfzh import setup_workspace_env  # noqa: E402

setup_workspace_env(quiet=True)

# 注意：本仓库的主入口叫 pdfzh.py，会遮蔽已安装的 pdfzh 包，
# 因此这里用 importlib 直接从站点包按路径加载 doclayout 模块。
import importlib.util  # noqa: E402

_spec = importlib.util.find_spec("pdf2zh.doclayout")
if _spec is None or not _spec.origin:
    raise SystemExit("找不到 pdf2zh.doclayout")
_dl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dl)  # type: ignore[union-attr]
OnnxModel = _dl.OnnxModel

VCLS = ["abandon", "figure", "table", "isolate_formula", "formula_caption"]

PDF = Path(r"C:\Users\Lenovo\Desktop\具身智能\2410.24164v4.pdf")
PNO = 4                                  # 第 5 页（0 基）
TARGET = "Pre-training and post-training"

model = OnnxModel.load_available()
src = pymupdf.open(PDF)
page = src[PNO]

pix = page.get_pixmap()
img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)[:, :, ::-1]
res = model.predict(img, imgsz=int(pix.height / 32) * 32)[0]

W, H = pix.width, pix.height
PW, PH = page.rect.width, page.rect.height

print(f"页面 {PW:.0f}x{PH:.0f}pt -> 位图 {W}x{H}px")
print(f"检测到 {len(res.boxes)} 个区域")
print()

hits = page.search_for(TARGET)
print(f"搜索 {TARGET!r}: {len(hits)} 处命中")
for h in hits:
    px0 = h.x0 * W / PW
    px1 = h.x1 * W / PW
    py0 = (1 - h.y1 / PH) * H          # 位图 y 向下，PDF y 向上
    py1 = (1 - h.y0 / PH) * H
    print(f"  标题框(pt) = ({h.x0:.1f},{h.y0:.1f})-({h.x1:.1f},{h.y1:.1f})")
    print(f"  标题框(px) = ({px0:.0f},{py0:.0f})-({px1:.0f},{py1:.0f})")

    overlapped = []
    for b in res.boxes:
        bx0, by0, bx1, by1 = (float(v) for v in b.xyxy.squeeze())
        if not (bx1 < px0 or bx0 > px1 or by1 < py0 or by0 > py1):
            cls = res.names[int(b.cls)]
            overlapped.append((cls, float(b.conf), bx0, by0, bx1, by1))

    if not overlapped:
        print("  -> 没有任何检测框与标题重叠（可能被归入大块正文区域）")
    for cls, conf, bx0, by0, bx1, by1 in overlapped:
        mark = "  <<< 会被跳过翻译" if cls in VCLS else ""
        print(
            f"  -> 重叠区域 class={cls!r} conf={conf:.2f} "
            f"box=({bx0:.0f},{by0:.0f})-({bx1:.0f},{by1:.0f}){mark}"
        )

# 顺便统计整页各类别占比
from collections import Counter  # noqa: E402

cnt = Counter(res.names[int(b.cls)] for b in res.boxes)
print()
print("整页类别分布:", dict(cnt))
print(f"跳过翻译的类别合计: {sum(v for k, v in cnt.items() if k in VCLS)}")
src.close()
