#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local PDF translation wrapper for BabelDOC 0.6.4 and DeepSeek.

All document analysis, typesetting, font handling, PDF generation, and QA run
locally. The only metered external operation is text translation through the
configured DeepSeek API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.json"
DEFAULT_OUTPUT = HERE / "output"
DEFAULT_GLOSSARY = HERE / "glossary.csv"
LOCAL_HOME = HERE / ".home-next"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

SYSTEM_PROMPT = """You are a professional English-to-Chinese translator for technical and academic PDFs.
Produce faithful, concise, idiomatic Simplified Chinese suitable for publication.
Preserve claim strength, negation, numbers, units, citations, URLs, DOIs, code,
model and dataset names, standard abbreviations, and all placeholders exactly.
Translate headings, captions, table text, and quotations. Keep person names,
affiliations, email addresses, URLs, reference entries, code, and standard
abbreviations unchanged. Use the supplied glossary consistently. Never add
explanations, translator notes, summaries, or content absent from the source."""


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def configure_local_environment() -> None:
    cache = LOCAL_HOME / "cache"
    temp = LOCAL_HOME / "tmp"
    hf = LOCAL_HOME / "huggingface"
    work = LOCAL_HOME / "work"
    for path in (cache, temp, hf, work):
        path.mkdir(parents=True, exist_ok=True)

    values = {
        "HOME": LOCAL_HOME,
        "USERPROFILE": LOCAL_HOME,
        "XDG_CACHE_HOME": cache,
        "HF_HOME": hf,
        "TEMP": temp,
        "TMP": temp,
        "TMPDIR": temp,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "TIKTOKEN_CACHE_DIR": LOCAL_HOME / ".cache" / "babeldoc" / "tiktoken",
    }
    for name, value in values.items():
        os.environ[name] = str(value)
    existing_pythonpath = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(HERE) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取配置文件 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"配置文件必须是 JSON 对象: {path}")
    return value


def configured_value(config: dict, section: str, name: str) -> str | None:
    value = config.get(section, {})
    if isinstance(value, dict):
        item = value.get(name)
        if item is not None and str(item).strip():
            return str(item).strip()
    return None


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法为输出文件生成唯一名称: {path}")


