#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检：确认 pdf2zh 依赖的版面模型与中文字体可以正常获取。

在真正翻译之前先跑这个脚本，可以提前发现「模型下载失败 / 字体缺失 /
缓存目录不可写」等问题，避免翻译跑到一半才报错。

用法:
    python check_env.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pdfzh import setup_workspace_env  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    flag = "✅" if ok else "❌"
    print(f"  {flag} {name}" + (f" —— {detail}" if detail else ""))


def main() -> int:
    print("=" * 68)
    print("pdfzh 环境自检")
    print("=" * 68)

    home = setup_workspace_env()

    # ---- 1. Python 版本 ----
    print("\n[1] Python 解释器")
    v = sys.version_info
    ok_py = (3, 11) <= (v.major, v.minor) <= (3, 12)
    record(
        f"Python {v.major}.{v.minor}.{v.micro}（pdf2zh 要求 3.11–3.12）",
        ok_py,
        "" if ok_py else "版本不符，可能出现兼容问题",
    )
    record("解释器路径", True, sys.executable)

    # ---- 2. 关键依赖 ----
    print("\n[2] 关键依赖")
    for mod in ("pymupdf", "pdf2zh", "babeldoc", "onnxruntime", "pdfminer", "requests"):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "")
            record(f"import {mod}", True, str(ver))
        except Exception as exc:
            record(f"import {mod}", False, f"{type(exc).__name__}: {exc}")

    # ---- 3. 缓存目录可写 ----
    print("\n[3] 缓存目录")
    cache = home / ".cache"
    try:
        probe = cache / "_write_probe.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        record(f"可写 {cache}", True)
    except Exception as exc:
        record(f"可写 {cache}", False, f"{type(exc).__name__}: {exc}")

    # ---- 4. 版面检测模型 ----
    print("\n[4] 版面检测模型（doclayout ONNX）")
    try:
        from babeldoc.assets.assets import get_doclayout_onnx_model_path

        t0 = time.time()
        path = get_doclayout_onnx_model_path()
        size_mb = Path(path).stat().st_size / 1048576
        record(
            "获取 doclayout 模型",
            True,
            f"{Path(path).name} ({size_mb:.1f} MB, {time.time() - t0:.1f}s)",
        )
    except Exception as exc:
        record("获取 doclayout 模型", False, f"{type(exc).__name__}: {exc}")

    # ---- 5. 中文字体 ----
    print("\n[5] 中文字体")
    try:
        from pdf2zh.high_level import download_remote_fonts

        t0 = time.time()
        fp = download_remote_fonts("zh")
        size_mb = Path(fp).stat().st_size / 1048576
        record(
            "获取中文字体",
            True,
            f"{Path(fp).name} ({size_mb:.1f} MB, {time.time() - t0:.1f}s)",
        )
    except Exception as exc:
        record("获取中文字体", False, f"{type(exc).__name__}: {exc}")

    # ---- 6. 拼版模块 ----
    print("\n[6] 拼版模块")
    try:
        from pdf_side_by_side import _cjk_font, detect_layout

        found = _cjk_font()
        record(
            "标签用 CJK 字体",
            found is not None,
            Path(found[1]).name if found else "未找到中文字体，页眉标签会退化为英文",
        )
    except Exception as exc:
        record("拼版模块", False, f"{type(exc).__name__}: {exc}")

    # ---- 7. 翻译引擎连通性 ----
    print("\n[7] 翻译引擎连通性（DeepSeek）")
    try:
        import httpx

        r = httpx.get("https://api.deepseek.com/v1/models", timeout=15.0)
        # 401 说明域名可达且服务正常，只是没带 Key
        record(
            "api.deepseek.com 可达", r.status_code in (200, 401),
            f"HTTP {r.status_code}",
        )
    except Exception as exc:
        record("api.deepseek.com 可达", False, f"{type(exc).__name__}: {exc}")

    key = os.environ.get("DEEPSEEK_API_KEY")
    record(
        "DEEPSEEK_API_KEY",
        bool(key),
        f"已设置（{key[:7]}…）" if key else "未设置，翻译时需用 --key 传入",
    )

    # ---- 汇总 ----
    print("\n" + "=" * 68)
    failed = [r for r in RESULTS if not r[1]]
    if failed:
        print(f"自检完成：{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过，"
              f"{len(failed)} 项需要注意")
        for name, _, detail in failed:
            print(f"  ❌ {name}: {detail}")
        return 1
    print(f"自检完成：{len(RESULTS)}/{len(RESULTS)} 项全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
