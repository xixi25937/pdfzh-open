#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：检查一份 PDF 的对象/xref 完整性。

用途：当 MuPDF 报出
    "MuPDF error: format error: object out of range (... 0 R); xref size ..."
这类**格式提示**时，用它判断到底是「无害提示」还是「真的坏文件」。

检查三项：
  1. 页面内容流里是否引用了超出 xref 范围的对象
  2. 每页 Resources 里的字体/图片对象号是否合法
  3. 每页能否实际渲染出位图

用法:
    python diag_object.py 文件.pdf
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pymupdf

REF_RE = re.compile(rb"(\d+)\s+0\s+R")


def check(path: Path) -> int:
    print("=" * 64)
    print(f"检查 {path.name}")
    print("=" * 64)

    doc = pymupdf.open(path)
    n = doc.xref_length()
    print(f"xref_length = {n}, page_count = {doc.page_count}")

    # ---- 1. 内容流中的超范围引用 ----
    bad_refs: dict[int, set[int]] = {}
    for pno in range(doc.page_count):
        page = doc[pno]
        streams: list[bytes] = []
        try:
            kind, value = doc.xref_get_key(page.xref, "Contents")
        except Exception:
            kind, value = None, None

        if kind == "array":
            for m in re.finditer(r"(\d+)\s+0\s+R", value or ""):
                try:
                    streams.append(doc.xref_stream(int(m.group(1))))
                except Exception:
                    pass
        else:
            try:
                streams.append(page.read_contents())
            except Exception:
                pass

        for stream in streams:
            for m in REF_RE.finditer(stream or b""):
                num = int(m.group(1))
                if num >= n:
                    bad_refs.setdefault(pno + 1, set()).add(num)

    if bad_refs:
        print("\n[注意] 页面内容流中引用了超出 xref 范围的对象：")
        for pno, nums in sorted(bad_refs.items())[:10]:
            print(f"  第 {pno} 页 -> {sorted(nums)}")
    else:
        print("\n[OK] 页面内容流中没有超范围引用")

    # ---- 2. 资源对象号合法性 ----
    print("\n逐页资源完整性：")
    problems = 0
    for pno in range(doc.page_count):
        page = doc[pno]
        try:
            for f in page.get_fonts(full=True):
                if f[0] >= n:
                    print(f"  第 {pno + 1} 页 字体 xref {f[0]} 超出范围")
                    problems += 1
            for img in page.get_images(full=True):
                if img[0] >= n:
                    print(f"  第 {pno + 1} 页 图片 xref {img[0]} 超出范围")
                    problems += 1
        except Exception as exc:
            print(f"  第 {pno + 1} 页读取资源失败: {exc}")
            problems += 1
    if problems == 0:
        print("  全部页面的字体/图片对象号都在合法范围内 ✅")

    # ---- 3. 逐页渲染 ----
    print("\n逐页渲染测试：")
    render_fail = 0
    for pno in range(doc.page_count):
        try:
            pix = doc[pno].get_pixmap(dpi=40)
            if pix.width == 0 or pix.height == 0:
                print(f"  第 {pno + 1} 页渲染为空")
                render_fail += 1
        except Exception as exc:
            print(f"  第 {pno + 1} 页渲染异常: {type(exc).__name__}: {exc}")
            render_fail += 1
    print(f"  渲染失败页数: {render_fail}/{doc.page_count}")

    doc.close()
    healthy = not render_fail and not problems
    print(
        "\n结论：",
        "无真实损坏，格式报错属于无害提示"
        if healthy
        else "存在真实损坏，需要处理",
    )
    return 0 if healthy else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="检查 PDF 对象/xref 完整性")
    ap.add_argument("pdf")
    ns = ap.parse_args()

    path = Path(ns.pdf).resolve()
    if not path.exists():
        print(f"[错误] 找不到 {path}", file=sys.stderr)
        return 2
    return check(path)


if __name__ == "__main__":
    sys.exit(main())
