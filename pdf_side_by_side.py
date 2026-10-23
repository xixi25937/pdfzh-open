#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_side_by_side.py — 把「原文 PDF」与「译文 PDF」合并成逐页对照的双语文档。

本模块是 PDFMathTranslate(pdf2zh) 的**版式后处理**扩展：
pdf2zh 自带的 dual 模式是「第1页英文、第2页中文、第3页英文……」的交替排布，
并不是真正的同一页左右对照。这里实现同一页内：

    ┌──────────────┬──────────────┐
    │  原文(英)     │   译文(中)    │      ← lr 模式（默认, 需要横向页）
    ├──────────────┼──────────────┤
    │              │              │
    └──────────────┴──────────────┘
              lr (left-right)

    ┌─────────────────────────────┐
    │        原文(英)              │
    ├─────────────────────────────┤      ← tb 模式（竖排, 保持原始纸张方向）
    │        译文(中)              │
    └─────────────────────────────┘
              tb (top-bottom)

实现要点
--------
1. 使用 PyMuPDF 的 ``show_pdf_page`` 嵌入，而不是把页面渲染成位图。
   因此原文与译文都是**矢量**内容 —— 任意放大不失真，且文字依然可选中/可搜索。
2. 每一页都按原始页面尺寸的纵横比等比缩放后居中放置，**不拉伸、不裁剪**。
3. 通过统计文本块的横坐标分布自动判断原文是单栏还是双栏：
   双栏论文若并排缩到半页会导致字号过小，因此自动改用 tb 竖排。

作者: 为「具身智能」工作区定制
许可: 本模块为独立实现，可单独使用；调用 pdf2zh 时请遵守其 AGPL-3.0 许可。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Sequence

import pymupdf

# --------------------------------------------------------------------------
# 布局判定
# --------------------------------------------------------------------------

LayoutMode = Literal["auto", "lr", "tb"]


@dataclass(frozen=True)
class ColumnStats:
    """单页分栏统计结果。"""

    n_spans: int
    left_ratio: float
    right_ratio: float
    two_column: bool


def analyze_columns(page: "pymupdf.Page", min_spans: int = 40) -> ColumnStats:
    """判断一页是否为「双栏排版」。

    做法：取所有文本 span 的水平中心点，统计其落在页面左右半区的比例。
    单栏正文（含居中的标题）绝大多数 span 会横跨或靠近中线，
    而双栏排版会形成两个明显分离的簇。
    """
    width = page.rect.width
    mid = width / 2.0
    left = right = 0
    n = 0

    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:  # 跳过图片块
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                x0, _, x1, _ = span["bbox"]
                n += 1
                center = (x0 + x1) / 2.0
                # 宽度超过页面 55% 的 span（大标题、通栏公式/表格）不计入分栏统计
                if (x1 - x0) > width * 0.55:
                    continue
                if center < mid:
                    left += 1
                else:
                    right += 1

    counted = left + right
    if counted == 0:
        return ColumnStats(n, 0.0, 0.0, False)

    left_ratio = left / counted
    right_ratio = right / counted
    two_column = (
        n >= min_spans and min(left_ratio, right_ratio) >= 0.30
    )
    return ColumnStats(n, left_ratio, right_ratio, two_column)


def detect_layout(src_doc: "pymupdf.Document", sample: int = 20) -> tuple[LayoutMode, str]:
    """自动选择布局模式。

    返回 ``(模式, 判定理由)``。
    """
    pages = list(range(min(sample, src_doc.page_count)))
    if not pages:
        return "lr", "空文档，回退为左右布局"

    two_col_pages = 0
    for i in pages:
        try:
            if analyze_columns(src_doc[i]).two_column:
                two_col_pages += 1
        except Exception:
            continue

    ratio = two_col_pages / len(pages)
    if ratio >= 0.5:
        return (
            "tb",
            f"检测到 {two_col_pages}/{len(pages)} 页为双栏排版，"
            "若想一张纸放下整页可选上下布局",
        )
    return "lr", f"检测到单栏排版（仅 {two_col_pages}/{len(pages)} 页为双栏），使用左右布局"


