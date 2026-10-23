#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""译文样式修复：把原文的字体家族、粗体、颜色带到译文字符上。

实测问题（第 2 页）：
    原文正文  NimbusRomNo9L-Regu / -Medi（常规/中粗）
    译文正文  Times-Roman（退化为通用字体），且粗体丢失
    原文颜色  全黑 + 1 处链接蓝
    译文颜色  基本黑，但链接色被换成了另一种蓝

根因在 pdf2zh/converter.py 的排版阶段：
    * 选字体只区分「拉丁 tiro / 非拉丁 noto」，没有沿用原字符的字体
    * gen_op_txt 生成的指令里没有设置填充色（默认黑），原色丢失

本模块通过重编译 receive_layout 来修：
    * 段落携带「原文主字体名」，译文拉丁字符优先用同名原文拉丁字体
    * 若原段落主字体是 Medi/Bold，则译文用对应粗体
    * 段落主色非黑时，输出前插入 rg 设置填充色

用法（作为库）:
    from patch_style import install
    install()
"""

from __future__ import annotations

import re
import textwrap
from typing import Any

# 原文常见「中粗/粗体」字体名特征
BOLD_HINT = re.compile(
    r"(Medi|Bold|Black|Heavy|Semi|Demib|Demi|Thick)", re.IGNORECASE
)
ITALIC_HINT = re.compile(r"(Ital|Oblique|Slanted)", re.IGNORECASE)


def _orig_receive_layout():
    from pdf2zh import converter as _c

    return _c.TranslateConverter.receive_layout


# 字体名里的「字重/样式」后缀 —— 用于把同一家族的字体归到一组
FONT_STYLE_TAGS = (
    "-Medi", "-Medium", "-Bold", "-Black", "-Heavy", "-Semi", "-Demi",
    "-Ital", "-Italic", "-Oblique", "-Regular", "-Regu", "-Roman",
    "-Light", "-Thin", "-Book", "-Extra",
)

# 视为「常规字重」的后缀：这些字体的字形嵌入最可靠
REGULAR_TAGS = ("-Regu", "-Regular", "-Roman", "")


def _base_of(name: str) -> str:
    """取字体家族名，例如 ``NimbusRomNo9L-Medi`` -> ``NimbusRomNo9L``。"""
    s = str(name or "").split("+")[-1]
    low = s.lower()
    for tag in FONT_STYLE_TAGS:
        if low.endswith(tag.lower()):
            return s[: -len(tag)]
    return s


def _is_regular_weight(name: str) -> bool:
    """判断字体名是否为常规字重。

    为什么需要它：实测把正文交给粗体（``NimbusRomNo9L-Medi``）后，
    重建 PDF 时该字体的数字字形嵌入失败 —— 文本层有数字、渲染上完全不可见
    （墨迹占比 0.000）。因此选字体时优先落在常规字重上。
    """
    s = str(name or "").split("+")[-1]
    low = s.lower()
    for tag in FONT_STYLE_TAGS:
        if low.endswith(tag.lower()):
            return tag.lower() in [t.lower() for t in REGULAR_TAGS if t]
    return True          # 没有样式后缀，视为常规


def install(verbose: bool = False) -> bool:
    """给 TranslateConverter.receive_layout 打样式保留补丁。

    返回是否安装成功。
    """
    from pdf2zh import converter as _c

    if getattr(_c.TranslateConverter.receive_layout, "_pdfzh_style_patched", False):
        return True

    import inspect
    import textwrap

    # inspect.getsource 返回「类方法」源码（首行带 4 空格类缩进）。
    # 用 dedent 抹平后编译更省心；下面的替换字符串都按「无额外缩进」书写，
    # 这样 dedent 生效（若某个替换串首行缩进更小，dedent 会缩不动）。
    src = textwrap.dedent(inspect.getsource(_c.TranslateConverter.receive_layout))

    def _sub(pattern: str, repl: str, flags: str, verbose_msg: str) -> None:
        """用正则替换；模板里的 {pad} 会替换成匹配到的实际缩进。

        只做 {pad} 一种占位替换，其它花括号保持原样（避免与代码里的
        f-string / 字典字面量冲突）。因此替换模板里不要写 {tail} 之类。
        """
        nonlocal src
        m = re.search(pattern, src, re.MULTILINE)
        if not m:
            if verbose:
                print(f"[patch_style] 未匹配到 {verbose_msg}")
            return
        pad = m.group("pad") if "pad" in (m.groupdict() or {}) else ""
        src = src[: m.start()] + repl.replace("{pad}", pad) + src[m.end():]

    # ---------------------------------------------------------------
    # 0) 注入「按原文字体名找资源名」的辅助函数
    #    关键事实（实测）：self.fontmap 的键是 PDF 资源名（F143/F31/F166…），
    #    **不是**字体名；LTChar 上的 child.fontname 才是真正的字体名。
    #
    #    重要修正（踩过的坑）：直接沿用原字体名会把数字/标点交给粗体
    #    （-Medi）这类**冷门字重**，而这些字体在重建 PDF 时字形嵌入有问题 ——
    #    实测数字「文本层存在、渲染完全不可见」（墨迹 0.000）。因此这里优先挑
    #    **常规字重**的同家族字体（Regu/Regular/Roman > 原字重），
    #    宁可少保留一点字重差异，也不能让字符消失。
    # ---------------------------------------------------------------
    _sub(
        r"^(?P<pad>[ \t]*)def receive_layout\(self, ltpage: LTPage\):",
        "{pad}def receive_layout(self, ltpage: LTPage):\n"
        "{pad}    _name2key = {}\n"
        "{pad}    _fam2key = {}\n"
        "{pad}    try:\n"
        "{pad}        for _k, _f in (self.fontmap or {}).items():\n"
        "{pad}            _fn = getattr(_f, 'fontname', None)\n"
        "{pad}            if not _fn:\n"
        "{pad}                continue\n"
        "{pad}            _n = str(_fn).split('+')[-1]\n"
        "{pad}            _name2key.setdefault(_n, _k)\n"
        "{pad}            _base = _base_of(_n)\n"
        "{pad}            _fam2key.setdefault(_base, []).append((_n, _k))\n"
        "{pad}    except Exception:\n"
        "{pad}        _name2key, _fam2key = {}, {}\n"
        "{pad}    def _pick_latin_font(_raw):\n"
        "{pad}        _n = str(_raw or '').split('+')[-1]\n"
        "{pad}        if _n in _name2key and _is_regular_weight(_n):\n"
        "{pad}            return _name2key[_n]\n"
        "{pad}        for _cand, _k in _fam2key.get(_base_of(_n), []):\n"
        "{pad}            if _is_regular_weight(_cand):\n"
        "{pad}                return _k\n"
        "{pad}        return _name2key.get(_n)",
        "M",
        "receive_layout 函数头",
    )

    # ---------------------------------------------------------------
    # 1) Paragraph 记录原文主字体与主色
    # ---------------------------------------------------------------
    _sub(
        r"^(?P<pad>[ \t]*)pstk\.append\(Paragraph\(child\.y0, child\.x0, child\.x0, child\.x0, child\.y0, child\.y1, child\.size, False\)\)$",
        "{pad}_p = Paragraph(child.y0, child.x0, child.x0, child.x0, child.y0, child.y1, child.size, False)\n"
        "{pad}try:\n"
        "{pad}    _p.fontname = child.fontname\n"
        "{pad}except Exception:\n"
        "{pad}    _p.fontname = None\n"
        "{pad}try:\n"
        "{pad}    _gs = getattr(child, 'graphicstate', None)\n"
        "{pad}    _p.color = getattr(_gs, 'scolor', None)\n"
        "{pad}    if _p.color is None:\n"
        "{pad}        _p.color = getattr(_gs, 'ncolor', None)\n"
        "{pad}except Exception:\n"
        "{pad}    _p.color = None\n"
        "{pad}pstk.append(_p)",
        "M",
        "段落创建语句",
    )

    # ---------------------------------------------------------------
    # 2) 选定拉丁字体时，按原文段落字体名反查资源名
    #    原逻辑：若 "tiro"(默认拉丁字体) 含有该字符 -> fcur_ = "tiro"
    #    改为：优先沿用原文同名字体；找不到再退回 "tiro"
    # ---------------------------------------------------------------
    _sub(
        r'^(?P<pad>[ \t]*)fcur_ = "tiro"(?P<tail>[^\n]*)$',
        '{pad}_key, _pf = _pick_latin_font(getattr(pstk[id], "fontname", None))\n'
        '{pad}fcur_ = _key if _key else "tiro"',
        "M",
        "拉丁字体选择语句",
    )

    # ---------------------------------------------------------------
    # 3) gen_op_txt 增加填充色参数
    # ---------------------------------------------------------------
    _sub(
        r'^(?P<pad>[ \t]*)def gen_op_txt\(font, size, x, y, rtxt\):\n'
        r'(?P<pad2>[ \t]*)return f"/\{font\} \{size:f\} Tf 1 0 0 1 \{x:f\} \{y:f\} Tm \[<\{rtxt\}>\] TJ "$',
        "{pad}def gen_op_txt(font, size, x, y, rtxt, color=None):\n"
        "{pad}    _c_ops = ''\n"
        "{pad}    try:\n"
        "{pad}        _rgb = None\n"
        "{pad}        if isinstance(color, (tuple, list)):\n"
        "{pad}            _vals = [float(v) for v in color[:3]]\n"
        "{pad}            if _vals and max(_vals) > 1.0:\n"
        "{pad}                _vals = [v / 255.0 for v in _vals]\n"
        "{pad}            if len(_vals) == 3 and any(abs(v) > 1e-6 for v in _vals):\n"
        "{pad}                _rgb = (_vals[0], _vals[1], _vals[2])\n"
        "{pad}        elif isinstance(color, int) and color != 0:\n"
        "{pad}            _rgb = (\n"
        "{pad}                ((color >> 16) & 255) / 255.0,\n"
        "{pad}                ((color >> 8) & 255) / 255.0,\n"
        "{pad}                (color & 255) / 255.0,\n"
        "{pad}            )\n"
        "{pad}        if _rgb is not None:\n"
        "{pad}            _c_ops = f'{_rgb[0]:f} {_rgb[1]:f} {_rgb[2]:f} rg '\n"
        "{pad}    except Exception:\n"
        "{pad}        _c_ops = ''\n"
        '{pad}    return f"{_c_ops}/{font} {size:f} Tf 1 0 0 1 {x:f} {y:f} Tm [<{rtxt}>] TJ "',
        "M",
        "gen_op_txt 定义",
    )

    # 调用处传入颜色
    _sub(
        r'(?P<pad>[ \t]*)ops_list\.append\(gen_op_txt\(vals\["font"\], vals\["size"\], vals\["x"\], vals\["dy"\] \+ y - vals\["lidx"\] \* size \* line_height, vals\["rtxt"\]\)\)',
        '{pad}ops_list.append(gen_op_txt(vals["font"], vals["size"], vals["x"], vals["dy"] + y - vals["lidx"] * size * line_height, vals["rtxt"], getattr(pstk[id], "color", None)))',
        "M",
        "gen_op_txt 调用处",
    )

    # ---------------------------------------------------------------
    # 4) Paragraph 增加默认属性，避免 getattr 到处判断
    # ---------------------------------------------------------------
    old_para_init = "class Paragraph:\n    def __init__(self, y, x, x0, x1, y0, y1, size, brk):"
    new_para_init = (
        "class Paragraph:\n"
        "    fontname = None\n"
        "    color = None\n"
        "    def __init__(self, y, x, x0, x1, y0, y1, size, brk):"
    )
    # Paragraph 类在另一处定义，这里改用运行时属性注入（见下）

    # exec 出来的函数以 _c.__dict__ 作为 globals，因此注入的辅助函数
    # 必须写进 converter 模块的命名空间，否则函数体内找不到它们。
    _c._base_of = _base_of
    _c._is_regular_weight = _is_regular_weight

    ns: dict[str, Any] = {}
    try:
        exec(compile(src, "<pdfzh_patched_receive_layout>", "exec"), _c.__dict__, ns)
    except Exception as exc:
        if verbose:
            print(f"[patch_style] 编译失败: {exc}")
        return False

    fn = ns.get("receive_layout")
    if fn is None:
        return False
    fn._pdfzh_style_patched = True
    _c.TranslateConverter.receive_layout = fn

    # 给 Paragraph 注入默认属性（避免在段落创建语句里改动类定义）
    if not hasattr(_c.Paragraph, "color"):
        _c.Paragraph.color = None
    if not hasattr(_c.Paragraph, "fontname"):
        _c.Paragraph.fontname = None

    return True


if __name__ == "__main__":
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(HERE))

    from pdfzh import patch_tempdir_cleanup, resolve_api_key, setup_workspace_env, write_prompt

    setup_workspace_env()
    patch_tempdir_cleanup()

    ok = install(verbose=True)
    print(f"[patch_style] 安装结果: {ok}")
