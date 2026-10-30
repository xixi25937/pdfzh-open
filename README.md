# pdfzh：英文 PDF 论文 / 芯片手册 → 左原文右中文的对照 PDF

pdfzh 把带文字层的英文 PDF（论文、芯片手册）翻译成中文，输出一份双栏对照 PDF：每页左边是原页，右边是同一版面的中文页。翻译由 DeepSeek 完成，解析、擦除原文和排版（XeLaTeX）都在本机运行。仅支持 Windows。

## 能做什么，不能做什么

| 能做 | 限制 |
|---|---|
| 正文逐段翻译，中文排在原文位置，字体颜色、粗细斜体尽量与原文一致 | 偶尔有一两段保留英文，覆盖率指标看不出（见下文） |
| 公式、数字、型号、网址用原版字形保护，不交给模型 | 公式混排很密的段落，译文语序或断句可能别扭 |
| 参考文献、图里的小字、表头、作者单位保留英文 | 这些部分不会被翻译，这是有意的设计 |
| 单个文本框或单页检查不通过时，只把那一块退回原文，整篇仍然输出 | 退回原文的位置会记录在 `qa.json`，需要自己看图 |
| 译文缓存在本地，`--offline` 可以零费用重新排版 | 排版规则改动后缓存可能对不上，需要补译缺失片段 |
| 文字版 PDF；没有文字层的页面原样保留 | 整份都是扫描件时，默认的原位模式会拒绝处理，没有接入 OCR |
| 字体编码异常（文字抽出来是乱码）的 PDF 会打印警告 | 这类 PDF 的文字不会被翻译 |

## 效果与质量

下面的数字来自作者自己的测试集，统计方法写在 [交接说明.md](交接说明.md)：

- 单元测试 136 个，全部通过。
- 回归集 94 份文档（论文和芯片手册）离线重排：93 份返回码 0。例外是一份 68 页的运放手册（LM358），只翻译过前 20 页，缺译文所以返回 3。
- 盲测（用没见过的文档，不改程序直接跑）累计 57 份达标，测过的文档总数约 58。达标的定义：返回码 0；正文覆盖率不低于 95%；正文没有整段退回英文；没有字压字到读不清；参考文献、表头、图里的小字不算正文。每份至少看过 3 页图。
- 其中首轮不改程序就达标的约 39 份（按各批表格汇总，达标标准中途放宽过，仅供参考）。其余是发现问题、修了程序以后才达标。换一份新文档，遇到新问题的概率不低。
- 芯片手册样本 17 份，来自 TI、ST、NXP、Microchip、Rohm、Toshiba、Allegro、Vishay、Diodes、Infineon、树莓派，生成器各不相同。
- **覆盖率指标会漏掉一类问题。** 例如一个字都没译的乱码字体 PDF 曾显示覆盖率 100%（现在会警告），一行落在 slot 之外的短英文也不计入覆盖率。判断质量要看输出的图。

## 依赖

- Windows（只在作者的 Windows 机器上测试过），64 位 Python 3.12（安装脚本也接受 3.11）。
- DeepSeek API 密钥（`deepseek-chat` 模型）。联网只用于翻译。
- TeX Live（`xelatex`、`latexmk`、`fc-match` 需要在 PATH 里），以及 Windows 自带的 Microsoft YaHei、Arial、Cambria Math、Segoe UI Symbol 字体。默认的 original 模式（版式与原文一致）需要它们。没装 TeX Live 时，命令行会自动改用 reflow 模式（中文在右侧续页，版式与原文不同）。reflow 模式没有系统测试过，只算兜底。
- Python 依赖以 [requirements.txt](requirements.txt) 为准：BabelDOC 0.6.4、PyMuPDF 1.28.2、pypdf 6.19.0、pylatexenc 2.11、rapidocr-onnxruntime 1.4.4。版面模型（DocLayout ONNX）第一次使用时由 BabelDOC 下载。

## 安装与第一次使用

