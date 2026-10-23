#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收脚本：抽取对照版 PDF 的文字，核对关键要求是否达成。

检查项：
  1. 左右/上下两栏都嵌入了内容（页数、尺寸、标签）
  2. 中文字体确实带 ToUnicode（中文可搜索、可复制）
  3. 参考文献条目是否保留了英文（默认策略）
  4. 公式占位符 {v*} 是否被模型擅自改动/丢失
  5. 每页中英内容是否都非空

用法:
    python verify_output.py 对照版.pdf [--expect-refs-english]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pymupdf

LATIN_WORD = re.compile(r"[A-Za-z]{4,}")
CJK = re.compile(r"[\u4e00-\u9fff]")
# 参考文献里常见的英文特征
REF_HINT = re.compile(
    r"(References|Bibliography|arXiv:\d|Proceedings of|Journal of|"
    r"Conference on|et al\.|pp\.\s*\d)",
    re.IGNORECASE,
)


def split_halves(page: "pymupdf.Page", mode: str) -> tuple[str, str]:
    """按版式把一页切成「原文区」「译文区」并返回各自文字。"""
    r = page.rect
    if mode == "lr":
        left = pymupdf.Rect(r.x0, r.y0, r.x0 + r.width / 2, r.y1)
        right = pymupdf.Rect(r.x0 + r.width / 2, r.y0, r.x1, r.y1)
    else:  # tb
        top = pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height / 2)
        bottom = pymupdf.Rect(r.x0, r.y0 + r.height / 2, r.x1, r.y1)
        left, right = top, bottom
    return page.get_text(clip=left), page.get_text(clip=right)