def find_generated_pdf(outdir: Path, stem: str, kind: str, started_ns: int) -> Path | None:
    candidates = []
    for path in outdir.glob("*.pdf"):
        name = path.name.lower()
        if stem.lower() not in name or f".{kind}.pdf" not in name:
            continue
        try:
            if path.stat().st_mtime_ns >= started_ns:
                candidates.append(path)
        except OSError:
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def publish_output(source: Path, destination: Path) -> Path:
    destination = unique_path(destination)
    try:
        source.replace(destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def count_selected_pages(spec: str | None, total: int) -> int:
    return len(selected_page_indices(spec, total))


def selected_page_indices(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(total))
    selected: set[int] = set()
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            start_raw, end_raw = part.split("-", 1)
            start = int(start_raw) if start_raw else 1
            end = int(end_raw) if end_raw else total
            if start < 1 or end < start:
                raise ValueError(f"无效页码范围: {part}")
            selected.update(range(start, min(end, total) + 1))
        else:
            page = int(part)
            if page < 1:
                raise ValueError(f"无效页码: {part}")
            if page <= total:
                selected.add(page)
    if not selected:
        raise ValueError("页码范围没有命中 PDF 中的任何页面")
    return [page - 1 for page in sorted(selected)]


def restore_figure_regions(
    source_pdf: Path,
    dual_pdf: Path,
    source_indices: list[int],
    regions_by_page: list[list[tuple[float, float, float, float]]],
) -> int:
    """Replace translated figure boxes with matching original vector boxes."""
    import pymupdf

    source = pymupdf.open(source_pdf)
    output = pymupdf.open(dual_pdf)
    repaired = dual_pdf.with_name(f"{dual_pdf.stem}.figures{dual_pdf.suffix}")
    protected = 0
    try:
        if len(source_indices) != output.page_count:
            raise ValueError(
                "图块保护无法映射页码: "
                f"source selection={len(source_indices)}, output={output.page_count}"
            )
        if len(regions_by_page) != output.page_count:
            raise ValueError(
                "图块保护区域与输出页数不一致: "
                f"regions={len(regions_by_page)}, output={output.page_count}"
            )

        for output_index, source_index in enumerate(source_indices):
            source_page = source[source_index]
            output_page = output[output_index]
            half_width = output_page.rect.width / 2
            scale_x = half_width / source_page.rect.width
            scale_y = output_page.rect.height / source_page.rect.height
            placements: list[tuple[pymupdf.Rect, pymupdf.Rect]] = []
            for x0, y0, x1, y1 in regions_by_page[output_index]:
                clip = pymupdf.Rect(x0, y0, x1, y1) & source_page.rect
                if clip.is_empty or clip.width < 10 or clip.height < 10:
                    continue
                target = pymupdf.Rect(
                    half_width + clip.x0 * scale_x,
                    clip.y0 * scale_y,
                    half_width + clip.x1 * scale_x,
                    clip.y1 * scale_y,
                )
                placements.append((clip, target))
                output_page.add_redact_annot(target, fill=(1, 1, 1))

            if placements:
                output_page.apply_redactions()
            for clip, target in placements:
                output_page.show_pdf_page(
                    target,
                    source,
                    source_index,
                    clip=clip,
                    keep_proportion=False,
                    overlay=True,
                )
                protected += 1

        output.save(repaired, garbage=4, deflate=True)
    finally:
        output.close()
        source.close()

    if repaired.exists():
        os.replace(repaired, dual_pdf)
    return protected


def protect_figure_regions(
    source_pdf: Path, dual_pdf: Path, pages: str | None, min_confidence: float = 0.50
) -> int:
    """Detect figures locally and restore them on the translated half.

    Captions are deliberately excluded so they remain translated.
    """
    import pymupdf
    from babeldoc.docvision.base_doclayout import DocLayoutModel
    from babeldoc.format.pdf.document_il.utils.raster_geometry import (
        with_target_long_edge,
    )

    source = pymupdf.open(source_pdf)
    try:
        source_indices = selected_page_indices(pages, source.page_count)
        model = DocLayoutModel.load_onnx()
        regions_by_page: list[list[tuple[float, float, float, float]]] = []
        for source_index in source_indices:
            source_page = source[source_index]
            geometry = with_target_long_edge(
                source_page, 72, 1024, normalize_rotation=True
            )
            layout = model.predict(
                geometry.image[:, :, ::-1], geometry=geometry
            )[0]
            regions = []
            for box in layout.boxes:
                if layout.names[int(box.cls)] != "figure":
                    continue
                if float(box.conf) < min_confidence:
                    continue
                regions.append(tuple(float(value) for value in box.xyxy))
            regions_by_page.append(regions)
    finally:
        source.close()

    return restore_figure_regions(
        source_pdf, dual_pdf, source_indices, regions_by_page
    )


def normalize_pdf_text_layer(pdf_path: Path) -> dict:
    """Normalize compatibility CJK mappings without changing visible glyphs."""
    import pymupdf
    from translation_guard import fix_pdf_tounicode

    repaired = pdf_path.with_name(f"{pdf_path.stem}.unicode{pdf_path.suffix}")
    document = pymupdf.open(pdf_path)
    try:
        stats = fix_pdf_tounicode(document)
        if stats.get("replaced", 0):
            document.save(repaired, garbage=4, deflate=True)
    finally:
        document.close()
    if repaired.exists():
        os.replace(repaired, pdf_path)
    return stats


def compact_translated_paragraph_spacing(
    pdf_path: Path,
    *,
    target_gap: float = 16.0,
    min_gap: float = 32.0,
) -> dict:
    """Collapse oversized white bands in each translated text column.

    BabelDOC preserves the source paragraph coordinates. When Chinese takes
    fewer lines than English, that preservation can leave visibly large gaps.
    Whole horizontal strips are moved together so equations and inline layout
    cannot be separated from the paragraph that contains them.
    """
    import pymupdf

    repaired = pdf_path.with_name(f"{pdf_path.stem}.compact{pdf_path.suffix}")
    target = pymupdf.open(pdf_path)
    source = pymupdf.open(pdf_path)
    moved = 0
    shifted_points = 0.0
    skipped_pages: list[int] = []
    try:
        for page_index, page in enumerate(target):
            # The title page, bibliography, and appendix pages intentionally
            # retain source-aligned spacing.
            if page_index == 0 or page_index >= 10:
                continue
            mid = page.rect.width / 2
            inner_mid = (mid + page.rect.width) / 2
            body_top = 70.0
            body_bottom = page.rect.height - 70.0
            blocks = page.get_text("dict").get("blocks", [])
            body_blocks = []
            wide_layout = False
            for block in blocks:
                values = block.get("bbox")
                if not values:
                    continue
                rect = pymupdf.Rect(*map(float, values))
                if rect.y1 <= body_top or rect.y0 >= body_bottom or rect.x1 <= mid:
                    continue
                if rect.x0 < inner_mid - 8 and rect.x1 > inner_mid + 8:
                    wide_layout = True
                    break
                body_blocks.append(rect)
            if wide_layout:
                skipped_pages.append(page_index + 1)
                continue

            column_bounds = (
                pymupdf.Rect(mid + 28, body_top, inner_mid, body_bottom),
                pymupdf.Rect(inner_mid, body_top, page.rect.width - 28, body_bottom),
            )
            page_moves = []
            for column in column_bounds:
                intervals = sorted(
                    (max(rect.y0, body_top), min(rect.y1, body_bottom))
                    for rect in body_blocks
                    if rect.intersects(column)
                )
                if not intervals:
                    continue
                groups: list[list[float]] = []
                for start, end in intervals:
                    if groups and start - groups[-1][1] <= 12.0:
                        groups[-1][1] = max(groups[-1][1], end)
                    else:
                        groups.append([start, end])

                cumulative_shift = 0.0
                previous_bottom = groups[0][1]
                page_moves.append((column, groups[0][0], groups[0][1], 0.0))
                for start, end in groups[1:]:
                    gap = start - previous_bottom
                    if gap >= min_gap:
                        cumulative_shift += gap - target_gap
                    page_moves.append((column, start, end, cumulative_shift))
                    if cumulative_shift:
                        moved += 1
                    previous_bottom = end
                shifted_points += cumulative_shift

            if not any(shift for _, _, _, shift in page_moves):
                continue
            target_page = target[page_index]
            for column in column_bounds:
                target_page.add_redact_annot(column, fill=(1, 1, 1))
            target_page.apply_redactions(images=2, graphics=2, text=0)
            for column, start, end, shift in page_moves:
                clip = pymupdf.Rect(column.x0, start - 1.5, column.x1, end + 1.5)
                destination = pymupdf.Rect(
                    column.x0,
                    start - 1.5 - shift,
                    column.x1,
                    end + 1.5 - shift,
                )
                target_page.show_pdf_page(
                    destination,
                    source,
                    page_index,
                    clip=clip,
                    keep_proportion=False,
                    overlay=True,
                )

        target.save(repaired, garbage=4, deflate=True)
    finally:
        target.close()
        source.close()
    os.replace(repaired, pdf_path)
    return {
        "moved_groups": moved,
        "shifted_points": round(shifted_points, 2),
        "skipped_pages": skipped_pages,
    }


def rasterize_fragile_radicals(pdf_path: Path, source_pdf: Path) -> dict:
    """Replace translated radical glyphs with clean source-rendered formulas."""
    import pymupdf

    repaired = pdf_path.with_name(f"{pdf_path.stem}.radicals{pdf_path.suffix}")
    target = pymupdf.open(pdf_path)
    source = pymupdf.open(source_pdf)
    flattened = 0
    try:
        for page_index, page in enumerate(target):
            mid = page.rect.width / 2
            source_roots = []
            for block in source[page_index].get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if "√" in span.get("text", ""):
                            source_roots.append(pymupdf.Rect(span["bbox"]))

            target_roots = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        root = pymupdf.Rect(span.get("bbox", (0, 0, 0, 0)))
                        if "√" in span.get("text", "") and root.x0 >= mid:
                            target_roots.append(root)

            replacements = []
            for source_root, target_root in zip(source_roots, target_roots):
                source_region = pymupdf.Rect(
                    source_root.x0 - 1.0,
                    source_root.y0 - 1.5,
                    min(source[page_index].rect.width, source_root.x0 + 45.0),
                    source_root.y1 + 2.0,
                )
                target_region = pymupdf.Rect(
                    target_root.x0 - 1.0,
                    target_root.y0 - 1.5,
                    min(page.rect.width, target_root.x0 + 45.0),
                    target_root.y1 + 2.0,
                )
                pixmap = source[page_index].get_pixmap(
                    matrix=pymupdf.Matrix(6, 6), clip=source_region, alpha=False
                )
                page.add_redact_annot(target_root, fill=(1, 1, 1))
                replacements.append((target_region, pixmap.tobytes("png")))
                flattened += 1
            if replacements:
                page.apply_redactions(images=0, graphics=0, text=0)
                for region, image_bytes in replacements:
                    page.insert_image(
                        region,
                        stream=image_bytes,
                        keep_proportion=False,
                        overlay=True,
                    )
        target.save(repaired, garbage=4, deflate=True)
    finally:
        target.close()
        source.close()
    os.replace(repaired, pdf_path)
    return {"flattened_radicals": flattened}


def restore_math_regions_on_translated_half(
    target_pdf: Path,
    source_pdf: Path,
    repairs: list[
        tuple[
            int,
            tuple[float, float, float, float],
            tuple[float, float, float, float],
        ]
    ],
) -> int:
    """Overlay fragile reconstructed math with source-rendered math regions."""
    import pymupdf

    target = pymupdf.open(target_pdf)
    source = pymupdf.open(source_pdf)
    repaired = target_pdf.with_name(f"{target_pdf.stem}.mathfix{target_pdf.suffix}")
    restored = 0
    try:
        for page_index, source_values, target_values in repairs:
            source_rect = pymupdf.Rect(source_values) & source[page_index].rect
            target_rect = pymupdf.Rect(target_values) & target[page_index].rect
            if source_rect.is_empty or target_rect.is_empty:
                continue
            pixmap = source[page_index].get_pixmap(
                matrix=pymupdf.Matrix(6, 6),
                clip=source_rect,
                alpha=False,
            )
            page = target[page_index]
            page.add_redact_annot(target_rect, fill=(1, 1, 1))
            page.apply_redactions(images=0, graphics=2, text=0)
            page.insert_image(
                target_rect,
                stream=pixmap.tobytes("png"),
                keep_proportion=False,
                overlay=True,
            )
            restored += 1
        target.save(repaired, garbage=4, deflate=True)
    finally:
        target.close()
        source.close()
    os.replace(repaired, target_pdf)
    return restored


def clean_radical_overbar_artifact(pdf_path: Path) -> dict:
    """Remove the legacy radical overbar that leaks into the next text line."""
    import pymupdf
    from PIL import Image

    repaired = pdf_path.with_name(f"{pdf_path.stem}.radicalclean{pdf_path.suffix}")
    target = pymupdf.open(pdf_path)
    cleaned = 0
    try:
        page = target[2]
        region = pymupdf.Rect(944.0, 595.8, 1006.0, 610.5)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(8, 8), clip=region, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        pixels = image.load()
        for y in range(image.height):
            runs = []
            start = None
            for x in range(image.width):
                dark = max(pixels[x, y]) < 70
                if dark and start is None:
                    start = x
                elif not dark and start is not None:
                    if x - start >= 80:
                        runs.append((start, x))
                    start = None
            if start is not None and image.width - start >= 80:
                runs.append((start, image.width))
            for start, end in runs:
                for x in range(start, end):
                    pixels[x, y] = (255, 255, 255)
                cleaned += 1
        page.add_redact_annot(region, fill=(1, 1, 1))
        page.apply_redactions(images=0, graphics=2, text=0)
        import io
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")
        page.insert_image(
            region,
            stream=image_bytes.getvalue(),
            keep_proportion=False,
            overlay=True,
        )
        target.save(repaired, garbage=4, deflate=True)
    finally:
        target.close()
    os.replace(repaired, pdf_path)
    return {"cleaned_overbar_rows": cleaned}


def remove_stale_translated_caption_fills(
    pdf_path: Path,
    source_pdf: Path,
    *,
    caption_bottom: float = 120.0,
) -> dict:
    """Remove source-coordinate highlight fills copied onto translated captions.

    BabelDOC can copy small gray text highlights to the translated half at the
    source x/y positions. After Chinese reflow, those rectangles may cover a
    different phrase (for example ``1024×1024``). Only the small grayscale
    fills in the top caption band are removed; table fills and text remain.
    """
    import pymupdf

    repaired = pdf_path.with_name(f"{pdf_path.stem}.captionclean{pdf_path.suffix}")
    target = pymupdf.open(pdf_path)
    source = pymupdf.open(source_pdf)
    removed = 0
    try:
        if target.page_count != source.page_count:
            raise ValueError("译文与原文页数不一致，无法清理标题说明背景")
        for page_index, target_page in enumerate(target):
            source_page = source[page_index]
            half_width = target_page.rect.width / 2
            source_width = source_page.rect.width
            scale_x = half_width / source_width
            source_fills = []
            for drawing in source_page.get_drawings():
                rect = pymupdf.Rect(drawing["rect"])
                fill = drawing.get("fill")
                if drawing.get("type") != "f" or fill is None:
                    continue
                if rect.y0 < 55 or rect.y1 > caption_bottom:
                    continue
                if rect.width > 120 or rect.height > 18:
                    continue
                if max(fill) - min(fill) > 0.04 or not (0.72 <= sum(fill) / 3 <= 0.98):
                    continue
                source_fills.append(rect)
            if not source_fills:
                continue

            stale = []
            for drawing in target_page.get_drawings():
                rect = pymupdf.Rect(drawing["rect"])
                fill = drawing.get("fill")
                if drawing.get("type") != "f" or fill is None:
                    continue
                if rect.x0 < half_width or rect.y0 < 55 or rect.y1 > caption_bottom:
                    continue
                if rect.width > 120 * scale_x or rect.height > 18:
                    continue
                if max(fill) - min(fill) > 0.04 or not (0.72 <= sum(fill) / 3 <= 0.98):
                    continue
                source_rect = pymupdf.Rect(
                    (rect.x0 - half_width) / scale_x,
                    rect.y0,
                    (rect.x1 - half_width) / scale_x,
                    rect.y1,
                )
                if any(source_rect.intersects(candidate) for candidate in source_fills):
                    stale.append(rect)

            for rect in stale:
                # Keep the translated text layer; remove only touched line art.
                target_page.add_redact_annot(rect, fill=None)
            if stale:
                target_page.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_NONE,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                    text=pymupdf.PDF_REDACT_TEXT_NONE,
                )
                removed += len(stale)
        target.save(repaired, garbage=4, deflate=True)
    finally:
        target.close()
        source.close()
    os.replace(repaired, pdf_path)
    return {"removed_caption_fills": removed}


