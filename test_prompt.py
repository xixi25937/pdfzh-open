#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：提示词是否正确组装（含术语表注入与变量占位符）。"""

from __future__ import annotations

import sys
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pdfzh import build_prompt, load_glossary, write_prompt  # noqa: E402

ok = True

gloss = load_glossary()
print(f"[1] 术语表片段长度: {len(gloss)}")
print(gloss[:300] if gloss else "  （未启用术语表）")
if "GLOSSARY" not in gloss and gloss:
    print("  ✗ 术语表片段缺少 GLOSSARY 标记")
    ok = False

for keep_refs in (True, False):
    path = write_prompt(keep_refs=keep_refs)
    text = path.read_text(encoding="utf-8")
    print(f"\n[2] {path.name}: {len(text)} 字符")
    checks = {
        "占位符硬规则 RULE 0": "RULE 0" in text,
        "文档类型自适应 RULE 2": "DETECT THE DOCUMENT TYPE" in text,
        "参考文献策略 RULE 5": "RULE 5" in text,
        "术语表 RULE 6": ("RULE 6" in text) == bool(gloss),
        "变量 ${text}": "${text}" in text,
        "变量 ${lang_out}": "${lang_out}" in text,
        "未留下裸 $text": "$text" not in text.replace("${text}", ""),
    }
    for name, passed in checks.items():
        print(f"    {'✅' if passed else '❌'} {name}")
        ok = ok and passed

    # 关键：用与 pdf2zh 相同的替换方式，确认正文里的 $ 或 {...} 不会被吃掉
    tricky = 'Cost is $5 and {v12} plus \\alpha, ratio $x^2$, email a@b.com.'
    rendered = Template(text).safe_substitute(
        {"lang_in": "en", "lang_out": "zh", "text": tricky}
    )
    print(f"    {'✅' if tricky in rendered else '❌'} 含 $ 与 {{}} 的正文原样嵌入")
    if tricky not in rendered:
        ok = False

print("\n结论：" + ("提示词组装正常 ✅" if ok else "存在问题 ❌"))
sys.exit(0 if ok else 1)
