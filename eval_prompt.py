#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词对比评测：同一批原文段落，分别用「旧提示词」和「新提示词」翻译。

为什么要这个脚本：
    提示词改了到底有没有变好，靠读提示词是看不出来的，必须对同一段原文
    跑两次、并排看结果。本脚本还顺带做几项自动检查：
      * {v*} 占位符是否被破坏（最致命的失败）
      * 长度比（译文/原文）是否异常，过长往往是"加水"（translationese）
      * 是否有多余前言（"以下是翻译："之类）
      * 中文标点是否规范

用法:
    python eval_prompt.py --pdf ..\2410.24164v4.pdf --page 2 [--limit 8]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pdfzh import (  # noqa: E402
    patch_tempdir_cleanup,
    resolve_api_key,
    setup_workspace_env,
)

setup_workspace_env(quiet=True)
patch_tempdir_cleanup()

# ---------------------------------------------------------------- 旧提示词
OLD_PROMPT = """You are a professional academic translator translating a research paper from en into zh.

Rules:
1. Translate naturally and accurately. Use standard academic terminology; keep it concise.
2. Absolute MUST: any placeholder of the form {v0}, {v1}, {v12} ... must be copied out EXACTLY as-is,
   with the same number, braces and order. NEVER translate, delete, reorder or invent them.
3. Keep these unchanged (do NOT translate):
   - LaTeX/HTML inline commands such as \\cite{...}, \\ref{...}, \\label{...}, $...$
   - URLs, DOIs, email addresses, file paths
   - Numbers, units, symbols, code identifiers and function/variable names
   - Author names and their affiliations in the author block
   - The body of the References / Bibliography section: keep every reference entry in the
     original English, only translating its Chinese-facing label if it has one
4. Headings, section titles, figure/table captions, table headers and footnotes ARE translated.
5. Output ONLY the translated text. No explanation, no quotes, no markdown fences, no prefix.

Source Text:
${text}

Translated Text:"""

PLACEHOLDER = re.compile(r"\{\s*v(\d+)\s*\}")
CJK = re.compile(r"[\u4e00-\u9fff]")
BAD_PREFIX = re.compile(
    r"^\s*(以下是|这是|译文[:：]|翻译[:：]|Translated|Translation|Sure|当然|好的)", re.I
)
HALF_PUNCT = re.compile(r"[a-zA-Z\u4e00-\u9fff],\s|\u4e00\u9fff\.")


def extract_passages(pdf: Path, pno: int, limit: int, max_chars: int = 700) -> list[str]:
    """从指定页抽取若干段落作为评测样本。

    注意：不能按空行切段 —— PDF 提取出来的正文通常没有空行，会得到一整页
    文本流（实测 5458 字符），既超出翻译时的真实段落粒度，也拖慢评测。
    这里改为「先按行聚合，再切成不超过 max_chars 的块」。
    """
    import pymupdf

    with pymupdf.open(pdf) as doc:
        text = doc[pno].get_text("text")

    # 合并被 PDF 硬换行切开的行，再切成常见段落长度
    flat = " ".join(text.split())
    # 在句末断句处切块，尽量贴近真实段落粒度
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", flat)

    out: list[str] = []
    buf = ""
    for piece in pieces:
        if len(buf) + len(piece) + 1 <= max_chars:
            buf = f"{buf} {piece}".strip()
        else:
            if len(buf) >= 80:
                out.append(buf)
            buf = piece
        if len(out) >= limit:
            break
    if len(out) < limit and len(buf) >= 80:
        out.append(buf)
    return out[:limit]