# --------------------------------------------------------------------------
# 页面放置
# --------------------------------------------------------------------------


@dataclass
class MergeOptions:
    """对照版式的参数。

    默认值刻意与成熟方案（BabelDOC / PDFMathTranslate 的 dual 输出）对齐：
    输出页尺寸 = 原文页尺寸 × 2，两页 **100% 原尺寸、原始坐标** 并排放置，
    不加任何页边留白和栏间距。这样：

    * 原文一个像素都没有被缩小；
    * 页面尺寸正好是标准纸张的两倍（Letter → 1224×792pt），便于打印；
    * 原页自带的页边距天然形成视觉分隔，不需要额外白边。

    想要边框感（留白 + 分隔线 + 原文/译文标签）时设 ``framed=True``。
    """

    mode: LayoutMode = "lr"
    padding: float = 0.0           # 页边留白 (pt)；0 = 原页贴边铺满
    gutter: float = 0.0            # 左右/上下两栏之间的间隙 (pt)
    label_left: str = "原 文 · English"
    label_right: str = "译 文 · 中文"
    draw_separator: bool = False
    framed: bool = False           # True 时启用留白 + 分隔线 + 标签
    force_page_size: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        # framed 模式下一键打开装饰；显式传了非零值则以传入值为准
        if self.framed:
            if not self.padding:
                self.padding = 12.0
            if not self.gutter:
                self.gutter = 18.0
            self.draw_separator = True


def _place_fit(
    target_page: "pymupdf.Page",
    src_doc: "pymupdf.Document",
    src_pno: int,
    box: "pymupdf.Rect",
) -> "pymupdf.Rect":
    """把 ``src_doc`` 的第 ``src_pno`` 页等比缩放居中放入 ``box``，返回实际矩形。"""
    src_rect = src_doc[src_pno].rect
    if src_rect.width <= 0 or src_rect.height <= 0:
        return box

    scale = min(box.width / src_rect.width, box.height / src_rect.height)
    w = src_rect.width * scale
    h = src_rect.height * scale
    # 在 box 内居中
    x0 = box.x0 + (box.width - w) / 2.0
    y0 = box.y0 + (box.height - h) / 2.0
    fitted = pymupdf.Rect(x0, y0, x0 + w, y0 + h)

    target_page.show_pdf_page(fitted, src_doc, src_pno)
    return fitted