1. 安装 Python 3.12（勾选 Add Python to PATH）和 TeX Live。
2. 运行 `install.cmd`：创建 `.venv-next`，安装 `requirements.txt`，最后运行一次 `doctor`。双击 `start.cmd` 时，如果 `.venv-next` 不存在，会先自动运行它。
3. 配置密钥：运行 `configure.cmd`，在弹出的对话框里粘贴 DeepSeek API 密钥（输入隐藏）。密钥用 Windows DPAPI 加密，保存在 `%LOCALAPPDATA%\PDFZH\settings.json`，只能被当前 Windows 用户解密，配置时不会调用接口。也可以改设环境变量 `DEEPSEEK_API_KEY`，它优先于保存的密钥。
4. 双击 `start.cmd`，在文件选择框里选一份 PDF。完成后会打开结果文件夹。
5. 结果在原 PDF 旁边的 `翻译结果\<名字>-中英对照.pdf`。自动检查没全部通过时，文件名是 `<名字>-中英对照-需复核.pdf`。同目录下的 `翻译结果\<名字>-<哈希>\` 放缓存和质检报告（`text-cache.json`、`qa.json`）。
6. 以后找回最近一份结果：双击 `results.cmd`。

费用：只有文字翻译消耗 DeepSeek 额度。一篇 8–20 页的论文约 3–10 万 token，按作者的使用经验约 0.1–0.3 元（估算，取决于页数和 DeepSeek 当时的价格）。75 页的长论文约 27 万 token。同一份 PDF 重跑会复用缓存，已翻译的片段不再收费。

## 常用命令

在仓库目录里运行：

```
pdfzh.cmd 论文.pdf                    翻译并生成对照 PDF
pdfzh.cmd 论文.pdf --offline          只用已有缓存，不调用接口，不花钱；缺译文时返回 3
pdfzh.cmd 论文.pdf --analyze-only     只检查结构，不翻译
pdfzh.cmd 论文.pdf --output D:\结果   指定输出根目录
pdfzh.cmd 论文.pdf --no-open          完成后不打开文件夹
pdfzh.cmd doctor                      检查 Python 依赖、TeX Live、密钥是否已配置（不联网）
pdfzh.cmd results                     打开最近一次结果所在文件夹
pdfzh.cmd configure                   保存或更换密钥
```

`--mode reflow|original` 可以强制指定模式。`--preview-cache` 只用于 reflow 模式，用缓存出预览。

返回码：

| 码 | 含义 |
|---|---|
| 0 | 成功。仍可能有保留英文的位置，见 `qa.json` |
| 2 | 输入有问题（文件不存在、不是 PDF、下载不完整）或其他错误 |
| 3 | 翻译不完整：离线缺缓存，或密钥无效 / 断网。后一种情况仍会输出 PDF，未译部分保留原文 |
| 4 | 原文片段的本地保护结构检查失败，没有调用接口 |
| 5 | 排版失败，输出的是原文副本，译文缓存保留，可以 `--offline` 重排 |

## 工作原理

original 模式的流程：用 PyMuPDF 抽取文字和版面，BabelDOC 的版面模型划分区域 → 把文字切成段落 → 公式、数学字形和型号做成"孤岛"原样保护，数字和符号换成 `@@P0@@` 这样的占位符 → DeepSeek 翻译，本地校验占位符、数字和人名，不合格带反馈重试 → 擦除原文 → XeLaTeX 把中文排回原位置 → 检查数字、占位符、位置和文字碰撞，哪个框不合格就把那个框退回原文。详细设计、函数名和排查方法见 [交接说明.md](交接说明.md) 第 4 节。

## 已知限制

完整清单和排查线索在 [交接说明.md](交接说明.md) 第 6 节，前几条：

1. 个别很短的行（例如列表项换行后只剩 `is GPT-OSS-20B [10].`）不会被翻译，也没有记录，覆盖率不统计。根因未核实。
2. 模型把数字写错时，翻译阶段不会重试，那一段在排版阶段退回英文。
3. 页面数字检查出现多余数字时，会牵连所有含这个数字的框，偶尔整块退回英文。
4. 伪代码框的行号、多行合并可能错位。
5. 行内公式很密的句子，公式碎片的顺序可能怪。
6. 小瑕疵：作者上标变下标、标题开头的冠词 A 不翻、小节号 "A." 变 "A。"、表格小字被压缩。

## 隐私与安全

- 翻译时只把 PDF 里的文字片段发给 `https://api.deepseek.com`。图片和公式字形不发送，PDF 文件本身不上传。可以用环境变量 `PDFZH_BASE_URL`、`PDFZH_MODEL`、`PDFZH_API_KEY` 改成别的 OpenAI 兼容接口。
- 密钥保存在本机的 DPAPI 加密配置里，不会写进仓库或日志。`config.json`、`settings.json`、`.env`、缓存和 PDF 都在 `.gitignore` 里。
- 结果目录里的 `text-cache.json` 含原文和译文，不要把它和别人的 PDF 一起发布。
- 安全与隐私注意事项见 [SECURITY.md](SECURITY.md)。PDF 和 LaTeX 都当作不可信输入，这个工具没有做过安全审计。

## 开发与测试

```
.venv-next\Scripts\python.exe -X utf8 -W ignore -m unittest test_pdfzh_nosource test_pdfzh_user test_pdfzh_reflow test_pdfzh_latex
```

约 100 秒，136 个测试。PowerShell 可能显示 exit 1，看输出里的 `OK` 即可。`tools\` 里有回归（`regress.py`）、盲测（`batch.py`）、覆盖率基线对比（`covbase.py`）、页面质检（`audit.py`）、渲染预览（`contact.py`）和工作日志（`worklog.py`）脚本；回归和盲测脚本需要自备测试 PDF 目录（`测试新论文`、`训练资料`，在仓库的上一级目录，不在仓库里）。设计说明、修复历史和下一步建议见 [交接说明.md](交接说明.md)，时间线见 [工作日志.md](工作日志.md)。

## 许可证

AGPL-3.0-only，见 [LICENSE](LICENSE)。依赖的 BabelDOC 和 PyMuPDF / MuPDF 同为 AGPL，其余依赖的许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。字体（Microsoft YaHei 等）、DocLayout 模型权重和完整的测试论文都不随仓库发布；`test-island-*.pdf` 是从论文页面裁出的单元测试夹具，来源见交接说明第 6 节。贡献方式见 [release/CONTRIBUTING.md](release/CONTRIBUTING.md)：提交前跑单元测试，不要上传密钥、缓存或没有授权的 PDF。
