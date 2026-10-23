#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdfzh —— PDF 中英对照翻译工具（左英文原版 / 右中文译文）

用法示例
--------
    # 左右并排（默认，单栏文档）
    python pdfzh.py 论文.pdf --key sk-xxxx

    # 双栏论文自动改用上下对照
    python pdfzh.py 论文.pdf --key sk-xxxx --layout auto

    # 只翻前 5 页快速试跑
    python pdfzh.py 论文.pdf --key sk-xxxx --pages 1-5

    # 查看帮助
    python pdfzh.py --help

设计说明
--------
* 翻译环节复用开源项目 PDFMathTranslate(pdf2zh)：它用版面检测模型区分正文/公式/图表，
  公式与图表保持原样，只替换正文文字，因此译文版式与原版基本一致。
* 对照拼版由本仓库的 pdf_side_by_side 模块完成：pdf2zh 自带的 dual 模式是
  「一页英文、一页中文」交替排布，而本工具把两页压到同一页的左右两栏（或上下两栏），
  采用矢量嵌入，放大不糊、文字可选中。
* 缓存与配置目录重定向到工作区内（见 .home/），避免沙箱/权限问题，也方便清理。

作者: 为「具身智能」工作区定制
依赖: pdf2zh (AGPL-3.0) + PyMuPDF
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# 路径常量
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
DEFAULT_HOME = HERE / ".home"          # 缓存/配置重定向目录
PROMPT_DIR = HERE / "prompts"


# --------------------------------------------------------------------------
# 环境准备
# --------------------------------------------------------------------------


def setup_workspace_env(quiet: bool = False) -> Path:
    """把 HOME/USERPROFILE、临时目录与各类缓存目录指到工作区内。

    必须在 import pdf2zh / babeldoc / pymupdf **之前** 调用：
    * pdf2zh 导入时会创建 ~/.cache/pdf2zh 翻译缓存；
    * babeldoc 导入时会创建 ~/.cache/babeldoc 模型/字体缓存与 tiktoken 缓存；
    * PyMuPDF 的字体子集化（subset_fonts）会往系统临时目录写文件。

    在受限环境（沙箱、无家目录写权限）下，以上任一失败都会让流程中断，
    因此这里统一重定向到工作区内，既能跑通也方便清理。
    """
    home = Path(os.environ.get("PDFZH_HOME") or DEFAULT_HOME)
    tmp = home / "tmp"
    for d in (home / ".cache", tmp):
        d.mkdir(parents=True, exist_ok=True)

    os.environ["USERPROFILE"] = str(home)
    os.environ["HOME"] = str(home)
    os.environ["TEMP"] = str(tmp)
    os.environ["TMP"] = str(tmp)
    os.environ["TMPDIR"] = str(tmp)
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    # 让 tempfile 立刻采用新的临时目录（它是进程内缓存过一次的）
    import tempfile

    tempfile.tempdir = str(tmp)

    if not quiet:
        print(f"[环境] 缓存 -> {home / '.cache'}")
        print(f"[环境] 临时 -> {tmp}")
    return home