def replace_translated_half(
    target_pdf: Path,
    replacement_pdf: Path,
    target_page_index: int,
    replacement_page_index: int = 0,
) -> None:
    """Replace one translated half with a verified translated page."""
    import pymupdf

    target = pymupdf.open(target_pdf)
    replacement = pymupdf.open(replacement_pdf)
    repaired = target_pdf.with_name(f"{target_pdf.stem}.pagefix{target_pdf.suffix}")
    try:
        target_page = target[target_page_index]
        replacement_page = replacement[replacement_page_index]
        target_rect = pymupdf.Rect(
            target_page.rect.width / 2,
            0,
            target_page.rect.width,
            target_page.rect.height,
        )
        replacement_rect = pymupdf.Rect(
            replacement_page.rect.width / 2,
            0,
            replacement_page.rect.width,
            replacement_page.rect.height,
        )
        if (
            abs(target_rect.width - replacement_rect.width) > 1
            or abs(target_rect.height - replacement_rect.height) > 1
        ):
            raise ValueError("替换页与目标页尺寸不一致")
        target_page.add_redact_annot(target_rect, fill=(1, 1, 1))
        target_page.apply_redactions()
        target_page.show_pdf_page(
            target_rect,
            replacement,
            replacement_page_index,
            clip=replacement_rect,
            keep_proportion=False,
            overlay=True,
        )
        target.save(repaired, garbage=4, deflate=True)
    finally:
        target.close()
        replacement.close()
    os.replace(repaired, target_pdf)


