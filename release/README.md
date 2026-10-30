# PDFZH · PDF 中文对照阅读工具

选择英文 PDF，生成左侧原页、右侧中文的 PDF。装有 TeX Live 时默认使用原位模式（original）：中文排在原文位置，版式与原文一致。没装 TeX Live 时自动改用重排模式（reflow）：中文允许重排和续页，续页重复对应原页，并注明页码，右侧不要求与原版框线、段落位置一一重合。

**只有文字翻译使用 DeepSeek Chat API。** 解析、版面识别、OCR、字体、分页和检查在本机运行。无需作者源码；默认的原位模式需要 TeX Live，没装时自动退回 reflow 模式。

目前是 Windows 试验版。自动检查覆盖已检测的文字和排版区域，不代表任意 PDF 都能完整识别或准确翻译。

## 初次使用

1. 下载本项目源码 ZIP 并解压，不要在压缩包里直接运行。
2. 安装 [Python 3.12 64 位](https://www.python.org/downloads/windows/)，勾选 Add Python to PATH。
3. 双击 `start.cmd`。首次安装依赖可能需要几分钟，版面模型首次使用时下载。
4. 按提示配置自己的 [DeepSeek API Key](https://platform.deepseek.com/api_keys)，选择英文 PDF。

以后双击 `start.cmd` 选文件，或把 PDF 拖到它上面。结果在原 PDF 旁的 **翻译结果** 文件夹；完成后自动打开。双击 `results.cmd` 找最近结果，`configure.cmd` 更换 Key。

本项目是源码发行包，并非免安装 EXE。不包含 Python、模型、字体或用户文档。OCR 模型随 RapidOCR 依赖安装；版面模型由 BabelDOC 下载。默认的原位排版模式另需免费 TeX Live（没装则自动用 reflow）。

## 两种排版模式

| 模式 | 适用情况 | 行为 |
| --- | --- | --- |
| 默认 `original`（找得到 xelatex、latexmk、fc-match 时） | 带文字层的论文、芯片手册；版式与原文一致 | XeLaTeX 原位排版；不能安全替换或放不下的部分保留英文，会报告 |
| `reflow`（没有 TeX Live 时自动使用，或加 `--mode reflow`） | 扫描页、混合尺寸、旋转页、长译文；没有系统测试过，只算兜底 | 右侧重新分页；原图和公式截图保留；图表文字以译注形式放在图表后 |

重排模式保留可提取的颜色、粗体和斜体信息。嵌入数学字体与部分上标使用原文图像保护，不让翻译模型重写公式。OCR 提取的文字通常没有可靠的颜色/粗体语义对应，不能保证恢复其局部样式。

图表以原图加译注展示，**不是重画全部中文图表**。复杂表格、图内标签、公式边界、异常字体和 OCR 的漏识别仍需人工复核。模糊扫描、手写、艺术字体不能保证识别。原页始终在左侧保留，可随时核对。

## 命令

```powershell
.\pdfzh.cmd "C:\资料\英文手册.pdf"
.\pdfzh.cmd "C:\资料\英文手册.pdf" --offline
.\pdfzh.cmd "C:\资料\英文手册.pdf" --analyze-only
.\pdfzh.cmd "C:\资料\英文手册.pdf" --preview-cache
.\pdfzh.cmd "C:\资料\英文手册.pdf" --mode original
.\pdfzh.cmd "C:\资料\英文手册.pdf" --output "D:\PDF翻译" --no-open
.\pdfzh.cmd doctor
```

- `--offline`：不调用翻译 API，缺少译文就停止；不会把英文冒充译文。
- `--analyze-only`：仅本地分析，列出待翻译清单，不调用翻译。
- `--preview-cache`：仅用旧缓存生成明确标记的预览 PDF；缺译文处显示“缺译文缓存”及原文。不会调用翻译，也不会覆盖正式成品。
- 上述开关禁止翻译调用，不是全机断网开关；首次版面模型未缓存时仍会下载免费模型。

新解析方式可能改变文字分段或增加 OCR 文字。完全匹配的旧缓存直接复用；不能安全匹配的片段列入 `pending-translations.json`。可先分析或预览，再决定是否补译。不会仅因改页宽或重新排版就重译。

0.2.1 修复逐词样式拆分与空格校验错误：连续同样式的文字合并发送，保留实际颜色、粗体和公式边界，纯空格和符号在本地还原。兼容旧缓存；再次运行会先尝试恢复 `translation-errors.json` 中通过检查的旧回复，只补译仍缺失的片段。不要删除这个文件或译文缓存。空译文、丢失保护内容、错乱编号仍会拦截；翻译不完整时，`--offline` 无法补齐。

0.2.2 区分 PDF 的字面符号与 LaTeX 格式语法，避免把普通花括号误判为未闭合命令。允许保护词随中文语序在相同样式的片段间移动，仍检查全段数量、颜色/粗体归属、公式与数字完整性。翻译前先在本机检查全部原文保护结构及样式重组；结构有误时停止，不发送付费翻译请求。通过新校验的已保存回复可用 `--offline` 免费恢复并排版。

## 输出与质量报告

顶层文件是 `<原名>-中英对照.pdf`，缓存预览另存 `<原名>-缓存预览.pdf`。同名不同内容的 PDF 缓存独立；同名顶层快捷 PDF 会更新。阅读器占用时另存“修订”版。

文档子目录中的文件：

- original 模式（默认）：`qa.json`（质检报告）、`structure.json`（版面结构）、`layout.json`（版面模型结果）、`text-cache.json`（译文缓存，不要删除）。以下几项是 reflow 模式的文件。
- `质量报告.html`：可直接打开，显示译文覆盖数量和识别待复核位置。
- `document.json`：原页尺寸、文字、来源、位置、样式与公式/图片资源。
- `pending-translations.json`：本次翻译前缺缓存的片段。
- `reflow-qa.json`：逐页对应关系、续页数、缺译文和识别问题。
- `reflow-text-cache.json`、旧版 `text-cache.json`：译文缓存，不要删除。
- `ocr-cache`：本地识别缓存，再次排版可复用。

“已提取文字已翻译”只表示已识别的片段有译文。OCR 置信度不是正确率，不能证明没有漏字。无法可靠识别的区域保留原图并报告，不会显示为全部翻译完成。

## 隐私和费用

- 仅待翻译文字发往 DeepSeek 官方接口 `https://api.deepseek.com`，模型固定 `deepseek-chat`。图像不发送给翻译接口。
- 数字和样式保护校验失败时可能重试，这仍会产生文字翻译费用。
- Key 通过 Windows DPAPI 加密，保存在 `%LOCALAPPDATA%\PDFZH\settings.json`，绑定当前 Windows 用户；不在命令行和日志里。已控制当前用户账户的程序仍可能解密它。
- 支持 `DEEPSEEK_API_KEY` 环境变量，优先于已保存配置。配置不调用接口、不检查余额。
- 译文、OCR 缓存、质量报告包含原文与路径，**不要上传配置、缓存或未经授权的文档**。

## 作者源码入口

```powershell
.\pdfzh-latex.cmd "C:\资料\paper.pdf" --source "C:\资料\tex-source" --main main.tex
```

独立试验入口，需要 TeX Live，不会自动替代默认模式。arXiv 编号文件名可尝试下载源码，找不到会停止。重编译可能改变分页。不要编译不可信源码；禁用 shell escape 不等于完整沙箱。

## 开发与发布

```powershell
.\.venv-next\Scripts\python.exe -m unittest test_pdfzh_user test_pdfzh_nosource test_pdfzh_latex test_pdfzh_reflow
.\.venv-next\Scripts\python.exe build_release.py
```

回归测试包含真实本地 OCR、长译文续页、混合尺寸、旋转页、空白页、数学图像保护、颜色粗体、双栏顺序以及禁止翻译调用的离线流程。测试样例不能代表所有 PDF。

打包脚本使用文件白名单，生成干净源码目录、ZIP、SHA256 和文件清单。开发仓库本身已公开或即将公开，密钥、缓存、PDF 由 `.gitignore` 排除；ZIP 可放在 GitHub Releases。尚未在全新 Windows 虚拟机完成安装验收；DLL 加载失败可检查 [VC++ x64 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)。

## 许可证

**AGPL-3.0-only**，见 [LICENSE](LICENSE)。依赖和模型遵循各自许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。不附带或授予论文、手册、字体、模型权重的再分发权利。
