#!/usr/bin/env python3
"""Focused, offline tests for the BabelDOC 0.6.4 wrapper."""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

import pymupdf

import pdfzh_next as app


class PdfzhNextTests(unittest.TestCase):
    def test_selected_page_indices(self) -> None:
        self.assertEqual(app.selected_page_indices(None, 4), [0, 1, 2, 3])
        self.assertEqual(app.selected_page_indices("3,1-2,9", 4), [0, 1, 2])
        self.assertEqual(app.selected_page_indices("2-", 4), [1, 2, 3])
        self.assertEqual(app.selected_page_indices("-2", 4), [0, 1])
        with self.assertRaises(ValueError):
            app.selected_page_indices("9", 4)

    def test_unique_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "result.pdf"
            target.touch()
            (root / "result-2.pdf").touch()
            self.assertEqual(app.unique_path(target).name, "result-3.pdf")

    def test_suspicious_citation_fragment_detection(self) -> None:
        broken = "[2026, 2025 2025 2026 2026 2022 Labs et al."
        normal = "越来越多的研究 [Liu et al. 2026; Wallace et al. 2024] 表明"
        self.assertTrue(app.is_suspicious_citation_fragment(broken, 20))
        self.assertFalse(app.is_suspicious_citation_fragment(normal, 20))

    def test_translation_tracking_finds_empty_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tracking = {
                "page": [
                    {
                        "paragraph": [
                            {"input": "source", "output": "", "pdf_unicode": "source"},
                            {"input": "other", "output": "译文", "pdf_unicode": "other"},
                        ]
                    }
                ]
            }
            (root / "translate_tracking.json").write_text(
                json.dumps(tracking, ensure_ascii=False), encoding="utf-8"
            )
            result = app.inspect_translation_tracking(root)
            self.assertTrue(result["found"])
            self.assertEqual(result["paragraphs"], 2)
            self.assertEqual(result["empty_outputs"], 1)

    def test_secret_is_not_in_subprocess_arguments(self) -> None:
        secret = "sk-test-never-log-this"
        with tempfile.TemporaryDirectory() as folder:
            path = app.write_babeldoc_secret_config(Path(folder), secret)
            command = ["babeldoc", "--config", str(path), "--openai"]
            self.assertNotIn(secret, " ".join(command))
            self.assertIn(secret, path.read_text(encoding="utf-8"))
            path.unlink()

    def test_figure_restoration_removes_old_text_layer(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_path = root / "source.pdf"
            dual_path = root / "dual.pdf"

            source = pymupdf.open()
            source_page = source.new_page(width=100, height=100)
            source_page.draw_rect(
                pymupdf.Rect(20, 20, 60, 60),
                color=(1, 0, 0),
                fill=(1, 0, 0),
            )
            source_page.insert_text((25, 45), "ORIGINAL", fontsize=6)
            source.save(source_path)
            source.close()

            dual = pymupdf.open()
            page = dual.new_page(width=200, height=100)
            with pymupdf.open(source_path) as source_doc:
                page.show_pdf_page(pymupdf.Rect(0, 0, 100, 100), source_doc, 0)
            page.draw_rect(
                pymupdf.Rect(120, 20, 160, 60),
                color=(0, 0, 1),
                fill=(0, 0, 1),
            )
            page.insert_text((125, 45), "BADTEXT", fontsize=6)
            dual.save(dual_path)
            dual.close()

            count = app.restore_figure_regions(
                source_path,
                dual_path,
                [0],
                [[(20, 20, 60, 60)]],
            )
            self.assertEqual(count, 1)
            with pymupdf.open(dual_path) as result:
                right = result[0].get_text(
                    "text", clip=pymupdf.Rect(100, 0, 200, 100)
                )
                self.assertIn("ORIGINAL", right)
                self.assertNotIn("BADTEXT", right)
                pixel = result[0].get_pixmap(alpha=False).pixel(130, 30)
                self.assertGreater(pixel[0], 200)
                self.assertLess(pixel[2], 80)

    def test_qa_uses_selected_source_page_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_path = root / "source.pdf"
            dual_path = root / "dual.pdf"

            source = pymupdf.open()
            for width in (100, 140, 180):
                page = source.new_page(width=width, height=100)
                page.insert_text((10, 20), "English source")
            source.save(source_path)
            source.close()

            dual = pymupdf.open()
            for width in (140, 180):
                page = dual.new_page(width=width * 2, height=100)
                page.insert_text((10, 20), "English source")
                page.insert_text((width + 10, 20), "中文译文", fontname="china-s")
            dual.save(dual_path)
            dual.close()

            qa = app.verify_dual_pdf(source_path, dual_path, "2-3")
            self.assertEqual(qa["status"], "pass")
            self.assertEqual(qa["median_width_ratio"], 2.0)
            self.assertEqual(qa["output_pages"], 2)

    def test_stale_caption_fill_is_removed_without_removing_text(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_path = root / "source.pdf"
            dual_path = root / "dual.pdf"

            source = pymupdf.open()
            source_page = source.new_page(width=100, height=100)
            source_page.draw_rect(
                pymupdf.Rect(20, 60, 35, 68),
                color=None,
                fill=(0.9, 0.9, 0.9),
            )
            source.save(source_path)
            source.close()

            dual = pymupdf.open()
            page = dual.new_page(width=200, height=100)
            page.insert_text((110, 67), "1024x1024", fontsize=6)
            page.draw_rect(
                pymupdf.Rect(120, 60, 135, 68),
                color=None,
                fill=(0.9, 0.9, 0.9),
            )
            dual.save(dual_path)
            dual.close()

            stats = app.remove_stale_translated_caption_fills(dual_path, source_path)
            self.assertEqual(stats["removed_caption_fills"], 1)
            with pymupdf.open(dual_path) as result:
                self.assertIn("1024x1024", result[0].get_text())
                self.assertFalse(
                    any(
                        d["type"] == "f"
                        and d.get("fill")
                        and d["rect"].x0 > 100
                        and d["rect"].y0 >= 60
                        and d["rect"].y1 <= 68
                        for d in result[0].get_drawings()
                    )
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