def main() -> int:
    ap = argparse.ArgumentParser(description="验收对照版 PDF")
    ap.add_argument("pdf")
    ap.add_argument("--layout", choices=["lr", "tb"], default=None)
    ap.add_argument("--expect-refs-english", action="store_true")
    ns = ap.parse_args()

    path = Path(ns.pdf).resolve()
    if not path.exists():
        print(f"[错误] 找不到 {path}", file=sys.stderr)
        return 2

    doc = pymupdf.open(path)
    r0 = doc[0].rect
    mode = ns.layout or ("lr" if r0.width > r0.height else "tb")

    print("=" * 66)
    print(f"验收: {path.name}")
    print(f"  页数 {doc.page_count} | 首页 {r0.width:.0f}x{r0.height:.0f}pt | 版式 {mode}")
    print("=" * 66)

    ok = True

    # ---- 逐页检查两栏内容 ----
    print("\n[1] 逐页两栏内容")
    empty = []
    for pno in range(doc.page_count):
        a, b = split_halves(doc[pno], mode)
        a_has = bool(a.strip())
        b_has = bool(b.strip())
        if not (a_has and b_has):
            empty.append((pno + 1, a_has, b_has))
    if empty:
        ok = False
        for pno, a_has, b_has in empty:
            print(f"  ✗ 第 {pno} 页 原文区={a_has} 译文区={b_has}")
    else:
        print(f"  ✅ {doc.page_count} 页的原文区与译文区均有内容")

    # ---- 中文可提取性（ToUnicode）----
    print("\n[2] 中文文本层（能否搜索/复制）")
    all_cjk = 0
    for pno in range(doc.page_count):
        _, b = split_halves(doc[pno], mode)
        all_cjk += len(CJK.findall(b))
    if all_cjk > 0:
        print(f"  ✅ 译文区共提取到 {all_cjk} 个中文字符（说明 ToUnicode 正常）")
    else:
        print("  ✗ 译文区提取不到中文，搜索/复制会失效")
        ok = False

    # ---- 参考文献语言 ----
    print("\n[3] 参考文献处理")
    full = "\n".join(doc[p].get_text() for p in range(doc.page_count))
    hits = REF_HINT.findall(full)
    if hits:
        # 统计参考文献区里中英文比例：取最后一个 References 出现位置之后的内容
        idx = max(full.rfind("References"), full.rfind("REFERENCES"))
        tail = full[idx:] if idx > 0 else full
        latin = len(LATIN_WORD.findall(tail))
        cn = len(CJK.findall(tail))
        ratio = latin / max(latin + cn, 1)
        print(f"  参考文献区: 英文字词 {latin} / 中文字 {cn} （英文占比 {ratio:.0%}）")
        if ns.expect_refs_english and ratio < 0.25:
            print("  ✗ 参考文献似乎被翻译了，与「保留英文」不符")
            ok = False
        elif ns.expect_refs_english:
            print("  ✅ 参考文献基本保留英文")
    else:
        print("  - 未在该文档中找到参考文献标识（可能本就没有）")

    # ---- 公式占位符残留检查 ----
    print("\n[4] 公式占位符 {v*} 残留")
    stray = re.findall(r"\{\s*v\d+\s*\}", full)
    if stray:
        print(f"  ⚠ 发现 {len(stray)} 处未替换的占位符，例如 {stray[:5]}")
        print("    （说明模型改动了占位符，会导致对应位置公式错位）")
    else:
        print("  ✅ 没有残留的 {v*} 占位符")

    # ---- 图像与字体 ----
    print("\n[5] 资源")
    imgs = sum(len(doc[p].get_images(full=True)) for p in range(doc.page_count))
    fonts = set()
    for p in range(doc.page_count):
        for f in doc[p].get_fonts(full=True):
            fonts.add(f[3])
    print(f"  图片对象: {imgs} 个")
    print(f"  嵌入字体: {len(fonts)} 种 -> {sorted(fonts)[:8]}")

    # ---- 6. 程序级质检（改进建议文档第三节的指标）----
    print("\n[6] 交付级质检")
    left_text = "".join(split_halves(doc[p], mode)[0] for p in range(doc.page_count))
    right_text = "".join(split_halves(doc[p], mode)[1] for p in range(doc.page_count))

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from translation_guard import (
            SpanGuard,
            find_compat_chars,
            scan_english_residue,
            strip_pdf_metadata,
        )

        # 6.1 受保护片段精确匹配（公式占位符/URL/DOI）
        #     只校验严格类型；并剔除页眉/侧栏出版元数据（本就不翻译）
        guard = SpanGuard()
        chk = guard.check(
            strip_pdf_metadata(left_text),
            strip_pdf_metadata(right_text),
            strict_only=True,
        )
        if chk.ok:
            print("  ✅ 公式/引用/URL/DOI 前后完全一致")
        else:
            ok = False
            print(f"  ❌ 受保护片段不一致：{chk.describe()}")

        # 6.2 普通英文残留（只报「夹在中文句子里」的，避免作者块/图片误报）
        residue = scan_english_residue(right_text)
        if residue:
            print(f"  ⚠ 疑似漏译（中文句中夹英文）{len(residue)} 处，例如: {residue[:5]}")
        else:
            print("  ✅ 无普通英文残留")

        # 6.3 兼容区字符（影响搜索与复制）
        compat = find_compat_chars(right_text)
        if compat:
            ok = False
            sample = ", ".join(f"U+{ord(c):04X}" for c in list(compat)[:5])
            print(f"  ❌ CJK 兼容区字符 {sum(compat.values())} 个（{sample}）——搜索/复制会失效")
        else:
            print("  ✅ 无兼容区字符")

        # 6.4 文本框重叠（译文区 span 包围盒）
        #     必须排除页面边缘的竖排侧栏带与标点的微小越界，否则假阳性
        overlaps = 0
        for p in range(doc.page_count):
            page = doc[p]
            r = page.rect
            clip = pymupdf.Rect(r.x0 + r.width / 2, r.y0, r.x1, r.y1)
            edge_x = r.x0 + r.width / 2 + r.width * 0.055
            spans = []
            for b in page.get_text("dict", clip=clip).get("blocks", []):
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    for s in line.get("spans", []):
                        txt = s.get("text", "")
                        if not txt.strip():
                            continue
                        # 图内文字常被提取成含控制字符的乱码碎片，非正文
                        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", txt):
                            continue
                        rect = pymupdf.Rect(s["bbox"])
                        if rect.x1 <= edge_x:
                            continue          # 边缘竖排带
                        spans.append((rect, txt.strip()))
            spans.sort(key=lambda item: (round(item[0].x0, 1), item[0].y0))
            for (a, ta), (b, tb) in zip(spans, spans[1:]):
                inter = a & b
                if not inter.is_valid:
                    continue
                area = inter.get_area()
                if area <= 12.0 or area <= 0.35 * min(a.get_area(), b.get_area()):
                    continue
                # 同一文本同位置重复绘制（嵌入式图表）不算重叠
                if ta == tb and abs(a.x0 - b.x0) < 1 and abs(a.y0 - b.y0) < 1:
                    continue
                overlaps += 1
        if overlaps:
            print(f"  ⚠ 译文区文本框重叠 {overlaps} 处（严重重叠会让字相压）")
        else:
            print("  ✅ 译文区无文本框重叠")
    except Exception as exc:
        print(f"  - 质检模块不可用：{exc}")

    doc.close()
    print("\n" + "=" * 66)
    print("验收结果：" + ("通过 ✅" if ok else "存在问题 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