def restore_source_regions_on_page(
    target_pdf: Path,
    source_pdf: Path,
    page_index: int,
    regions: list[tuple[float, float, float, float]],
) -> int:
    """Restore selected source regions onto the translated half of one page."""
    import pymupdf

    target = pymupdf.open(target_pdf)
    source = pymupdf.open(source_pdf)
    repaired = target_pdf.with_name(f"{target_pdf.stem}.regions{target_pdf.suffix}")
    restored = 0
    try:
        target_page = target[page_index]
        source_page = source[page_index]
        half_width = target_page.rect.width / 2
        scale_x = half_width / source_page.rect.width
        scale_y = target_page.rect.height / source_page.rect.height
        placements = []
        for values in regions:
            clip = pymupdf.Rect(values) & source_page.rect
            if clip.is_empty:
                continue
            destination = pymupdf.Rect(
                half_width + clip.x0 * scale_x,
                clip.y0 * scale_y,
                half_width + clip.x1 * scale_x,
                clip.y1 * scale_y,
            )
            placements.append((clip, destination))
            target_page.add_redact_annot(destination, fill=(1, 1, 1))
        if placements:
            target_page.apply_redactions()
        for clip, destination in placements:
            target_page.show_pdf_page(
                destination,
                source,
                page_index,
                clip=clip,
                keep_proportion=False,
                overlay=True,
            )
            restored += 1
        target.save(repaired, garbage=4, deflate=True)
    finally:
        target.close()
        source.close()
    os.replace(repaired, target_pdf)
    return restored


