# LaTeX 源码翻译试验版

使用作者真实 LaTeX 源码，保留公式、图片、表格数值、引用和作者信息，由 XeLaTeX 重新排版中文。只把文字与不透明占位符发给 `deepseek-chat`。适合能取得 `.tex` 源文件的论文，不能把任意 PDF 自动还原为准确的 LaTeX。

## 当前论文直接使用

```powershell
cd "C:\Users\Lenovo\Desktop\具身智能\pdfzh"
.\pdfzh-latex.cmd "..\2605.15055v2.pdf" --offline
```

本篇译文已缓存。`--offline` 禁止翻译 API 和源码下载；缺少缓存就停止，不偷偷付费。修改本地排版设置后可以反复离线编译。

## 新论文

如果文件名是 arXiv 编号（例如 `2605.15055v2.pdf`），工具优先找本地源码，没有则尝试从 arXiv 获取作者源码。首次执行只对缓存缺失的文字调用 DeepSeek。

```powershell
.\pdfzh-latex.cmd "C:\论文\2605.15055v2.pdf"
```

其他文件名需要自行提供源码目录；主文件不是 `main.tex` 时指定 `--main`：

```powershell
.\pdfzh-latex.cmd "C:\论文\paper.pdf" --source "C:\论文\source" --main main.tex --outdir "C:\论文\latex-output"
```

先检查分段而不翻译：加 `--prepare-only`。翻译配置读取现有 `config.json` 或 `DEEPSEEK_API_KEY`，模型固定为 `deepseek-chat`，不会打印密钥。

## 输出与限制

默认目录是 `output\latex`，包含中文 PDF、左右对照试验 PDF、可编辑的 `project` 源码、`text-cache.json` 和 `qa.json`。缓存请保留，否则下次可能需要补译。

**当前是重新分页的试验版，不是逐页对应的正式成品。** 本篇中文页数与英文不同，左右对照按物理页号拼接，每页顶部有英文提示。不能把它当作同一段落左右一一对齐。原版 PDF 和旧正式输出不被覆盖。

工具校验占位符完整性和公式源码，禁止翻译模型插入 TeX 命令，并检查编译日志。即使编译成功，报告也标记 `experimental_review_required`，不宣称全文翻译或排版完美。复杂自定义宏、特殊文档类、源码缺失或扫描 PDF 需要另行适配；图片内部的文字维持原图。

LaTeX 本地编译使用 `-no-shell-escape`。需要本机 `xelatex`、`latexmk`、`bibtex` 及文档所需宏包；Python 依赖见 `requirements-latex.txt`。