def patch_tempdir_cleanup() -> None:
    """让临时目录在受限环境下也能正常工作。

    这里解决两个实测到的坑（容器 / 沙箱 / 严格 ACL 环境常见）：

    1. **无法写入 ``mkdtemp`` 新建的子目录**
       某些沙箱只允许写入工作区内「已存在」的目录，新建的子目录会被拒绝。
       于是把 ``mkdtemp`` 改成在 temp 根目录下创建**同级**唯一目录，
       再让 ``TemporaryDirectory`` 指向它。

    2. **清理时因 ``chmod`` 被禁而崩溃**
       ``shutil.rmtree`` 删除失败时会调用 ``os.chmod(path, 0o700)`` 修权限，
       在禁 chmod 的环境下会抛 PermissionError 把整个流程带崩 ——
       而这一步只是删临时文件，不该致命。改成「尽力而为」的清理。

    以上都只影响临时文件的落点与清理，不改变翻译/排版结果。
    """
    import shutil
    import tempfile

    _orig_mkdtemp = tempfile.mkdtemp
    _root = tempfile.tempdir

    def _flat_mkdtemp(suffix=None, prefix=None, dir=None, **kw):
        suffix = suffix or ""
        prefix = prefix or "tmp"
        # 始终落在 temp 根目录下，做成同级目录而不是新建子目录
        base = Path(dir or _root or tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        for _ in range(100):
            candidate = base / f"{prefix}{next(tempfile._get_candidate_names())}{suffix}"
            try:
                candidate.mkdir()
                return str(candidate)
            except FileExistsError:
                continue
        return _orig_mkdtemp(suffix, prefix, dir, **kw)

    def _cleanup_tolerant(self):
        path = getattr(self, "name", None)
        if not path:
            return
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass

    def _exit(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False

    tempfile.mkdtemp = _flat_mkdtemp
    tempfile.TemporaryDirectory.cleanup = _cleanup_tolerant
    tempfile.TemporaryDirectory.__exit__ = _exit


# --------------------------------------------------------------------------
# 提示词
# --------------------------------------------------------------------------
#
# 设计要点（都是踩过坑总结的）：
#   1. 占位符规则必须放最前面并用最强烈的措辞 —— 模型一旦改动 {v12}，
#      对应位置的公式就会错位或消失，这是不可接受的硬性失败。
#   2. 「不翻译清单」单独成节，避免与「要翻译」的规则互相干扰。
#   3. 文档类型自适应写成「先判定、再按类型选策略」的显式分支，
#      而不是笼统地说「灵活处理」—— 后者模型会一律按论文腔翻译。
#   4. 术语一致性靠可选词表（glossary.json）注入，长文档尤其需要。
#   5. 结尾重复一次「只输出译文」，因为这是最常见的失败模式（模型爱加解释）。

_PROMPT_HEAD = """You are a professional translator. Translate the text below from ${lang_in} into ${lang_out}.

=== RULE 0 — ABSOLUTE, NEVER VIOLATE ===
The source text contains placeholders of the form {v0}, {v1}, {v12} ...
They stand for formulas, math and inline symbols that are rendered separately.
* Copy every placeholder out EXACTLY as written: same digits, same braces, same order.
* NEVER translate, rename, renumber, merge, split, delete or invent a placeholder.
* If a placeholder is split across lines, keep it intact on one line.
Breaking this rule corrupts the formulas in the output document.

=== RULE 1 — KEEP VERBATIM (do not translate) ===
* LaTeX / HTML markup: \\cite{...}, \\ref{...}, \\label{...}, \\textbf{...}, $...$ and similar commands
* URLs, DOIs, email addresses, file paths, arXiv IDs
* Numeric values, units, and measurement symbols
* Code: identifiers, function/variable/class names, CLI flags, file names, API names
* Standard abbreviations already in Latin script: GPT, CNN, RGB, VLA, MLP, SGD, LLM ...
  (explain them in ${lang_out} at first occurrence if a natural translation exists,
   otherwise keep the abbreviation as-is)
* IMPORTANT: do NOT leave ordinary English words or phrases untranslated.
  "Leaving something in English" is ONLY for the fixed-format items listed above.
  If a phrase happens to be a term of art, translate it into the established ${lang_out} term, or translate it descriptively and append the English in
  parentheses on first occurrence — but never leave a plain English phrase
  sitting inside an otherwise translated sentence.

=== RULE 2 — DETECT THE DOCUMENT TYPE, THEN TRANSLATE ACCORDINGLY ===
First infer what kind of document this is from the passage itself, then apply the
matching style. Different types need genuinely different voices — do not translate
everything in one stiff academic tone.

* Academic paper / thesis (abstract, related work, method, experiments)
  -> Formal academic register. Use the established terminology of the field.
     Prefer accepted standard Chinese terms over literal word-for-word renderings.
* Technical documentation / API reference / README
  -> Clear and instructional. Keep parameter names, commands and code intact.
     Translate descriptions, not symbols.
* News / blog / essay
  -> Natural journalistic prose. Prioritise readability and idiom over literal fidelity;
     restructure sentences where Chinese word order demands it.
* Report / white paper / business document
  -> Professional and neutral. Consistent terminology, no colloquialisms.
* Legal / policy / contract
  -> Conservative and precise. Never paraphrase; keep modality
     (shall / may / must) exactly as strong as the source.
* Manual / guide / tutorial
  -> Second person, imperative, step-by-step clarity.
* Slide deck / poster / figure labels
  -> Terse. Prefer short noun phrases; do not pad them into full sentences.
* Mixed content (e.g. paper with code, report with tables)
  -> Apply the above per passage. Captions, table headers and footnotes follow
     their own function, not the surrounding body text.

Quality bar for every type:
* Write idiomatic ${lang_out} that reads as if originally written in it.
  Never produce translationese or word-for-word mapping.
* Preserve the source's meaning, tone, emphasis and hedging strength.
  Do not add information, do not drop information, do not soften or strengthen claims.
* Keep discourse markers that carry meaning (however / therefore / surprisingly /
  note that / in contrast) — they are part of the argument, not filler.
* Match the source's level of formality.
* Render proper nouns the way the target-language community normally does;
  keep personal names in their original spelling.
* Do not translate a passage into a longer, vaguer paraphrase. Be as compact as the source.

=== RULE 3 — STRUCTURE & TYPOGRAPHY ===
* Preserve the original structure and order exactly: headings, lists, numbering,
  bullets, quotation marks, line-oriented layout.
* Keep heading hierarchy (do not promote or demote headings, do not add markers).
* Use full-width Chinese punctuation in ${lang_out} (，。；：？！、""''（）),
  and keep half-width punctuation inside Latin text, formulas and identifiers.
* Never insert extra line breaks, markdown fences, or bullet characters that were
  not in the source.
"""

_PROMPT_TAIL = """
=== RULE 4 — OUTPUT FORMAT ===
Output ONLY the translation. No preamble, no explanation, no notes, no alternatives,
no quotes around the whole result, no markdown code fences, no "Translation:" prefix.

Source Text:
${text}

Translated Text:"""

_PROMPT_KEEP_REFS = """
=== RULE 5 — REFERENCES SECTION (this document keeps them in the source language) ===
If the passage is an entry in a References / Bibliography list:
* Keep the reference entry itself in its original language (author names, paper title,
  venue, year, DOI/URL all unchanged).
* Translate only a leading label if one exists (e.g. "参考文献" style labels).
This rule overrides Rule 2 for reference entries only — it does not apply to
in-text citations such as [12] or (Smith et al., 2023), which stay as they are too.
"""

_PROMPT_TRANSLATE_ALL = """
=== RULE 5 — REFERENCES SECTION (this document translates them too) ===
References / bibliography entries ARE translated like the rest of the text,
except for author names, DOIs and URLs which stay unchanged.
"""


def load_glossary() -> str:
    """读取可选的术语表，拼成提示词片段。

    ``glossary.json`` 支持两种写法::

        {"flow matching": "流匹配"}                       # 简单版
        {"inference": {"preferred": "推理",               # 进阶版
                       "forbidden": ["推断"],
                       "context": "模型执行阶段"}}

    以 ``_`` 开头的键视为注释，会被跳过。文件不存在时返回空串。
    """
    path = HERE / "glossary.json"
    if not path.exists():
        return ""
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            return ""

        lines: list[str] = []
        for key, val in data.items():
            if str(key).startswith("_") or not str(key).strip():
                continue
            if isinstance(val, dict):
                pref = str(val.get("preferred", "")).strip()
                if not pref:
                    continue
                entry = f"  - {key}  ->  {pref}"
                forbidden = val.get("forbidden") or []
                if forbidden:
                    entry += f"   [禁止: {', '.join(str(x) for x in forbidden)}]"
                ctx = str(val.get("context", "")).strip()
                if ctx:
                    entry += f"   ({ctx})"
                lines.append(entry)
            elif str(val).strip():
                lines.append(f"  - {key}  ->  {val}")

        if not lines:
            return ""
        return (
            "\n=== RULE 6 — GLOSSARY (MANDATORY, overrides your own wording) ===\n"
            "Always use exactly these renderings; never substitute synonyms.\n"
            "Entries marked [禁止] are wrong translations you must never produce.\n"
            + "\n".join(lines)
            + "\n"
        )
    except Exception:
        return ""


def build_prompt(keep_refs: bool) -> str:
    """组装完整提示词：公共头 + 参考文献策略 + 术语表 + 输出格式。"""
    body = _PROMPT_KEEP_REFS if keep_refs else _PROMPT_TRANSLATE_ALL
    return _PROMPT_HEAD + body + load_glossary() + _PROMPT_TAIL


def write_prompt(keep_refs: bool) -> Path:
    """把提示词写到文件（pdf2zh 的 --prompt 接受文件路径）。"""
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    name = "keep_refs.txt" if keep_refs else "translate_all.txt"
    path = PROMPT_DIR / name
    path.write_text(build_prompt(keep_refs), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# API Key
# --------------------------------------------------------------------------


def resolve_api_key(explicit: str | None, service: str) -> str | None:
    """按优先级取 API Key：命令行 > 环境变量 > 本地 config.json。"""
    if explicit:
        return explicit.strip()

    env_names = {
        "deepseek": "DEEPSEEK_API_KEY",
        "silicon": "SILICON_API_KEY",
        "zhipu": "ZHIPU_API_KEY",
        "modelscope": "MODELSCOPE_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    env_name = env_names.get(service)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].strip()

    cfg = HERE / "config.json"
    if cfg.exists():
        try:
            import json

            data = json.loads(cfg.read_text(encoding="utf-8"))
            key = (data.get("api_keys") or {}).get(service)
            if key:
                return str(key).strip()
        except Exception as exc:
            print(f"[警告] 读取 config.json 失败：{exc}", file=sys.stderr)
    return None


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def patch_babeldoc_prompt() -> None:
    """绕开 pdf2zh 使用 BabelDOC 后端时的 prompt 类型 bug。

    pdf2zh.pdf2zh.main() 会先把 ``args.prompt``（文件路径字符串）读成
    ``string.Template`` 对象；但同一个 ``args`` 接着被转交给 BabelDOC 路径的
    ``yadt_main()``，那里又把 ``args.prompt`` 当成文件路径去 ``open()``，
    于是抛 ``TypeError: expected str, bytes or os.PathLike object, not Template``。

    这里在调用前把 ``args.prompt`` 还原成文件路径字符串即可：
    BabelDOC 路径自己会再读一次文件并构造 Template。
    文件路径通过环境变量 ``PDFZH_PROMPT_PATH`` 传递（pdf2zh 是命令行式调用，
    参数对象在内部构造，外部拿不到引用）。
    """
    import inspect

    from pdf2zh import pdf2zh as _p2z

    original = _p2z.yadt_main
    if getattr(original, "_pdfzh_patched", False):
        return
    try:
        param_name = list(inspect.signature(original).parameters)[0]
    except Exception:
        param_name = "parsed_args"

    def _wrapped(*args, **kwargs):
        parsed = kwargs.get(param_name)
        if parsed is None and args:
            parsed = args[0]
        prompt_path = os.environ.get("PDFZH_PROMPT_PATH")
        if parsed is not None and prompt_path and os.path.exists(prompt_path):
            parsed.prompt = prompt_path
        return original(*args, **kwargs)

    _wrapped._pdfzh_patched = True
    _p2z.yadt_main = _wrapped


def _find_babeldoc_outputs(outdir: Path, before: set[Path], stem: str) -> tuple[Path | None, Path | None]:
    """在目录里找出 BabelDOC 新产出的 (纯中文页, 双语页)。

    BabelDOC 的输出命名与旧引擎不同（且可能带/不带 watermark 后缀），
    因此不写死文件名，而是按「新增文件 + 是否含中文」来判定。
    """
    after = {p for p in outdir.rglob("*.pdf")}
    new_pdfs = sorted(after - before)
    if not new_pdfs:
        return None, None

    def cjk_count(p: Path) -> int:
        try:
            import pymupdf

            with pymupdf.open(p) as d:
                text = "".join(d[i].get_text() for i in range(min(4, d.page_count)))
            return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        except Exception:
            return 0

    scored = sorted(((cjk_count(p), p) for p in new_pdfs), key=lambda t: -t[0])
    # 候选：既可能是 mono（全中文），也可能是 dual（中英交替）
    mono = next((p for c, p in scored if c > 200 and "dual" not in p.name.lower()), None)
    if mono is None:
        mono = next((p for c, p in scored if c > 200), None)
    dual = next((p for c, p in scored if "dual" in p.name.lower()), None)
    return mono, dual


def patch_translator_guard(max_retry: int = 2) -> dict:
    """给翻译出口加程序级保护：NFKC 规范化 + 受保护片段校验重试。

    挂在 ``pdf2zh.translator.BaseTranslator.translate`` 上，因为**所有**引擎
    （deepseek / openai / google / ...）都经过这个方法，是唯一的必经出口。

    做两件事：

    1. **NFKC 规范化**（关键修复）
       pdf2zh 写 PDF 时用字形编号反查编码，译文里会出现 CJK 兼容区字形
       （实测 5 页 300 个：``不`` 被写成 ``不`` U+F967），导致搜索/复制失效。
       在这里统一规范化，从源头根治。

    2. **受保护片段校验 + 定点重试**
       公式占位符 ``{v0}``、引用 ``[12]``、URL、DOI、数字与单位在翻译前后
       必须完全一致。不一致时只重译该段（最多 ``max_retry`` 次），
       而不是整篇重来。

    返回统计字典，便于调用方汇报。
    """
    from pdf2zh.translator import BaseTranslator

    stats: dict = {
        "calls": 0, "normalized": 0, "retried": 0,
        "failed": 0, "failed_samples": [],
    }

    if getattr(BaseTranslator.translate, "_pdfzh_guarded", False):
        return stats

    sys.path.insert(0, str(HERE))
    from translation_guard import SpanGuard, normalize_text

    guard = SpanGuard()
    original = BaseTranslator.translate

    def _guarded(self, text: str, ignore_cache: bool = False) -> str:
        stats["calls"] += 1

        # 缓存命中也要规范化：早期版本可能已把兼容区字形写进缓存
        if not (self.ignore_cache or ignore_cache):
            cached = self.cache.get(text)
            if cached is not None:
                fixed = normalize_text(cached)
                if fixed != cached:
                    stats["normalized"] += 1
                    self.cache.set(text, fixed)
                return fixed

        best: str | None = None
        for attempt in range(max_retry + 1):
            try:
                raw = self.do_translate(text)
            except Exception:
                if attempt >= max_retry:
                    raise
                stats["retried"] += 1
                continue

            candidate = normalize_text(raw or "")
            check = guard.check(text, candidate)
            if check.ok:
                best = candidate
                break

            best = candidate
            if attempt >= max_retry:
                stats["failed"] += 1
                if len(stats["failed_samples"]) < 5:
                    stats["failed_samples"].append(
                        f"{check.describe()} | 原文: {text[:60]!r}"
                    )
            else:
                stats["retried"] += 1

        if best is None:
            best = normalize_text(self.do_translate(text) or "")

        stats["normalized"] += 1
        self.cache.set(text, best)
        return best

    _guarded._pdfzh_guarded = True
    BaseTranslator.translate = _guarded
    return stats


def patch_heading_rescue() -> dict:
    """修掉「小节标题被误判成 figure 而不翻译」的问题。

    实测根因（用版面模型逐框核对过）：
        ``A. Pre-training and post-training`` 这类**子小节标题**被
        doclayout 模型判成 ``figure``（置信度 0.96），而 ``figure`` 属于
        converter 里「保护区域」的类别，不送翻译，于是标题原样保留英文。
        实测整篇有 13 个小节标题因此未翻译；``IV.``/``V.`` 这类罗马数字
        主标题不受影响。

    修法分两步：

    1. 在 ``pdf2zh.high_level.translate_patch`` 里，版面预测之后**记下
       每一页的 (预测结果, 页面对象)**，供后续按页取用（用正则改写源码注入
       一行，比整函数重写稳）。
    2. 包住 ``TranslateConverter.receive_layout``：它在入口处能拿到当前页号
       （``ltpage.pageid``），此时遮罩刚建好，正是改判时机。对每个被判为
       figure/figure_caption/abandon 的框，若：
         * 框内文字匹配 ``A. Xxxx`` / ``1.1 Xxxx`` 这类标题形态；
         * 框不太大（真图片框通常很大）；
         * 框内图片面积占比低（确实是文字而不是图）；
       就把该框覆盖的 layout 网格值由「保护(0)」改为「正文」，
       让标题进入正常翻译流程。

    返回统计字典。
    """
    stats: dict = {"pages": 0, "rescued": 0, "samples": []}

    # ---- 第 1 步：记录每页版面预测 ----
    try:
        import inspect
        import re as _re
        import textwrap

        import pdf2zh.high_level as _hl

        if not getattr(_hl, "_pdfzh_layout_recorded", False):
            src = inspect.getsource(_hl.translate_patch)
            anchor = "page_layout = model.predict(image, imgsz=int(pix.height / 32) * 32)[0]"
            if anchor in src:
                patched_src = src.replace(
                    anchor,
                    anchor
                    + "\n            _pdfzh_record_layout(page.pageno, page_layout)",
                    1,
                )
                patched_src = textwrap.dedent(patched_src)
                # 注意：
                #  * exec 出来的函数以 _hl.__dict__ 作为 globals，
                #    因此注入的辅助函数必须同时写进模块命名空间。
                #  * 记录时必须**用原始页面 doc_en**，不能用 doc_zh ——
                #    doc_zh 是翻译后的页面，文字已是中文，标题正则匹配不上。
                _hl._pdfzh_record_layout = _record_layout
                ns: dict = {"_pdfzh_record_layout": _record_layout}
                exec(compile(patched_src, "<pdfzh_translate_patch>", "exec"), _hl.__dict__, ns)
                _hl.translate_patch = ns["translate_patch"]
            _hl._pdfzh_layout_recorded = True
    except Exception as exc:
        stats["error_record"] = str(exc)

    # ---- 第 2 步：在 receive_layout 里改判 ----
    try:
        from pdf2zh.converter import TranslateConverter
    except Exception:
        return stats

    if getattr(TranslateConverter.receive_layout, "_pdfzh_heading_rescued", False):
        return stats

    import re as _re

    HEADING = _re.compile(
        r"^\s*(?:[A-H]|[IVX]{1,4}|\d+(?:\.\d+)*)[.)]\s+[A-Z][A-Za-z]"
    )
    PROTECTED = {"figure", "figure_caption", "abandon", "table", "table_caption"}

    original = TranslateConverter.receive_layout

    def _patched(self, ltpage):
        try:
            _rescue_headings(self, ltpage, HEADING, PROTECTED, stats)
        except Exception:
            pass
        return original(self, ltpage)

    _patched._pdfzh_heading_rescued = True
    TranslateConverter.receive_layout = _patched
    return stats


# 每页版面预测结果的暂存：(pageid) -> (page_layout, original_page)
_PAGE_LAYOUT: dict = {}
# 原始 PDF 的句柄（用于取「原文」文字判断标题形态）
_ORIG_DOC = {"doc": None}


def _orig_page(pageno: int):
    """取原始 PDF 的指定页（惰性打开并缓存）。

    必须用**原始页面**判断标题形态：翻译后的页面文字已经是中文，
    ``A. Xxxx`` 这类英文标题正则匹配不上（这是踩过的坑）。
    """
    import os as _os

    # translate_patch 拿到的是输入流而不是路径，这里从命令行参数取原文件
    path = _os.environ.get("PDFZH_SOURCE_PDF")
    if not path:
        return None
    if _ORIG_DOC["doc"] is None:
        try:
            import pymupdf as _fitz

            _ORIG_DOC["doc"] = _fitz.open(path)
        except Exception:
            return None
    doc = _ORIG_DOC["doc"]
    try:
        return doc[pageno]
    except Exception:
        return None


def _record_layout(pageno, page_layout, _page=None) -> None:
    _PAGE_LAYOUT[pageno] = (page_layout, _orig_page(pageno))


def _rescue_headings(self, ltpage, heading_pat, protected_classes, stats) -> None:
    """实际执行改判：见 :func:`patch_heading_rescue` 的说明。"""
    import numpy as np

    pageid = getattr(ltpage, "pageid", None)
    layout = getattr(self, "layout", None)
    if pageid is None or layout is None:
        return
    entry = _PAGE_LAYOUT.get(pageid)
    if not entry:
        if os.environ.get("PDFZH_DEBUG_RESCUE"):
            print(f"[救回诊断] pageid={pageid} 无版面记录（已有 {list(_PAGE_LAYOUT)}）")
        return
    page_layout, page = entry
    if page is None:
        if os.environ.get("PDFZH_DEBUG_RESCUE"):
            print(f"[救回诊断] pageid={pageid} 原始页面取不到"
                  f"（PDFZH_SOURCE_PDF={os.environ.get('PDFZH_SOURCE_PDF')!r}）")
        return
    grid = layout.get(pageid)
    if grid is None:
        if os.environ.get("PDFZH_DEBUG_RESCUE"):
            print(f"[救回诊断] pageid={pageid} 网格缺失（layout 键={list(layout)}）")
        return

    names = page_layout.names
    h, w = grid.shape
    pw, ph = page.rect.width, page.rect.height
    _dbg = os.environ.get("PDFZH_DEBUG_RESCUE")
    if _dbg:
        print(f"[救回诊断] pageid={pageid} grid={w}x{h} 检测框={len(page_layout.boxes)}")

    for box in page_layout.boxes:
        cls_name = names[int(box.cls)]
        if _dbg:
            print(f"  [框] class={cls_name!r} 受保护={cls_name in protected_classes}")
        if cls_name not in protected_classes:
            continue
        x0, y0, x1, y1 = (float(v) for v in box.xyxy.squeeze())
        # 注意：page_layout 的坐标来自「翻译后页面」渲染的位图，
        # 而这里要把框映射到「原始页面」的坐标系才能取到英文原文。
        pix = page.get_pixmap()
        rect = pymupdf.Rect(
            x0 * pw / pix.width, (1 - y1 / pix.height) * ph,
            x1 * pw / pix.width, (1 - y0 / pix.height) * ph,
        )
        if rect.get_area() > 0.25 * pw * ph:
            if _dbg:
                print(f"  跳过 {cls_name}: 框过大 {rect.get_area() / (pw * ph):.2f}")
            continue                      # 太大的框多半是真的整幅图
        try:
            text = page.get_text("text", clip=rect).strip()
        except Exception:
            continue
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if _dbg:
            print(f"  候选 {cls_name}: 首行={lines[0][:44]!r}" if lines else f"  候选 {cls_name}: 无文字")
        if not lines or not heading_pat.match(lines[0][:80]):
            continue
        # 框内图片面积占比过高 -> 是图不是标题，跳过
        try:
            img_area = 0.0
            for info in page.get_image_info():
                img_area += (pymupdf.Rect(info["bbox"]) & rect).get_area()
            ratio = img_area / max(rect.get_area(), 1.0)
            if ratio > 0.35:
                if _dbg:
                    print(f"    跳过: 图占比 {ratio:.2f}")
                continue
        except Exception:
            pass

        # 把该框覆盖的网格区域解保护（0 -> 非 0 即视为正文）
        gx0 = int(np.clip(x0 - 1, 0, w - 1))
        gx1 = int(np.clip(x1 + 1, 0, w - 1))
        gy0 = int(np.clip(h - y1 - 1, 0, h - 1))
        gy1 = int(np.clip(h - y0 + 1, 0, h - 1))
        before = grid[gy0:gy1, gx0:gx1]
        if before.size and (before == 0).all():
            grid[gy0:gy1, gx0:gx1] = 1
            stats["rescued"] += 1
            if len(stats["samples"]) < 8:
                stats["samples"].append(f"p{pageid + 1}: {lines[0][:46]}")
    stats["pages"] += 1


def patch_numpy_fromstring() -> None:
    """兼容 numpy 2.x：补回被移除的二进制模式 ``np.fromstring``。

    背景：BabelDOC 0.2.33 在多处（``docvision/doclayout.py``、
    ``rpc_doclayout.py``、``table_detection/rapidocr.py``）使用了
    ``np.fromstring(pix.samples, np.uint8)`` 来把页面位图转成数组。
    numpy 2.x 移除了 ``fromstring`` 的二进制模式，直接抛
    ``ValueError: The binary mode of fromstring is removed``，
    导致 BabelDOC 在「解析版面」之后、正式翻译之前就失败。

    官方替代是 ``np.frombuffer``（零拷贝）或 ``np.frombuffer(...).copy()``。
    这里只接管「非 unicode dtype」的调用，文本模式仍走原实现，
    因此不会影响其它依赖 numpy 的库。
    """
    import numpy as np

    if getattr(np.fromstring, "_pdfzh_patched", False):
        return

    _orig = np.fromstring

    def _fromstring_compat(string, dtype=float, count=-1, *, sep=""):
        # 文本模式（dtype 是字符串/unicode）保持原行为
        if isinstance(dtype, str) and dtype.lower().startswith("u"):
            return _orig(string, dtype=dtype, count=count, sep=sep)
        if isinstance(dtype, type) and issubclass(dtype, np.str_):
            return _orig(string, dtype=dtype, count=count, sep=sep)
        # 二进制模式：等价于 frombuffer，拷贝一份保证可写
        data = np.frombuffer(string, dtype=dtype, count=count)
        return data.copy()

    _fromstring_compat._pdfzh_patched = True
    np.fromstring = _fromstring_compat


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pdfzh",
        description="把英文 PDF 翻译成中文，并生成「左英文原版 / 右中文译文」的对照 PDF。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python pdfzh.py paper.pdf --key sk-xxxx\n"
            "  python pdfzh.py paper.pdf --key sk-xxxx --pages 1-5\n"
            "  python pdfzh.py paper.pdf --key sk-xxxx --layout tb --gutter 24\n"
        ),
    )
    ap.add_argument("pdf", help="要翻译的英文 PDF 路径")
    ap.add_argument("-o", "--outdir", default="output", help="输出目录（默认 output/）")
    ap.add_argument(
        "-s", "--service", default="deepseek",
        help="翻译引擎，默认 deepseek（亦支持 silicon/zhipu/modelscope/openai 等）",
    )
    ap.add_argument("--key", default=None, help="API Key（也可以用环境变量或 config.json）")
    ap.add_argument("--model", default=None, help="指定模型名，如 deepseek-chat / deepseek-reasoner")
    ap.add_argument("--base-url", default=None, help="自定义 OpenAI 兼容接口地址")
    ap.add_argument("--lang-in", default="en", help="源语言，默认 en")
    ap.add_argument("--lang-out", default="zh", help="目标语言，默认 zh")
    ap.add_argument("-t", "--thread", type=int, default=8, help="并发翻译线程数，默认 8")
    ap.add_argument(
        "--pages", default=None,
        help="只翻译指定页，如 1-5 或 2,4,7（页码从 1 开始；不填=全部）",
    )
    ap.add_argument(
        "--layout", choices=["lr", "tb"], default="lr",
        help="对照版式：lr 左英右中（默认）/ tb 上下对照",
    )
    ap.add_argument(
        "--framed", action="store_true",
        help="加上页边留白、栏间分隔线与「原文/译文」标签"
             "（默认不加：两页 100% 原尺寸贴边并排，与 BabelDOC 风格一致）",
    )
    ap.add_argument("--gutter", type=float, default=None, help="两栏间距 pt（默认 0）")
    ap.add_argument("--padding", type=float, default=None, help="页边留白 pt（默认 0）")
    ap.add_argument(
        "--translate-references", action="store_true",
        help="连参考文献一起翻译（默认参考文献保留英文）",
    )
    ap.add_argument(
        "--no-guard", action="store_true",
        help="关闭翻译保护层（默认开启：NFKC 规范化 + 受保护片段校验重试）",
    )
    ap.add_argument(
        "--no-preserve-style", action="store_true",
        help="关闭「沿用原文字体」修复（默认开启：让译文的拉丁字符沿用原文"
             "字体家族，避免退化成通用 Times-Roman）",
    )
    ap.add_argument("--babeldoc", action="store_true", help="改用实验性 babeldoc 后端")
    ap.add_argument("--ignore-cache", action="store_true", help="忽略翻译缓存，强制重译")
    ap.add_argument(
        "--skip-subset-fonts", action="store_true",
        help="跳过字体子集化（体积更大，但可绕开个别环境的临时目录权限问题）",
    )
    ap.add_argument(
        "--keep-mono", action="store_true",
        help="同时保留纯中文译文单页版（xxx-mono.pdf）",
    )
    return ap


def parse_pages(spec: str | None) -> list[int] | None:
    """把 '1-5,8' 解析成 0 基页码列表。"""
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    setup_workspace_env()
    patch_tempdir_cleanup()

    # ---- 输入校验 ----
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"[错误] 找不到文件：{pdf_path}", file=sys.stderr)
        return 2
    if pdf_path.suffix.lower() != ".pdf":
        print(f"[错误] 目前只支持 PDF，收到：{pdf_path.suffix}", file=sys.stderr)
        return 2

    outdir = Path(args.outdir)
    if not outdir.is_absolute():
        outdir = (Path.cwd() / outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    # 清空上一轮遗留的版面预测缓存（同一进程内多次调用时避免串页）
    _PAGE_LAYOUT.clear()
    # 供标题救回补丁定位「原文」页面
    os.environ["PDFZH_SOURCE_PDF"] = str(pdf_path)

    # ---- API Key ----
    api_key = resolve_api_key(args.key, args.service)
    if args.service in {"deepseek", "silicon", "zhipu", "modelscope", "openai"} and not api_key:
        print(
            f"[错误] 未提供 {args.service} 的 API Key。\n"
            f"       三种方式任选其一：\n"
            f"       1) 命令行： --key sk-xxxxxxxx\n"
            f"       2) 环境变量：set {args.service.upper()}_API_KEY=sk-xxxxxxxx\n"
            f"       3) 配置文件：{HERE / 'config.json'} 里写入 "
            f'{{"api_keys": {{"{args.service}": "sk-xxxxxxxx"}}}}\n'
            f"       （也可以改用 --service 指定其它已配置的引擎）",
            file=sys.stderr,
        )
        return 3

    # ---- 组装 pdf2zh 参数 ----
    prompt_file = write_prompt(keep_refs=not args.translate_references)
    pages = parse_pages(args.pages)

    # 默认启用「字体沿用」修复：实测可把译文里退化的 Times-Roman
    # 替换回原文的拉丁字体家族（NimbusRomNo9L 等）。
    if not args.no_preserve_style:
        try:
            sys.path.insert(0, str(HERE))
            from patch_style import install as _install_style

            if _install_style():
                print("[样式] 已启用「沿用原文字体」修复")
            else:
                print("[样式] 字体修复未生效（不影响翻译）", file=sys.stderr)
        except Exception as exc:
            print(f"[样式] 字体修复跳过：{exc}", file=sys.stderr)

    # 翻译保护层：NFKC 规范化 + 受保护片段校验重试（必须在最外层，
    # 因为它是唯一经过所有翻译引擎的出口）
    guard_stats: dict = {}
    if not args.no_guard:
        try:
            guard_stats = patch_translator_guard()
            print("[保护] 已启用 NFKC 规范化 + 公式/引用/数字一致性校验")
            # 仅靠文本层规范化还不够：PyMuPDF 的 subset_fonts 重建 ToUnicode
            # 时会给同一字形挑中 CJK 兼容区码位（实测 5 页 300 个），
            # 导致搜索/复制失效。这一层在 subset_fonts 之后修正映射。
            from translation_guard import install_tounicode_fix

            if install_tounicode_fix():
                print("[保护] 已启用 PDF 文本层 ToUnicode 修正")

            # 小节标题救回：实验性，默认关闭。
            # 已知问题：版面模型会把 `A. Xxxx` 形式的**子小节标题**误判成
            # figure（实测置信度 0.96），而 figure 属「保护区域」不送翻译，
            # 导致整篇 13 个小节标题保留英文（详见 README「已知限制」）。
            # 下面的补丁尝试在 receive_layout 里改判，但实测未生效，
            # 因此默认不启用；需要复现时设 PDFZH_RESCUE_HEADINGS=1。
            if os.environ.get("PDFZH_RESCUE_HEADINGS"):
                rescue_stats = patch_heading_rescue()
                print(f"[保护] 标题救回（实验）：{rescue_stats}")
        except Exception as exc:
            print(f"[保护] 未启用：{exc}", file=sys.stderr)

    if args.babeldoc:
        patch_numpy_fromstring()
        patch_babeldoc_prompt()

    from pdf2zh.pdf2zh import main as pdf2zh_main

    # pdf2zh 用 "service:model" 的形式指定模型，例如 deepseek:deepseek-chat
    service_spec = f"{args.service}:{args.model}" if args.model else args.service

    pdf2zh_args: list[str] = [
        str(pdf_path),
        "--lang-in", args.lang_in,
        "--lang-out", args.lang_out,
        "--service", service_spec,
        "--output", str(outdir),
        "--thread", str(args.thread),
        "--prompt", str(prompt_file),
    ]
    if args.pages:
        pdf2zh_args += ["--pages", args.pages]
    if args.babeldoc:
        pdf2zh_args += ["--babeldoc"]
    if args.ignore_cache:
        pdf2zh_args += ["--ignore-cache"]
    if args.skip_subset_fonts:
        pdf2zh_args += ["--skip-subset-fonts"]

    # ---- 通过环境变量把 Key 交给 pdf2zh ----
    os.environ["PDFZH_PROMPT_PATH"] = str(prompt_file)
    env_map = {
        "deepseek": "DEEPSEEK_API_KEY",
        "silicon": "SILICON_API_KEY",
        "zhipu": "ZHIPU_API_KEY",
        "modelscope": "MODELSCOPE_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    if api_key and args.service in env_map:
        os.environ[env_map[args.service]] = api_key
    if args.base_url and args.service == "openai":
        os.environ["OPENAI_BASE_URL"] = args.base_url

    print(
        f"\n[1/3] 开始翻译\n"
        f"      文件   : {pdf_path.name}\n"
        f"      引擎   : {args.service}"
        + (f" ({args.model})" if args.model else "")
        + f"\n      范围   : {'全部页' if not pages else args.pages}\n"
        f"      参考文献: {'翻译' if args.translate_references else '保留英文'}\n"
        f"      输出目录: {outdir}\n"
    )

    # 记录翻译前的文件快照，便于之后识别 BabelDOC 的产出
    pdfs_before = {p for p in outdir.rglob("*.pdf")}

    rc = pdf2zh_main(pdf2zh_args)
    if rc not in (0, None):
        print(f"[错误] 翻译环节返回码 {rc}", file=sys.stderr)
        return rc

    if guard_stats.get("calls"):
        print(
            f"\n[保护] 统计：{guard_stats['calls']} 段 | "
            f"规范化 {guard_stats['normalized']} | "
            f"定点重试 {guard_stats['retried']} | "
            f"仍不一致 {guard_stats['failed']}"
        )
        for s in guard_stats.get("failed_samples", []):
            print(f"        ⚠ {s}")

    stem = pdf_path.stem
    mono = outdir / f"{stem}-mono.pdf"

    # BabelDOC 后端的输出命名与旧引擎不同，这里统一发现一下
    if not mono.exists():
        found_mono, _found_dual = _find_babeldoc_outputs(outdir, pdfs_before, stem)
        if found_mono is not None:
            mono = found_mono
            print(f"[信息] BabelDOC 产出译文：{mono.name}")

    if not mono.exists():
        print(
            f"[错误] 未找到译文文件（期望 {stem}-mono.pdf 或 BabelDOC 的产出）\n"
            f"       请检查上面的日志，确认翻译是否成功。\n"
            f"       提示：若失败在「字体子集化(subset_fonts)」一步，\n"
            f"       可加 --skip-subset-fonts 再试一次。",
            file=sys.stderr,
        )
        return 4

    # ---- 拼版 ----
    print(f"\n[2/3] 生成左右/上下对照版式 …")
    sys.path.insert(0, str(HERE))
    from pdf_side_by_side import MergeOptions, merge_side_by_side

    out_pdf = outdir / f"{stem}-对照版.pdf"

    # 默认与 BabelDOC 风格一致：两页 100% 原尺寸、原始坐标并排，无留白无间距，
    # 输出页尺寸正好是原页的 2 倍（Letter -> 1224x792pt）。
    # 显式传了值就以显式值为准。
    merge_opts = MergeOptions(mode=args.layout, framed=args.framed)
    if args.gutter is not None:
        merge_opts.gutter = args.gutter
    if args.padding is not None:
        merge_opts.padding = args.padding

    info = merge_side_by_side(
        pdf_path,
        mono,
        out_pdf,
        merge_opts,
        # 只翻译了部分页时，输出也只保留这几页，
        # 避免把未翻译的原始页面一起塞进对照版。
        only_pages=pages,
    )

    # 默认 lr 下每半页宽度等于原页宽度，所以不会缩小原文；
    # 但对双栏论文，仍提示一下可换成上下布局，便于打印到单张纸上。
    if args.layout == "lr":
        try:
            import pymupdf as _fitz

            from pdf_side_by_side import detect_layout

            suggested, _ = detect_layout(_fitz.open(pdf_path))
            if suggested == "tb":
                print(
                    "\n[提示] 本文档是双栏排版。左右并排不会缩小文字，"
                    "但整页会很宽；若要打印在单张纸上，可加 --layout tb。"
                )
        except Exception:
            pass

    # ---- 清理中间产物 ----
    leftovers = [mono, outdir / f"{stem}-dual.pdf"]
    if args.keep_mono:
        leftovers = [outdir / f"{stem}-dual.pdf"]
    for f in leftovers:
        try:
            if f.exists():
                f.unlink()
        except Exception as exc:
            print(f"[提示] 未能删除中间文件 {f.name}：{exc}", file=sys.stderr)

    # 页码可能不连续，如实列出而不是硬写成区间
    pages_desc = "、".join(str(p) for p in info["source_pages"][:12])
    if len(info["source_pages"]) > 12:
        pages_desc += f" … 共 {len(info['source_pages'])} 页"

    print(
        f"\n[3/3] 完成 ✅\n"
        f"      输出   : {info['output']}\n"
        f"      页数   : {info['pages']} 页（原文第 {pages_desc} 页）\n"
        f"      版式   : {info['mode']}（{info['reason']}）\n"
        f"      体积   : {info['size_bytes'] / 1048576:.1f} MB\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