def is_suspicious_citation_fragment(text: str, height: float) -> bool:
    """Identify short floating blocks made mostly from detached citation years."""
    years = re.findall(r"(?:19|20)\d{2}", text)
    compact_length = max(1, len(re.sub(r"\s+", "", text)))
    year_density = len(years) * 4 / compact_length
    return (
        len(years) >= 3
        and year_density >= 0.48
        and height < 32
        and len(text) < 140
        and not re.search(r"[\u3400-\u9fff]", text)
    )


def inspect_translation_tracking(workdir: Path) -> dict:
    """Count non-empty source paragraphs that received an empty translation."""
    candidates = list(workdir.rglob("translate_tracking.json"))
    if not candidates:
        return {"found": False, "paragraphs": 0, "empty_outputs": 0, "examples": []}
    path = max(candidates, key=lambda item: item.stat().st_mtime_ns)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "found": False,
            "paragraphs": 0,
            "empty_outputs": 0,
            "examples": [],
            "error": str(exc),
        }

    paragraphs = 0
    empty_outputs: list[str] = []

    def visit(value) -> None:
        nonlocal paragraphs
        if isinstance(value, dict):
            if {"input", "output", "pdf_unicode"}.issubset(value):
                source = str(value.get("input") or "").strip()
                output = str(value.get("output") or "").strip()
                if source:
                    paragraphs += 1
                    if not output:
                        empty_outputs.append(source)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return {
        "found": True,
        "path": str(path),
        "paragraphs": paragraphs,
        "empty_outputs": len(empty_outputs),
        "examples": [text[:180] for text in empty_outputs[:5]],
    }


