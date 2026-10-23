# Third-party notices

PDFZH source is offered under GNU AGPL version 3 only. Copyright belongs to its contributors.

This is a source-only distribution. Dependencies are installed separately and retain their licenses:

| Component | Purpose | Upstream / license |
| --- | --- | --- |
| BabelDOC 0.6.4 | Local layout detection | https://github.com/funstory-ai/BabelDOC — AGPL-3.0 |
| PyMuPDF 1.28.2 / MuPDF | PDF processing | https://github.com/pymupdf/PyMuPDF — AGPL or commercial; AGPL route here |
| pypdf 6.19.0 | Content stream normalization | https://github.com/py-pdf/pypdf — BSD-3-Clause |
| pylatexenc 2.11 | LaTeX parsing | https://github.com/phfaist/pylatexenc — MIT |
| RapidOCR ONNX Runtime 1.4.4 | Local CPU OCR | https://github.com/RapidAI/RapidOCR — Apache-2.0; installed package includes PaddleOCR model assets, not bundled in this source ZIP |
| TeX Live / XeLaTeX | Local typesetting | https://tug.org/texlive/copying.html — multiple component licenses |
| Microsoft YaHei / Arial / Cambria Math / Segoe UI Symbol | User-installed Windows fonts | Proprietary; not redistributed |
| DocLayout ONNX assets | Downloaded by BabelDOC | Check model upstream license before redistribution; weights not included |

Transitive dependencies retain their individual terms. DeepSeek is an external API, not an included model. No papers, datasheets, fonts or model weights are bundled. Do not redistribute copyrighted or confidential input PDFs without authorization.