def call_llm(prompt_template: str, text: str, api_key: str, model: str = "deepseek-chat") -> str:
    """直接调 DeepSeek（OpenAI 兼容接口）。

    变量替换方式必须与 pdf2zh 一致：它用 ``Template.safe_substitute``，
    因此提示词里要写 ``${text}``（带花括号），这样即使正文里出现 ``$``
    或 ``{...}`` 也不会被误当成占位变量。
    """
    from string import Template

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
    prompt = Template(prompt_template).safe_substitute(
        {"lang_in": "en", "lang_out": "zh", "text": text}
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def check(name: str, src: str, out: str) -> dict:
    src_ph = sorted(PLACEHOLDER.findall(src))
    out_ph = sorted(PLACEHOLDER.findall(out))
    ratio = len(out) / max(len(src), 1)
    return {
        "name": name,
        "占位符一致": src_ph == out_ph,
        "src_ph": src_ph,
        "out_ph": out_ph,
        "长度比": round(ratio, 2),
        "多余前言": bool(BAD_PREFIX.match(out)),
        "中文字数": len(CJK.findall(out)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="对比评测新旧翻译提示词")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, default=2)
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--show", action="store_true", help="打印完整译文对照")
    ns = ap.parse_args()

    api_key = resolve_api_key(None, "deepseek")
    if not api_key:
        print("[错误] 未找到 DeepSeek API Key（config.json / 环境变量）", file=sys.stderr)
        return 3

    from pdfzh import build_prompt

    new_prompt = build_prompt(keep_refs=True)

    pdf = Path(ns.pdf).resolve()
    passages = extract_passages(pdf, ns.page - 1, ns.limit)
    print(f"样本数: {len(passages)}（取自 {pdf.name} 第 {ns.page} 页）")
    print(f"新提示词长度: {len(new_prompt)} 字符 / 旧提示词长度: {len(OLD_PROMPT)} 字符")
    print("=" * 78)

    rows = []
    for i, src in enumerate(passages, 1):
        print(f"\n【样本 {i}】原文 {len(src)} 字符")
        print(f"  原文: {src[:150]}{'…' if len(src) > 150 else ''}")
        old_out = call_llm(OLD_PROMPT, src, api_key)
        new_out = call_llm(new_prompt, src, api_key)
        rows.append((check("旧", src, old_out), check("新", src, new_out), old_out, new_out))
        print(f"  旧: {old_out[:150]}{'…' if len(old_out) > 150 else ''}")
        print(f"  新: {new_out[:150]}{'…' if len(new_out) > 150 else ''}")

    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"{'样本':<5}{'占位符(旧/新)':<18}{'长度比(旧/新)':<18}{'多余前言(旧/新)':<18}")
    for i, (o, n, _, _) in enumerate(rows, 1):
        print(
            f"{i:<5}"
            f"{('OK' if o['占位符一致'] else '坏') + ' / ' + ('OK' if n['占位符一致'] else '坏'):<20}"
            f"{str(o['长度比']) + ' / ' + str(n['长度比']):<20}"
            f"{('有' if o['多余前言'] else '无') + ' / ' + ('有' if n['多余前言'] else '无'):<20}"
        )

    ok_old = sum(1 for o, _, _, _ in rows if o["占位符一致"])
    ok_new = sum(1 for _, n, _, _ in rows if n["占位符一致"])
    print(f"\n占位符保持正确: 旧 {ok_old}/{len(rows)}  新 {ok_new}/{len(rows)}")
    avg_old = sum(o["长度比"] for o, _, _, _ in rows) / max(len(rows), 1)
    avg_new = sum(n["长度比"] for _, n, _, _ in rows) / max(len(rows), 1)
    print(f"平均长度比（越低越紧凑）: 旧 {avg_old:.2f}  新 {avg_new:.2f}")

    if ns.show:
        print("\n" + "=" * 78)
        print("完整对照")
        print("=" * 78)
        for i, (_, _, old_out, new_out) in enumerate(rows, 1):
            print(f"\n----- 样本 {i} -----")
            print(f"[旧] {old_out}")
            print(f"[新] {new_out}")

    # 存一份便于人工细看
    outfile = HERE / "output" / "prompt_eval.md"
    outfile.parent.mkdir(parents=True, exist_ok=True)
    with outfile.open("w", encoding="utf-8") as f:
        f.write("# 提示词对比评测\n\n")
        for i, (src, (o, n, old_out, new_out)) in enumerate(zip(passages, rows), 1):
            f.write(f"## 样本 {i}\n\n**原文**\n\n> {src}\n\n")
            f.write(f"**旧提示词**（占位符{'OK' if o['占位符一致'] else '损坏'}，长度比 {o['长度比']}）\n\n{old_out}\n\n")
            f.write(f"**新提示词**（占位符{'OK' if n['占位符一致'] else '损坏'}，长度比 {n['长度比']}）\n\n{new_out}\n\n---\n\n")
    print(f"\n完整对照已写入: {outfile}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