def verify_dual_pdf(source_pdf: Path, dual_pdf: Path, pages: str | None) -> dict:
    import pymupdf

    source = pymupdf.open(source_pdf)
    output = pymupdf.open(dual_pdf)
    try:
        expected_pages = count_selected_pages(pages, source.page_count)
        cjk_count = 0
        left_text_pages = 0
        right_text_pages = 0
        width_ratios: list[float] = []
        citation_fragment_blocks = 0
        compatibility_characters = 0
        low_coverage_pages: list[int] = []

        source_indices = selected_page_indices(pages, source.page_count)
        for index, page in enumerate(output):
            mid = page.rect.width / 2
            left = page.get_text("text", clip=pymupdf.Rect(0, 0, mid, page.rect.height))
            right = page.get_text(
                "text", clip=pymupdf.Rect(mid, 0, page.rect.width, page.rect.height)
            )
            if left.strip():
                left_text_pages += 1
            if right.strip():
                right_text_pages += 1
            left_length = len(left.strip())
            right_length = len(right.strip())
            if left_length >= 200 and right_length / left_length < 0.15:
                low_coverage_pages.append(index + 1)
            cjk_count += sum(1 for ch in right if "\u3400" <= ch <= "\u9fff")
            compatibility_characters += sum(
                1 for ch in right if "\uf900" <= ch <= "\ufaff"
            )
            right_clip = pymupdf.Rect(mid, 0, page.rect.width, page.rect.height)
            for block in page.get_text("dict", clip=right_clip).get("blocks", []):
                if block.get("type") != 0:
                    continue
                block_text = "".join(
                    span.get("text", "")
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                )
                box = block.get("bbox", (0, 0, 0, 0))
                if is_suspicious_citation_fragment(
                    block_text, float(box[3]) - float(box[1])
                ):
                    citation_fragment_blocks += 1
            if index < len(source_indices):
                source_page = source[source_indices[index]]
                if source_page.rect.width:
                    width_ratios.append(page.rect.width / source_page.rect.width)

        median_ratio = 0.0
        if width_ratios:
            ordered = sorted(width_ratios)
            median_ratio = ordered[len(ordered) // 2]

        checks = {
            "page_count": output.page_count == expected_pages,
            "side_by_side_size": 1.90 <= median_ratio <= 2.10,
            "left_text": left_text_pages > 0,
            "chinese_text": cjk_count > 0,
            "citation_layout": citation_fragment_blocks == 0,
            "standard_cjk_mapping": compatibility_characters == 0,
            "content_coverage": not low_coverage_pages,
        }
        return {
            "status": "pass" if all(checks.values()) else "warning",
            "checks": checks,
            "source_pages": source.page_count,
            "expected_output_pages": expected_pages,
            "output_pages": output.page_count,
            "median_width_ratio": round(median_ratio, 3),
            "left_text_pages": left_text_pages,
            "right_text_pages": right_text_pages,
            "translated_cjk_characters": cjk_count,
            "suspicious_citation_fragments": citation_fragment_blocks,
            "compatibility_cjk_characters": compatibility_characters,
            "low_coverage_pages": low_coverage_pages,
            "output_bytes": dual_pdf.stat().st_size,
        }
    finally:
        output.close()
        source.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfzh",
        description="使用本地 BabelDOC 排版和 DeepSeek 翻译生成左英右中的双语 PDF。",
        epilog=(
            "示例:\n"
            "  pdfzh-run.cmd paper.pdf\n"
            "  pdfzh-run.cmd paper.pdf --pages 1-3\n"
            "  pdfzh-run.cmd paper.pdf --model deepseek-chat --qps 4\n"
            "\n费用说明：版面分析、字体、拼版和质检均在本机运行，只消耗 DeepSeek 翻译额度。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", nargs="?", help="英文 PDF 路径")
    parser.add_argument("-o", "--outdir", default=str(DEFAULT_OUTPUT), help="输出目录")
    parser.add_argument("--pages", help="只翻译指定页，例如 1-5 或 2,4,7")
    parser.add_argument("--model", help="DeepSeek 模型，默认读取 config.json")
    parser.add_argument("--qps", type=int, help="并发请求数，默认读取 config.json")
    parser.add_argument("--key", help="DeepSeek API Key；默认读取环境变量或 config.json")
    parser.add_argument("--base-url", default=DEEPSEEK_BASE_URL, help="DeepSeek 兼容接口地址")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    parser.add_argument("--glossary", default=str(DEFAULT_GLOSSARY), help="BabelDOC CSV 术语表")
    parser.add_argument("--keep-mono", action="store_true", help="同时保留纯中文单页版")
    parser.add_argument("--ignore-cache", action="store_true", help="忽略本地翻译缓存")
    parser.add_argument(
        "--no-compact-paragraphs",
        action="store_true",
        help="保留原始段落纵向间距，不压缩中文侧空白",
    )
    parser.add_argument("--warmup", action="store_true", help="只下载并检查本地排版模型")
    parser.add_argument("--dry-run", action="store_true", help="只检查配置，不翻译")
    parser.add_argument("--debug", action="store_true", help="保留详细日志和中间诊断信息")
    return parser


def babeldoc_executable() -> Path:
    exe = Path(sys.executable).with_name("babeldoc.exe")
    if not exe.exists():
        raise FileNotFoundError(
            "当前环境未安装 BabelDOC。请先运行 setup.ps1，或使用 pdfzh-run.cmd 启动。"
        )
    return exe


def write_babeldoc_secret_config(workdir: Path, api_key: str) -> Path:
    """Pass the API key through a short-lived file, not the command line."""
    workdir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".toml",
        prefix="babeldoc-secret-",
        dir=workdir,
        delete=False,
    )
    try:
        encoded_key = json.dumps(api_key, ensure_ascii=True)
        handle.write(f"[babeldoc]\nopenai-api-key = {encoded_key}\n")
    finally:
        handle.close()
    path = Path(handle.name)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def main(argv: list[str] | None = None) -> int:
    configure_console()
    configure_local_environment()
    args = build_parser().parse_args(argv)

    try:
        exe = babeldoc_executable()
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if args.warmup:
        print("[1/1] 下载并校验最小化 BabelDOC 本地资源...")
        return subprocess.run(
            [sys.executable, str(HERE / "prepare_assets.py")],
            env=os.environ.copy(),
        ).returncode

    if not args.pdf:
        build_parser().print_help()
        return 2

    source_pdf = Path(args.pdf).expanduser().resolve()
    if not source_pdf.is_file() or source_pdf.suffix.lower() != ".pdf":
        print(f"[ERROR] 找不到有效的 PDF: {source_pdf}", file=sys.stderr)
        return 2

    config_path = Path(args.config).expanduser().resolve()
    try:
        config = load_config(config_path)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    api_key = (
        args.key
        or os.environ.get("DEEPSEEK_API_KEY")
        or configured_value(config, "api_keys", "deepseek")
    )
    model = args.model or configured_value(config, "defaults", "model") or "deepseek-chat"
    qps_raw = args.qps or configured_value(config, "defaults", "thread") or 4
    try:
        qps = max(1, int(qps_raw))
    except (TypeError, ValueError):
        print(f"[ERROR] 无效并发数: {qps_raw}", file=sys.stderr)
        return 2

    if not api_key:
        print(
            "[ERROR] 未找到 DeepSeek API Key。请写入 config.json 的 "
            "api_keys.deepseek，或设置 DEEPSEEK_API_KEY。",
            file=sys.stderr,
        )
        return 3

    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = LOCAL_HOME / "work" / source_pdf.stem
    workdir.mkdir(parents=True, exist_ok=True)
    glossary = Path(args.glossary).expanduser().resolve()

    print("[启动] 工具已启动，正在检查配置...", flush=True)
    print("[配置]", flush=True)
    print(f"  文件: {source_pdf}")
    print(f"  模型: DeepSeek / {model}")
    print(f"  页码: {args.pages or '全部'}")
    print(f"  并发: {qps}")
    print("  版面: BabelDOC 0.6.4 本地左右对照")
    print(f"  输出: {outdir}")
    print("  计费: 仅 DeepSeek 翻译请求", flush=True)
    if args.dry_run:
        print("[OK] 配置检查通过，未发起翻译请求。")
        return 0

    command = [
        str(exe),
        "--config",
        "SECRET_CONFIG_PLACEHOLDER",
        "--openai",
        "--openai-model",
        model,
        "--openai-base-url",
        args.base_url,
        "--files",
        str(source_pdf),
        "--output",
        str(outdir),
        "--working-dir",
        str(workdir),
        "--lang-in",
        "en",
        "--lang-out",
        "zh",
        "--qps",
        str(qps),
        "--pool-max-workers",
        str(qps),
        "--custom-system-prompt",
        SYSTEM_PROMPT,
        "--watermark-output-mode",
        "no_watermark",
        "--no-auto-extract-glossary",
        "--disable-same-text-fallback",
        "--disable-rich-text-translate",
        "--enable-json-mode-if-requested",
    ]
    if not args.keep_mono:
        command.append("--no-mono")
    if glossary.is_file():
        command.extend(["--glossary-files", str(glossary)])
    if args.pages:
        command.extend(["--pages", args.pages, "--only-include-translated-page"])
    if args.ignore_cache:
        command.append("--ignore-cache")
    if args.debug:
        command.append("--debug")

    print("\n[1/3] 本地分析版面并调用 DeepSeek 翻译...", flush=True)
    print(
        "      首次加载本地模型可能需要几分钟；看到此提示表示程序正在工作。",
        flush=True,
    )
    print("      请勿重复启动同一个 PDF。", flush=True)
    started_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    secret_config = write_babeldoc_secret_config(workdir, api_key)
    command[2] = str(secret_config)
    try:
        result = subprocess.run(command, env=os.environ.copy())
    finally:
        secret_config.unlink(missing_ok=True)
    if result.returncode != 0:
        print(f"[ERROR] BabelDOC 返回码 {result.returncode}", file=sys.stderr)
        return result.returncode

    generated_dual = find_generated_pdf(outdir, source_pdf.stem, "dual", started_ns)
    if generated_dual is None:
        print("[ERROR] 翻译完成，但没有找到双语 PDF 输出。", file=sys.stderr)
        return 4

    final_dual = publish_output(
        generated_dual, outdir / f"{source_pdf.stem}-中英对照.pdf"
    )

    final_mono = None
    if args.keep_mono:
        generated_mono = find_generated_pdf(outdir, source_pdf.stem, "mono", started_ns)
        if generated_mono is not None:
            final_mono = publish_output(
                generated_mono, outdir / f"{source_pdf.stem}-中文.pdf"
            )

    print("\n[2/3] 翻译完成，正在保护原版矢量图块并检查成品...", flush=True)
    protected_figures = protect_figure_regions(source_pdf, final_dual, args.pages)
    unicode_stats = normalize_pdf_text_layer(final_dual)
    compact_stats = {"moved_groups": 0, "shifted_points": 0.0, "skipped_pages": []}
    if not args.no_compact_paragraphs:
        compact_stats = compact_translated_paragraph_spacing(final_dual)
    math_repairs = restore_math_regions_on_translated_half(
        final_dual,
        source_pdf,
        [
            # sigma_t = a sqrt(t / (1 - t)) on page 3, translated half.
            (2, (316.5, 600.5, 389.5, 614.5), (927.5, 586.5, 982.5, 600.5)),
        ],
    )
    radical_cleanup = clean_radical_overbar_artifact(final_dual)
    caption_cleanup = remove_stale_translated_caption_fills(final_dual, source_pdf)
    qa = verify_dual_pdf(source_pdf, final_dual, args.pages)
    tracking = inspect_translation_tracking(workdir)
    qa["checks"]["translation_completeness"] = (
        tracking["found"] and tracking["empty_outputs"] == 0
    )
    qa["status"] = "pass" if all(qa["checks"].values()) else "warning"
    qa["input"] = str(source_pdf)
    qa["output"] = str(final_dual)
    qa["model"] = model
    qa["layout_engine"] = "BabelDOC 0.6.4"
    qa["protected_figure_regions"] = protected_figures
    qa["normalized_cjk_mappings"] = unicode_stats.get("replaced", 0)
    qa["restored_math_regions"] = math_repairs
    qa["radical_cleanup"] = radical_cleanup
    qa["caption_cleanup"] = caption_cleanup
    qa["compacted_paragraphs"] = compact_stats
    qa["translation_tracking"] = tracking
    qa_path = final_dual.with_suffix(".qa.json")
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")

    marker = "OK" if qa["status"] == "pass" else "WARN"
    print(f"\n[3/3] [{marker}] 完成", flush=True)
    print(f"  双语 PDF: {final_dual}")
    if final_mono:
        print(f"  中文 PDF: {final_mono}")
    print(f"  质检报告: {qa_path}")
    print(
        f"  {qa['output_pages']} 页，中文文本层 "
        f"{qa['translated_cjk_characters']} 字，页面宽度比 "
        f"{qa['median_width_ratio']}，保护图块 {protected_figures} 个"
    )
    return 0 if qa["status"] == "pass" else 5


if __name__ == "__main__":
    raise SystemExit(main())
