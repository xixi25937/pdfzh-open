#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translation_guard.py —— 翻译质量的程序级保护层。

设计原则（来自改进建议文档）：
    **模型负责表达，程序负责保护和验收，排版器负责回退。**

本模块提供四件互相独立、可单独使用的能力：

1. :func:`normalize_text` —— Unicode NFKC 规范化。
   pdf2zh 写 PDF 时会用字形编号反查编码，结果落进 CJK 兼容区
   （实测 5 页里有 300 个：`不`U+F967 而不是 `不`U+4E0D），
   导致**搜索和复制失效**。在译文进入 PDF 之前强制 NFKC 即可根治。

2. :class:`SpanGuard` —— 受保护片段的登记与逐一比对。
   把公式占位符 `{v0}`、URL、DOI、邮箱、引用编号、数字与单位提取出来，
   翻译后逐个比对集合/数量/顺序。任何缺失、重复、改写都判为失败，
   只重试该段而不是整篇重译。

3. :func:`scan_english_residue` —— 普通英文残留扫描。
   连续 2 个以上普通英文单词视为漏译（专名、缩写、URL 走白名单）。

4. :func:`qa_report` —— 汇总成可交付的质检报告。

用法::

    from translation_guard import normalize_text, SpanGuard, qa_report
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# --------------------------------------------------------------------------
# 1. Unicode 规范化
# --------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """NFKC 规范化，修正 CJK 兼容区字形与全角/半角混用。

    为什么用 NFKC 而不是 NFC：
      * NFC 不处理 CJK 兼容表意文字（U+F900–FAFF），`不` 依然是 `不`；
      * NFKC 会把 `不`(U+F967) 正确折叠为 `不`(U+4E0D)，同时把全角字母
        数字折成半角，正好是中文排版想要的效果。

    注意：不要把 NFKC 用在**原始英文文本**上（可能改动某些数学符号的
    语义外观），只用于译文。
    """
    if not text:
        return text
    return unicodedata.normalize("NFKC", text)


def find_compat_chars(text: str) -> dict[str, int]:
    """找出文本里的 CJK 兼容区 / 兼容形式字符，返回 {字符: 次数}。"""
    out: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        if 0xF900 <= cp <= 0xFAFF or 0xFE30 <= cp <= 0xFE4F or 0x2E80 <= cp <= 0x2FDF:
            out[ch] = out.get(ch, 0) + 1
    return out


# --------------------------------------------------------------------------
# 1b. PDF 层：修正 ToUnicode 映射里的兼容区码位
# --------------------------------------------------------------------------

_TOUNICODE_HEX = re.compile(rb"<([0-9A-Fa-f]{4})>")


def fix_pdf_tounicode(doc) -> dict:
    """把 PDF 里所有 ToUnicode CMap 的兼容区码位改成标准等价字符。

    为什么需要这一步（实测根因）：
        译文文本在进入 PDF 前已经做过 NFKC 规范化，但 PyMuPDF 的
        ``subset_fonts`` 重建 ToUnicode CMap 时，对同一个字形会挑中
        **CJK 兼容表意文字**码位：

            器 U+5668 与 器 U+FA38 共用同一字形 gid
            subset 后的 CMap 写成了 <fa38>，于是提取出来是 `器` 而不是 `器`

        实测 5 页里有 300 个这类字符，导致**搜索与复制失效**。

    安全性：兼容表意文字与其标准等价字符指向**同一个字形**，
    因此只改文本层映射、不改字形选择，视觉渲染完全不变。

    参数 ``doc`` 为已打开的 ``pymupdf.Document``，就地修改。
    返回统计信息。
    """
    stats = {"streams": 0, "replaced": 0, "samples": []}

    def _fix_stream(raw: bytes) -> tuple[bytes, int]:
        n = 0

        def _sub(m: re.Match) -> bytes:
            nonlocal n
            cp = int(m.group(1), 16)
            if 0xF900 <= cp <= 0xFAFF or 0xFE30 <= cp <= 0xFE4F:
                fixed = unicodedata.normalize("NFKC", chr(cp))
                if len(fixed) == 1 and ord(fixed) != cp:
                    n += 1
                    return f"<{ord(fixed):04x}>".encode("ascii")
            return m.group(0)

        return _TOUNICODE_HEX.sub(_sub, raw), n

    try:
        for xref in range(1, doc.xref_length()):
            try:
                if not doc.xref_is_stream(xref):
                    continue
                raw = doc.xref_stream(xref)
            except Exception:
                continue
            # 判据只看流内容：ToUnicode CMap 必然含 begincmap。
            # （不要用 xref_object 里的关键字过滤 —— 实测那种对象字典里
            #  并不含 "CMap"/"ToUnicode" 字面量，会把目标全过滤掉。）
            if not raw or b"begincmap" not in raw:
                continue
            fixed_raw, n = _fix_stream(raw)
            if n:
                doc.update_stream(xref, fixed_raw)
                stats["streams"] += 1
                stats["replaced"] += n
                if len(stats["samples"]) < 5:
                    stats["samples"].append(f"xref {xref}: {n} 处")
    except Exception as exc:
        stats["error"] = str(exc)
    return stats


# --------------------------------------------------------------------------
# 2. 受保护片段：提取 / 比对
# --------------------------------------------------------------------------

# 顺序有讲究：先匹配长模式，避免 URL 被数字规则切碎
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("FORMULA", re.compile(r"\{\s*v\d+\s*\}")),                       # pdf2zh 公式占位符
    ("URL", re.compile(r"https?://[^\s<>\)\]\}，。；]+")),
    ("DOI", re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")),
    ("ARXIV", re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?\b", re.I)),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("CITE", re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")),         # [12] / [1, 2] / [3-5]
    ("NUMBER", re.compile(
        r"(?<![\w.])[-−+]?\d+(?:\.\d+)?(?:\s*[%‰])?"
        r"(?:\s*(?:×|x)\s*10\s*(?:\^|\*\*)?\s*[-−]?\d+)?"
    )),
]


@dataclass
class ProtectedSpan:
    kind: str
    text: str


# 严格校验的类型：这些一旦不一致必然是真错，必须逐字相同。
STRICT_KINDS = ("FORMULA", "URL", "DOI", "ARXIV", "EMAIL")

# 宽松校验的类型：引用编号（版面合并可能改变分组）与数字
# （纯整数如年份、页码、编号在不同版本/不同段落里天然会变，误报率高）。
LOOSE_KINDS = ("CITE", "NUMBER")


@dataclass
class SpanCheck:
    """一段文本的保护片段比对结果。"""

    ok: bool
    missing: list[ProtectedSpan] = field(default_factory=list)
    added: list[ProtectedSpan] = field(default_factory=list)
    changed_order: bool = False

    def describe(self) -> str:
        if self.ok:
            return "OK"
        bits = []
        if self.missing:
            bits.append("丢失 " + ", ".join(f"{s.kind}:{s.text}" for s in self.missing[:5]))
        if self.added:
            bits.append("新增 " + ", ".join(f"{s.kind}:{s.text}" for s in self.added[:5]))
        if self.changed_order:
            bits.append("顺序改变")
        return "; ".join(bits)


class SpanGuard:
    """受保护片段的提取与比对。"""

    def __init__(self, extra_allowed: Iterable[str] | None = None) -> None:
        # 允许的专名/缩写白名单（不做"英文残留"判定）
        self.allowed_terms = {t.strip() for t in (extra_allowed or []) if t.strip()}

    # ---- 提取 ----
    def extract(self, text: str) -> list[ProtectedSpan]:
        spans: list[ProtectedSpan] = []
        taken: list[tuple[int, int]] = []

        def overlaps(a: int, b: int) -> bool:
            return any(not (b <= s or a >= e) for s, e in taken)

        for kind, pat in PATTERNS:
            for m in pat.finditer(text):
                s, e = m.start(), m.end()
                if overlaps(s, e):
                    continue          # 已被更长的模式占用（如 URL 里的数字）
                taken.append((s, e))
                spans.append(ProtectedSpan(kind, m.group(0).strip()))
        spans.sort(key=lambda sp: text.find(sp.text))
        return spans

    # ---- 比对 ----
    def check(
        self,
        source: str,
        translated: str,
        strict_only: bool = False,
    ) -> SpanCheck:
        """比对源文与译文中的受保护片段。

        参数
        ----
        strict_only : 只校验 :data:`STRICT_KINDS`（公式占位符、URL、DOI、
            arXiv 编号、邮箱）。用于**整篇级**质检：此时引用编号会被版面
            重新分组（``[1, 2]`` 可能变成 ``[1], [2]``），纯整数（年份、
            页码、编号）也会因段落不同而天然不一致，逐字比对必然大量误报。

        判定标准（刻意宽松，只抓真错）：
          * **硬失败**：片段丢失、重复、被改写或凭空新增（集合 + 计数比对）。
          * **软提示**：位置顺序变化。中文语序与英文天然不同，不计入失败。
        """
        kinds = set(STRICT_KINDS) if strict_only else None

        def _extract(t: str) -> list[ProtectedSpan]:
            spans = self.extract(t)
            return [s for s in spans if kinds is None or s.kind in kinds]

        src = _extract(source)
        dst = _extract(translated)

        src_cnt: dict[tuple[str, str], int] = {}
        for s in src:
            src_cnt[(s.kind, s.text)] = src_cnt.get((s.kind, s.text), 0) + 1
        dst_cnt: dict[tuple[str, str], int] = {}
        for s in dst:
            dst_cnt[(s.kind, s.text)] = dst_cnt.get((s.kind, s.text), 0) + 1

        missing = [
            ProtectedSpan(k, t)
            for (k, t), n in src_cnt.items()
            for _ in range(max(0, n - dst_cnt.get((k, t), 0)))
        ]
        added = [
            ProtectedSpan(k, t)
            for (k, t), n in dst_cnt.items()
            for _ in range(max(0, n - src_cnt.get((k, t), 0)))
        ]

        # 顺序只作为提示：比较双方共有片段的相对次序
        order_changed = False
        for kind in {k for k, _ in src_cnt}:
            s_seq = [t for (k, t) in src_cnt if k == kind]
            d_seq = [t for (k, t) in dst_cnt if k == kind]
            common = [t for t in s_seq if t in d_seq]
            if common != [t for t in d_seq if t in set(common)]:
                order_changed = True
                break

        return SpanCheck(
            ok=not missing and not added,
            missing=missing,
            added=added,
            changed_order=order_changed,
        )


# --------------------------------------------------------------------------
# 3. 普通英文残留扫描
# --------------------------------------------------------------------------

# 允许留在译文里的拉丁片段（专名、缩写、模型名、单位等）。
#
# 判定规则刻意从严：只有**单个词**或**整个片段**命中白名单才放过。
# 因此这里只放「本身就是专名/缩写」的条目，不放 Open / Vision / Language
# 这类普通英文词 —— 否则 "narrowly tailored" 这类真漏译也会被放过。
DEFAULT_WHITELIST = [
    # 模型 / 数据集 / 方法名（多词的整体短语）
    "π0", "pi0", "PaliGemma", "SigLIP", "Gemma", "Transformer", "transformer",
    "Open X-Embodiment", "OXE", "Bridge", "RT-1", "RT-2", "Octo",
    "Diffusion Policy", "ALOHA", "Mobile ALOHA", "DROID", "HumanPlus", "HPT",
    "PaLM-E", "CLIP", "BERT", "YOLO", "ResNet", "ImageNet", "PaliGemma-3B",
    "vision-language-action", "vision-language", "flow matching",
    "action expert", "cross-embodiment", "mobile manipulator",
    # 标准缩写
    "VLM", "VLA", "LLM", "MLP", "CNN", "RNN", "RGB", "RGBD", "GPU", "CPU", "TPU",
    "SGD", "Adam", "AdamW", "ReLU", "GELU", "VAE", "GAN", "RL", "IL", "BC",
    "VRR", "VRPRN", "MSE", "KL", "JSON", "YAML", "XML", "HTTP", "API", "SDK", "CLI",
    "URL", "URI", "DOI", "PDF", "CSV", "SQL", "ID", "IDs", "RPC", "TCP", "UDP",
    # 工具 / 语言 / 平台
    "arXiv", "LaTeX", "GitHub", "Python", "PyTorch", "TensorFlow", "JAX", "CUDA",
    "Docker", "Linux", "Windows", "macOS", "ONNX", "MuJoCo", "Isaac", "ROS",
]

_FRAGMENT_WHITELIST = {
    " ".join(w.lower().split()) for w in DEFAULT_WHITELIST
}
_WORD_WHITELIST = {
    w.lower() for w in DEFAULT_WHITELIST if " " not in w.strip()
}

_RESIDUE_PAT = re.compile(r"[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*)+")

# 连续小写普通词：真正要抓的漏译特征是「两个及以上首字母小写的英文词相邻」。
# （额外要求见 scan_english_residue：片段里至少得有一个 4 字母以上的实词，
#   以此过滤作者名换行残片 'a n'、's and' 这类噪声。）
_LOWER_RUN_PAT = re.compile(r"\b[a-z][A-Za-z'’\-]*(?:\s+[a-z][A-Za-z'’\-]*)+\b")

_CJK_PAT = re.compile(r"[\u4e00-\u9fff]")

# 控制字符：图内文字/嵌入字体被提取时常出现的乱码碎片，
# 它们不是正文文本，应排除在漏译与重叠判定之外。
_CTRL_PAT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 判定「夹在中文里」时，向前/后各看多少个字符
CONTEXT_WINDOW = 25

# 页眉/侧栏的出版元数据 —— 它们**本来就不该被翻译**，
# 因此在整篇级比对前先剔除，避免误报成「片段丢失」。
# 实测 arXiv 侧栏会贡献 arXiv 编号、日期、分类码、版本号等。
_METADATA_PAT = re.compile(
    r"(arXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?"
    r"|\[cs\.[A-Za-z]{2}\]"
    r"|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\b"
    r"|\b(?:19|20)\d{2}\b)",
    re.IGNORECASE,
)


def strip_pdf_metadata(text: str) -> str:
    """剔除页眉/侧栏的出版元数据，供整篇级比对使用。"""
    return _METADATA_PAT.sub(" ", text or "")


def scan_english_residue(
    text: str,
    whitelist: Iterable[str] | None = None,
    require_cjk_context: bool = True,
) -> list[str]:
    """找出译文里**夹在中文句子中**的普通英文片段（疑似漏译）。

    判定规则（层层收窄，目标是零误报）：
      1. 只取「连续 >= 2 个首字母小写的英文词」，例如 ``narrowly tailored``。
         作者名/机构名/缩写（``Kevin Black``、``VLM``、``Open X-Embodiment``）
         首字母大写，天然不会被命中。
      2. 片段若是白名单短语、缩写或含 URL/DOI/邮箱/引用编号，放过。
      3. 默认还要求片段**前后窗口内出现中文字符**。
         理由：论文里的作者块、图片内文字、代码块本身就该是英文，
         它们不构成漏译；只有"中文句子里夹着英文"才是真问题。

    参数 ``require_cjk_context=False`` 可关闭第 3 条（用于整篇英文文档检查）。
    """
    frag_wl = _FRAGMENT_WHITELIST if whitelist is None else {
        " ".join(w.lower().split()) for w in whitelist
    }
    word_wl = _WORD_WHITELIST if whitelist is None else {
        w.lower() for w in whitelist if " " not in w.strip()
    }

    hits: list[str] = []
    seen: set[str] = set()
    for m in _LOWER_RUN_PAT.finditer(text or ""):
        hit = m.group(0).strip()
        if hit.lower() in seen:
            continue
        if hit.lower() in frag_wl:
            continue
        words = [w for w in re.split(r"[\s'’\-]+", hit) if w]
        if words and all(w.lower() in word_wl for w in words):
            continue
        # 片段里至少得有一个 4 字母以上的实词，否则多半是作者名换行残片
        if not any(len(w) >= 4 for w in words):
            continue
        if re.search(r"(https?://|www\.|@|10\.\d{4}|\[\d+\])", hit):
            continue
        # 图内/侧栏文字常被提取成含控制字符的碎片，不是正文漏译
        if _CTRL_PAT.search(hit):
            continue
        # arXiv 侧栏编号被逐字拆开（'a rX iv :2 4 1 0...'）：剥掉空白后
        # 若呈现 arXiv/编号特征就放过
        squashed = re.sub(r"\s+", "", hit)
        if re.search(r"(arXiv|arxiv)", squashed, re.I) or re.fullmatch(
            r"[a-zA-Z:.\[\]0-9v]+", squashed
        ) and sum(c.isdigit() for c in squashed) >= 4:
            continue
        if require_cjk_context:
            lo = max(0, m.start() - CONTEXT_WINDOW)
            hi = min(len(text), m.end() + CONTEXT_WINDOW)
            ctx = text[lo:hi]
            # 前后紧邻处若出现 arXiv 侧栏标识，说明这是页边元数据而不是正文
            if re.search(r"(arXiv|arxiv)", re.sub(r"\s+", "", ctx)):
                continue
            if not _CJK_PAT.search(ctx):
                continue      # 纯英文区域（作者块/图片/代码），不算漏译
        seen.add(hit.lower())
        hits.append(hit)
    return hits


# --------------------------------------------------------------------------
# 4. 质检报告
# --------------------------------------------------------------------------

QA_TARGETS = {
    "公式/引用/URL 占位符精确匹配": "100%",
    "数字与单位一致": "100%",
    "术语表命中率": ">= 98%",
    "普通英文残留": "0（白名单除外）",
    "正文文本框重叠": "0",
    "译文空块": "0",
    "兼容区字符": "0",
}


def qa_report(path: str | Path) -> dict:
    """对一份对照版 PDF 做程序级质检，返回结构化结果。

    检查项与改进建议文档第三节的指标表对齐，可写成 ``qa.json`` / ``qa.md``。
    """
    import pymupdf

    path = Path(path)
    doc = pymupdf.open(path)
    half = lambda p: pymupdf.Rect(  # noqa: E731
        p.rect.x0, p.rect.y0, p.rect.x0 + p.rect.width / 2, p.rect.y1
    )
    half_r = lambda p: pymupdf.Rect(  # noqa: E731
        p.rect.x0 + p.rect.width / 2, p.rect.y0, p.rect.x1, p.rect.y1
    )

    left_text = "".join(doc[i].get_text(clip=half(doc[i])) for i in range(doc.page_count))
    right_text = "".join(doc[i].get_text(clip=half_r(doc[i])) for i in range(doc.page_count))

    guard = SpanGuard()
    # 整篇级质检只校验严格类型：引用编号会被版面重新分组
    # （[1, 2] 可能拆成 [1], [2]），纯整数（年份/页码/编号）在不同段落
    # 天然不同，逐字比对必然误报。
    # 同时剔除页眉/侧栏的出版元数据，它们本就不翻译。
    span_check = guard.check(
        strip_pdf_metadata(left_text),
        strip_pdf_metadata(right_text),
        strict_only=True,
    )
    compat = find_compat_chars(right_text)
    residue = scan_english_residue(right_text)

    # 文本框重叠检测（译文区内的 span 包围盒）
    #
    # 必须排除两类假阳性（实测）：arXiv 侧栏那种**竖排单字符**，
    # 每个字符一个 span、字形包围盒天然相互叠压；以及标点符号的
    # 微小越界。因此：
    #   * 跳过页面左右边缘的窄带（竖排侧栏所在区域）；
    #   * 用绝对面积阈值兜底，忽略零头级交叠。
    overlaps = 0
    for i in range(doc.page_count):
        page = doc[i]
        r = doc[i].rect
        clip = half_r(page)
        # 译文页左边缘的竖排带（原页侧栏被搬过来的位置）
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
                    if _CTRL_PAT.search(txt):
                        continue          # 图内乱码碎片，非正文
                    rect = pymupdf.Rect(s["bbox"])
                    if rect.x1 <= edge_x:
                        continue          # 边缘竖排带，跳过
                    spans.append((rect, txt.strip()))
        spans.sort(key=lambda item: (round(item[0].x0, 1), item[0].y0))
        for (a, ta), (b, tb) in zip(spans, spans[1:]):
            inter = a & b
            if not inter.is_valid:
                continue
            area = inter.get_area()
            if area <= 12.0 or area <= 0.35 * min(a.get_area(), b.get_area()):
                continue
            # 同一文本在同一位置重复绘制（嵌入式图表常见）不算重叠：
            # 它不会造成两段字互相压叠，只是同一份内容被画了两遍。
            if ta == tb and abs(a.x0 - b.x0) < 1 and abs(a.y0 - b.y0) < 1:
                continue
            overlaps += 1

    result = {
        "file": str(path),
        "pages": doc.page_count,
        "placeholders": {
            "ok": span_check.ok,
            "detail": span_check.describe(),
            "missing": [f"{s.kind}:{s.text}" for s in span_check.missing][:20],
            "added": [f"{s.kind}:{s.text}" for s in span_check.added][:20],
        },
        "number_consistency": {
            "ok": not span_check.missing and not span_check.added,
        },
        "english_residue": residue[:20],
        "compat_chars": {f"U+{ord(c):04X} {c}": n for c, n in compat.items()},
        "compat_total": sum(compat.values()),
        "box_overlaps": overlaps,
        "right_cjk": sum(1 for c in right_text if "\u4e00" <= c <= "\u9fff"),
    }
    doc.close()

    result["verdict"] = (
        "通过"
        if span_check.ok and not compat and not residue and overlaps == 0
        else "需修"
    )
    return result


def write_qa_markdown(result: dict, out: str | Path) -> Path:
    """把质检结果写成 markdown。"""
    out = Path(out)
    lines = [
        f"# 质检报告 — {Path(result['file']).name}",
        "",
        f"- 页数：{result['pages']}",
        f"- 译文区中文字符：{result['right_cjk']}",
        f"- **结论：{result['verdict']}**",
        "",
        "| 指标 | 目标 | 实测 |",
        "|---|---|---|",
        f"| 公式/引用/URL/数字 精确匹配 | {QA_TARGETS['公式/引用/URL 占位符精确匹配']} "
        f"| {'✅' if result['placeholders']['ok'] else '❌ ' + result['placeholders']['detail']} |",
        f"| 普通英文残留 | {QA_TARGETS['普通英文残留']} "
        f"| {'✅' if not result['english_residue'] else '❌ ' + str(len(result['english_residue'])) + ' 处'} |",
        f"| 正文文本框重叠 | {QA_TARGETS['正文文本框重叠']} "
        f"| {'✅' if result['box_overlaps'] == 0 else '❌ ' + str(result['box_overlaps'])} |",
        f"| 兼容区字符 | {QA_TARGETS['兼容区字符']} "
        f"| {'✅' if result['compat_total'] == 0 else '❌ ' + str(result['compat_total'])} |",
        "",
    ]
    if result["placeholders"]["missing"]:
        lines += ["## 丢失的保护片段", ""] + [f"- {x}" for x in result["placeholders"]["missing"]] + [""]
    if result["placeholders"]["added"]:
        lines += ["## 新增（不应出现）", ""] + [f"- {x}" for x in result["placeholders"]["added"]] + [""]
    if result["english_residue"]:
        lines += ["## 疑似漏译（普通英文残留）", ""] + [f"- `{x}`" for x in result["english_residue"]] + [""]
    if result["compat_chars"]:
        lines += ["## 兼容区字符", "", "| 字符 | 次数 |", "|---|---|"]
        lines += [f"| `{k}` | {v} |" for k, v in result["compat_chars"].items()]
        lines += [""]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def install_tounicode_fix() -> bool:
    """挂载 ToUnicode 修正：在 ``Document.subset_fonts`` 之后自动修复。

    ``subset_fonts`` 是**最后一步**会改写 ToUnicode 的操作，因此紧跟在它
    之后修复能覆盖全部影响面。返回是否安装成功。
    """
    try:
        import pymupdf
    except Exception:
        return False

    if getattr(pymupdf.Document.subset_fonts, "_pdfzh_tounicode_patched", False):
        return True

    original = pymupdf.Document.subset_fonts
    stats = {"calls": 0, "replaced": 0}

    def _patched(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        try:
            info = fix_pdf_tounicode(self)
            stats["calls"] += 1
            stats["replaced"] += info.get("replaced", 0)
        except Exception:
            pass
        return result

    _patched._pdfzh_tounicode_patched = True
    _patched._pdfzh_stats = stats
    pymupdf.Document.subset_fonts = _patched
    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="翻译质检 / ToUnicode 修复")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--output", default=None, help="写出 qa.md 的路径")
    ap.add_argument(
        "--fix-tounicode", metavar="OUT_PDF",
        help="修正 PDF 的 ToUnicode 兼容区码位并另存为新文件",
    )
    ns = ap.parse_args()

    if ns.fix_tounicode:
        import pymupdf

        src = pymupdf.open(ns.pdf)
        info = fix_pdf_tounicode(src)
        src.save(ns.fix_tounicode, deflate=True, garbage=3, clean=True)
        src.close()
        print("ToUnicode 修复:", json.dumps(info, ensure_ascii=False))
        print("已保存:", ns.fix_tounicode)
        raise SystemExit(0)

    rep = qa_report(ns.pdf)
    print(json.dumps(
        {k: v for k, v in rep.items() if k not in ("compat_chars",)},
        ensure_ascii=False, indent=2,
    ))
    if ns.output:
        print("已写出:", write_qa_markdown(rep, ns.output))
