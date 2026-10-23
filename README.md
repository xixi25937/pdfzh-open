# pdfzh - PDF 中英对照翻译工具

## 新版简洁入口与开源包

0.2 默认采用完整文字重排：本地 OCR、右侧中文续页、原页位置标记和质量报告。默认不再依赖 TeX Live；旧原位模式用 `--mode original`。缓存预览用 `--preview-cache`，不会调用翻译接口。最新用法以 [release/README.md](release/README.md) 为准。

现在双击 `start.cmd` 即可首次配置 DeepSeek API、选取 PDF，完成后自动打开原 PDF 旁的“翻译结果”。双击 `results.cmd` 找最近结果，`configure.cmd` 更换密钥。`pdfzh.cmd` 已切换至该简洁入口；旧 BabelDOC 入口保留为 `pdfzh-babeldoc.cmd` / `pdfzh-run.cmd`。

开源版说明在 [release/README.md](release/README.md)。运行 `build_release.py` 按白名单生成 `dist/` 干净源码包；不要把此开发工程整个上传，里面仍有私有配置、文档和旧实验文件。

以下为历史入口说明，选项不适用于新的 `pdfzh.cmd`。

新增 **无作者源码的试验入口**：`pdfzh-nosource.cmd`，适用于带文字层的 PDF，不需要从网上找论文源码。使用本地版面识别、表格提取和 XeLaTeX 文字覆盖，输出逐页左英右中；只由 DeepSeek Chat 翻译文本。支持 `--analyze-only` 零翻译分析、`--offline` 缓存重排。详见 [无源码工具说明](无源码工具说明.md)。这是独立入口，尚未替换下面的旧默认入口；扫描件、旋转文字与复杂公式混排仍有限制。

新增可独立调用的 **LaTeX 源码翻译试验版**：`pdfzh-latex.cmd`。本篇译文已缓存，可加 `--offline` 免费重排。公式由原始 LaTeX 定义编译，分页与原 PDF 可能不同；详见 [LaTeX 使用说明](LATEX使用说明.md)。旧默认入口未切换。

把英文 PDF 翻译为中文，并生成同页左英文、右中文的矢量双语 PDF。

- 翻译：DeepSeek，默认 `deepseek-chat`
- 排版：本地 BabelDOC 0.6.4
- 成本：只有发送给 DeepSeek 的文字翻译消耗额度
- 本地完成：版面识别、字体、公式/图片处理、拼版、缓存和质检
- 产物：`<原名>-中英对照.pdf` 和同名 `.qa.json`

当前默认版是根据样本
`2410.24164v4_dual_智谱4Flash.pdf` 的 BabelDOC 版本和页面结构重新整理的。
旧工程仍保留，可通过 `pdfzh_legacy.cmd` 调用。

## 直接使用

在 PowerShell 中运行：

```powershell
cd C:\Users\Lenovo\Desktop\具身智能\pdfzh

# 首次安装
.\setup.ps1

# 翻译完整 PDF
.\pdfzh-run.cmd "C:\路径\论文.pdf"

# 先试前三页，确认效果并节省额度
.\pdfzh-run.cmd "C:\路径\论文.pdf" --pages 1-3

# 只检查配置，不调用 DeepSeek
.\pdfzh-run.cmd "C:\路径\论文.pdf" --dry-run

# 查看参数
.\pdfzh-run.cmd --help
```

默认输出目录是 `pdfzh\output`。可自行指定：

```powershell
.\pdfzh-run.cmd "C:\路径\论文.pdf" -o "D:\翻译结果"
```

重复运行不会覆盖已有成果，会自动生成 `-2`、`-3` 等新文件名。

## DeepSeek 配置

工具默认读取 `config.json`：

```json
{
  "api_keys": {
    "deepseek": "你的 DeepSeek API Key"
  },
  "defaults": {
    "model": "deepseek-chat",
    "thread": 8
  }
}
```

正常使用不需要在命令行输入 Key。程序会通过运行期间的临时配置把 Key
交给排版引擎，避免 Key 出现在子进程命令行和普通日志中；临时配置随后自动删除。

## 输出效果

每个输出页的宽度为原页的两倍，高度保持不变：

```text
┌──────────────────────┬──────────────────────┐
│ 原始英文页            │ 中文翻译页            │
│ 原版位置、公式和图表   │ 与原版对应的版式       │
└──────────────────────┴──────────────────────┘
```

左右两半都保留为 PDF 矢量内容，文字可搜索和复制。工具会在翻译后重新检测
`figure` 区域，把可能被排版引擎误处理的科研图恢复为原版矢量图；图注仍翻译成中文。

## 常用参数

| 参数 | 作用 |
|---|---|
| `--pages 1-3` | 只翻译并输出指定页 |
| `-o 目录` | 指定输出目录 |
| `--model deepseek-chat` | 指定 DeepSeek 模型 |
| `--qps 4` | 调整并发数 |
| `--keep-mono` | 额外保留纯中文版 |
| `--ignore-cache` | 忽略缓存并重新翻译 |
| `--glossary 文件.csv` | 使用指定术语表 |
| `--dry-run` | 仅检查配置，不翻译 |
| `--debug` | 保留更详细的诊断信息 |

建议先用 `--pages 1-3` 观察复杂图表、双栏正文和公式页面，再决定是否翻译全文。

## 术语表

默认术语表是 `glossary.csv`，格式与 BabelDOC 一致。可直接编辑或使用另一个文件：

```csv
source,target,tgt_lng
flow matching,流匹配,zh
embodied agent,具身智能体,zh
```

## 质检

每次成功翻译都会生成 `.qa.json`，检查：

- 输出页数是否与所选页一致
- 页面是否为左右双页宽度
- 左侧是否保留原文文本层
- 右侧是否包含可搜索的中文文本层
- 恢复了多少个原版图块

离线自测：

```powershell
.\.venv-next\Scripts\python.exe -m unittest -v test_pdfzh_next.py
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `pdfzh-run.cmd` | 默认的一键入口，带启动与等待提示 |
| `pdfzh_next.py` | BabelDOC 0.6.4 + DeepSeek 主程序 |
| `setup.ps1` | 安装环境并准备免费本地资源 |
| `prepare_assets.py` | 可断点续传的最小资源准备程序 |
| `glossary.csv` | 默认术语表 |
| `sitecustomize.py` | 限制字体资源到英文和简体中文所需集合 |
| `test_pdfzh_next.py` | 新版离线回归测试 |
| `pdfzh_legacy.cmd` | 旧工程回退入口 |

## 已知边界

- 正常文本型 PDF 效果最好；纯扫描件需要 OCR，当前未把 OCR 作为默认流程。
- 极端复杂的表格、嵌套公式或艺术字可能需要人工检查。
- 图片内部的英文按原图保留，不会为了翻译而栅格化重绘。
- 正文文本会发送给 DeepSeek；本地排版模型不会产生额外费用。

## 许可

排版内核 BabelDOC 使用 AGPL-3.0。个人和学术使用时仍应遵守其许可；若把工具
部署为对外网络服务或商用产品，需要单独评估 AGPL 的发布义务。