def _find_cjk_font() -> tuple["pymupdf.Font", str] | None:
    """找一个可用的中文字体文件。

    注意：PyMuPDF 内置的 ``china-s`` / ``cjk`` 在**嵌入时不会保留中文字形**
    （渲染出来只剩字母和空白），因此必须使用真实的 CJK 字体文件。
    """
    candidates = [
        # 项目自带字体（若已从 babeldoc 缓存下载）
        Path.home() / ".cache" / "babeldoc" / "fonts" / "SourceHanSerifCN-Regular.ttf",
        # Windows 自带
        Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
        Path(r"C:\Windows\Fonts\NotoSerifSC-VF.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        # macOS / Linux
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    # 再扫一遍 babeldoc 字体缓存目录（文件名可能带版本/子集后缀）
    for base in (
        Path.home() / ".cache" / "babeldoc" / "fonts",
        Path.home() / ".cache" / "babeldoc",
    ):
        try:
            if base.is_dir():
                candidates.extend(sorted(base.glob("SourceHan*.ttf"))[:4])
        except Exception:
            pass

    for path in candidates:
        try:
            if path.exists():
                return pymupdf.Font(fontfile=str(path)), str(path)
        except Exception:
            continue
    return None


_CJK_FONT_CACHE: list[tuple["pymupdf.Font", str]] | None = None


def _cjk_font() -> tuple["pymupdf.Font", str] | None:
    global _CJK_FONT_CACHE
    if _CJK_FONT_CACHE is None:
        found = _find_cjk_font()
        _CJK_FONT_CACHE = [found] if found else []
    return _CJK_FONT_CACHE[0] if _CJK_FONT_CACHE else None


def _draw_badge(
    page: "pymupdf.Page", anchor: "pymupdf.Rect", text: str, align_right: bool
) -> None:
    """在栏目内容上方画一个小标签，明确区分原文/译文。"""
    if not text:
        return

    fontsize = 8.5
    found = _cjk_font()
    if found is not None:
        font, fontfile = found
        width = font.text_length(text, fontsize=fontsize)
        page.insert_text(
            pymupdf.Point(
                (anchor.x1 - width) if align_right else anchor.x0, 12.0
            ),
            text,
            fontname="pdfzhCJK",
            fontfile=fontfile,
            fontsize=fontsize,
            color=(0.42, 0.45, 0.50),
        )
        return

    # 没有可用中文字体时退化为内置字体（中文可能不显示，但英文标签仍可读）
    try:
        width = pymupdf.get_text_length(text, fontname="helv", fontsize=fontsize)
    except Exception:
        width = len(text) * fontsize * 0.5
    page.insert_text(
        pymupdf.Point((anchor.x1 - width) if align_right else anchor.x0, 12.0),
        text,
        fontname="helv",
        fontsize=fontsize,
        color=(0.42, 0.45, 0.50),
    )


def _writable_target(path: Path) -> Path:
    """返回一个可以直接新建的路径，避免覆盖已存在的文件。

    背景：某些受限环境（沙箱 / 严格 ACL）**禁止删除或替换已存在的文件**，
    而 PyMuPDF 保存到已存在的路径时会先删原文件，于是抛
    ``PermissionError: [WinError 5]``。这里改成自动换名
    （``xxx.pdf`` -> ``xxx-2.pdf``），既能跑通，也避免静默覆盖用户的成果。
    """
    if not path.exists():
        return path
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists():
            return cand
    return path


def merge_side_by_side(
    original: str | Path,
    translated: str | Path,
    output: str | Path,
    options: MergeOptions | None = None,
    only_pages: Sequence[int] | None = None,
) -> dict:
    """把原文与译文逐页合并为对照文档。

    参数
    ----
    original : 英文原版 PDF 路径
    translated : 中文译文 PDF 路径（页码需与原文一一对应）
    output : 输出 PDF 路径
    options : :class:`MergeOptions`
    only_pages : 只输出这些页（0 基页码）。用于 `--pages 1-3` 这类
        只翻译部分页的场景：此时输出应当只含这几页，而不是把
        未翻译的页面也一起塞进来。

    返回
    ----
    dict —— 处理摘要，含页数、所用模式、输出尺寸等。
    """
    opts = options or MergeOptions()
    original = Path(original)
    translated = Path(translated)
    output = Path(output)

    src = pymupdf.open(original)
    dst = pymupdf.open(translated)

    try:
        if src.page_count != dst.page_count:
            raise ValueError(
                f"原文与译文页数不一致（{src.page_count} vs {dst.page_count}），无法逐页对照。"
            )
        if src.page_count == 0:
            raise ValueError("输入 PDF 没有任何页面。")

        if only_pages is None:
            page_indices = list(range(src.page_count))
        else:
            page_indices = [p for p in only_pages if 0 <= p < src.page_count]
            if not page_indices:
                raise ValueError("only_pages 没有落在有效页码范围内。")

        mode: LayoutMode = opts.mode
        reason = "用户指定布局"
        if mode == "auto":
            mode, reason = detect_layout(src)

        out = pymupdf.open()
        for pno in page_indices:
            w = opts.force_page_size[0] if opts.force_page_size else src[pno].rect.width
            h = opts.force_page_size[1] if opts.force_page_size else src[pno].rect.height

            if mode == "lr":
                page_w = w * 2 + opts.gutter + opts.padding * 2
                page_h = h + opts.padding * 2
            elif mode == "tb":
                page_w = w + opts.padding * 2
                page_h = h * 2 + opts.gutter + opts.padding * 2
            else:
                raise ValueError(f"未知布局模式: {mode}")

            page = out.new_page(width=page_w, height=page_h)

            if mode == "lr":
                left_box = pymupdf.Rect(
                    opts.padding, opts.padding, opts.padding + w, opts.padding + h
                )
                right_box = pymupdf.Rect(
                    left_box.x1 + opts.gutter, opts.padding,
                    left_box.x1 + opts.gutter + w, opts.padding + h,
                )
            else:  # tb
                top_box = pymupdf.Rect(
                    opts.padding, opts.padding, opts.padding + w, opts.padding + h
                )
                bottom_box = pymupdf.Rect(
                    opts.padding, top_box.y1 + opts.gutter,
                    opts.padding + w, top_box.y1 + opts.gutter + h,
                )
                left_box, right_box = top_box, bottom_box

            # 顺序很重要：show_pdf_page 是把原页作为 Form XObject 插入的，
            # 只有在它之后绘制的内容才会显示在页面上方。
            r_src = _place_fit(page, src, pno, left_box)
            r_dst = _place_fit(page, dst, pno, right_box)

            if opts.draw_separator:
                if mode == "lr":
                    x_sep = (left_box.x1 + right_box.x0) / 2.0
                    page.draw_line(
                        pymupdf.Point(x_sep, opts.padding * 0.5),
                        pymupdf.Point(x_sep, page_h - opts.padding * 0.5),
                        color=(0.82, 0.84, 0.87),
                        width=0.7,
                    )
                else:
                    y_sep = (top_box.y1 + bottom_box.y0) / 2.0
                    page.draw_line(
                        pymupdf.Point(opts.padding * 0.5, y_sep),
                        pymupdf.Point(page_w - opts.padding * 0.5, y_sep),
                        color=(0.82, 0.84, 0.87),
                        width=0.7,
                    )

            _draw_badge_enabled = opts.framed and bool(opts.label_left or opts.label_right)
            if _draw_badge_enabled:
                _draw_badge(page, r_src, opts.label_left, align_right=False)
                _draw_badge(page, r_dst, opts.label_right, align_right=True)

        out.set_metadata(
            {
                "title": f"{original.stem} — 中英对照",
                "producer": "pdfzh side-by-side merger",
                "creator": "pdfzh",
            }
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        target = _writable_target(output)
        out.save(str(target), deflate=True, garbage=4, clean=True)
        output = target

        summary = {
            "pages": len(page_indices),
            "source_pages": [p + 1 for p in page_indices],
            "mode": mode,
            "reason": reason,
            "output": str(output),
            "size_bytes": output.stat().st_size if output.exists() else 0,
        }
        out.close()
        return summary
    finally:
        src.close()
        dst.close()


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import json

    ap = argparse.ArgumentParser(description="把原文/译文 PDF 合并为双语对照版")
    ap.add_argument("original", help="英文原版 PDF")
    ap.add_argument("translated", help="中文译文 PDF")
    ap.add_argument("-o", "--output", required=True, help="输出 PDF 路径")
    ap.add_argument(
        "--layout", choices=["auto", "lr", "tb"], default="auto",
        help="版式：lr 左右并排 / tb 上下对照 / auto 自动判断（默认）",
    )
    ap.add_argument("--gutter", type=float, default=18.0, help="两栏间距 pt")
    ap.add_argument("--padding", type=float, default=12.0, help="页边留白 pt")
    ns = ap.parse_args()

    info = merge_side_by_side(
        ns.original,
        ns.translated,
        ns.output,
        MergeOptions(mode=ns.layout, gutter=ns.gutter, padding=ns.padding),
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))
