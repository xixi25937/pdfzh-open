# Contributing

感谢帮助改进！目前主要支持 Windows，默认采用本地 OCR 与中文重排；旧原位模式另需 TeX Live。

提交问题时请提供：Windows/Python/TeX Live 版本、命令、报错阶段、已脱敏的小样本或局部截图。不要上传 API Key、settings.json、text-cache.json 或未经授权的 PDF。自动检查通过不代表语义翻译正确，请明确区分翻译、文字提取、布局和公式问题。

改动应修复通用规则，不得硬编码某个文档的文件名、页码、坐标。新增回归测试尽量使用自行生成的合成 PDF；测试不能消耗 API 额度。

```powershell
.\.venv-next\Scripts\python.exe -m unittest test_pdfzh_user test_pdfzh_nosource test_pdfzh_latex test_pdfzh_reflow
```

布局变更还需实际渲染检查。提交代码视为按项目 AGPL-3.0-only 许可贡献。发布前运行 build_release.py，并复核白名单、密钥和个人信息检查。
