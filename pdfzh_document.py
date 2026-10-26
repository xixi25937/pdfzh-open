"""Local document adapters and a versioned, geometry-independent translation plan.

No document names, page numbers, paid OCR, or translation calls belong here.
The source PDF is never modified. Uncertain recognition is recorded, not hidden.
"""
from __future__ import annotations

import hashlib
import concurrent.futures
import json
from pathlib import Path
import re
import unicodedata

import pymupdf as fitz

import pdfzh_latex as translation
import pdfzh_nosource as native
import pdfzh_styles as styles

VERSION = 1
OCR_VERSION = 'rapidocr-onnxruntime-1.4.4-2400-v1'
MATH_FONT = re.compile(r'cmmi|cmsy|cmex|math|symbol|msam|msbm', re.I)


def invalid_text(text):
    return any(unicodedata.category(c) in {'Cc', 'Co'} or c == '\ufffd' for c in text)


def area_overlap(a, b):
    a, b = fitz.Rect(a), fitz.Rect(b)
    return (a & b).get_area() / max(1, min(a.get_area(), b.get_area()))


def reading_order(items):
    """Recursive whitespace cuts: full-width headings, then columns, then rows."""
    if len(items) < 2:
        return items
    gaps = []
    for axis in (0, 1):
        intervals = sorted((x['bbox'][axis], x['bbox'][axis + 2]) for x in items)
        end = intervals[0][1]
        for start, stop in intervals[1:]:
            gap = start - end
            if gap > (10 if axis == 0 else 3):
                gaps.append((gap * (2 if axis == 0 else 1), axis, (start + end) / 2))
            end = max(end, stop)
    if gaps:
        _, axis, cut = max(gaps)
        before = [x for x in items if x['bbox'][axis + 2] <= cut]
        after = [x for x in items if x['bbox'][axis] >= cut]
        if before and after and len(before) + len(after) == len(items):
            return reading_order(before) + reading_order(after)
    return sorted(items, key=lambda x: (round(x['bbox'][1] / 3), x['bbox'][0]))


class LocalOCR:
    def __init__(self):
        self.engine = None

    def recognize(self, page, cache_file, source_sha):
        key = f'{source_sha}:{page.number}:{OCR_VERSION}'
        if cache_file.is_file():
            cached = json.loads(cache_file.read_text(encoding='utf8'))
            if cached.get('key') == key:
                return cached['lines']
        if self.engine is None:
            from rapidocr_onnxruntime import RapidOCR
            print('[OCR] 加载本地识别模型（CPU；不调用付费接口）…', flush=True)
            self.engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1,
                                  det_limit_side_len=1600, det_limit_type='max',
                                  text_score=0.3)
        scale = min(3, 2400 / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.engine, pix.tobytes('png'))
            while True:
                try:
                    result, _ = future.result(timeout=15)
                    break
                except concurrent.futures.TimeoutError:
                    print(f'[OCR] 第 {page.number+1} 页仍在本地识别，完成后自动缓存…', flush=True)
        sx, sy = page.rect.width / pix.width, page.rect.height / pix.height
        lines = []
        for polygon, text, score in result or []:
            xs, ys = zip(*polygon)
            lines.append({'bbox': [min(xs)*sx, min(ys)*sy, max(xs)*sx, max(ys)*sy],
                          'text': text, 'confidence': float(score)})
        translation.write_json(cache_file, {'key': key, 'lines': lines})
        return lines


def crop_asset(page, rect, directory):
    rect = fitz.Rect(rect)
    rect.normalize()
    rect = (rect + (-1.5, -1.5, 1.5, 1.5)) & page.rect
    if rect.is_empty:
        raise ValueError('原文区域坐标为空')
    identity = f'{page.number}:{tuple(round(v, 3) for v in rect)}'
    name = hashlib.sha256(identity.encode()).hexdigest()[:20] + '.png'
    target = directory / name
    if not target.exists():
        page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False).save(target)
    return {'file': name, 'width': rect.width, 'height': rect.height}


def is_math(span):
    text = span['text'].strip()
    return bool(text and (MATH_FONT.search(span.get('font', '')) or
                span.get('flags', 0) & 1 or
                any(0x1D400 <= ord(c) <= 0x1D7FF for c in text) or
                re.search(r'[∑∫√∏∂∇≈≠≤≥∞]', text)))


def split_line(line):
    """PDF text streams sometimes put two distant columns on one physical line."""
    groups = []
    for span in line['spans']:
        if not span['text'].strip():
            # Some producers encode the entire column gutter as a whitespace
            # span. It must not connect the columns or inflate the text bbox.
            if groups and span['bbox'][2]-span['bbox'][0] < span.get('size',10)*2:
                groups[-1].append(span)
            continue
        if not groups or span['bbox'][0]-groups[-1][-1]['bbox'][2] > max(16, span.get('size', 10)*2):
            groups.append([span])
        else:
            groups[-1].append(span)
    result = []
    for spans in groups:
        rect = fitz.Rect(spans[0]['bbox'])
        for span in spans[1:]:
            rect |= fitz.Rect(span['bbox'])
        result.append({**line, 'spans': spans, 'bbox': list(rect)})
    return result


def paragraph_groups(lines, regions):
    """Semantic layout boundaries override unreliable native PDF block grouping."""
    buckets = {}
    for line in lines:
        rect = fitz.Rect(line['bbox']); center = (rect.tl + rect.br)/2
        matches = [(i, r) for i,r in enumerate(regions)
                   if r['confidence'] >= .25 and center in fitz.Rect(r['bbox'])]
        selected = min(matches, key=lambda pair: fitz.Rect(pair[1]['bbox']).get_area()) if matches else None
        key = selected[0] if selected else -1
        buckets.setdefault(key, []).append(line)
    result = []
    for key, group in buckets.items():
        group = reading_order(group)
        kind = regions[key]['kind'] if key >= 0 else ''
        lengths = [len(native.body_text([l])) for l in group]
        # Keep bullets and table/figure labels independent so they can reuse
        # translations and cannot merge across unrelated cells or columns.
        separate = kind in {'table', 'figure', 'isolate_formula'} or (
            kind!='title' and len(group)>1 and sum(lengths)/len(lengths)<42)
        separate = separate or (key == -1 and len(buckets)>1)
        if separate:
            result.extend([[line] for line in group])
        else:
            result.append(group)
    return result


def make_text_item(page, lines, directory, kind='text'):
    """Reuse the guarded style protocol; inline math becomes an immutable image."""
    original = native.body_text(lines)
    spans = [s for l in lines for s in l['spans'] if s['text'].strip()]
    rect = fitz.Rect(lines[0]['bbox'])
    for line in lines[1:]:
        rect |= fitz.Rect(line['bbox'])
    dominant = max(spans, key=lambda s: len(s['text']))
    item = {'kind': kind, 'bbox': list(rect), 'source': original, 'origin': 'native',
            'style': styles.style(dominant), 'font': dominant.get('size', 10)}
    if not any(is_math(s) for s in spans):
        item['segment'] = styles.rich_segment(lines, native.segment(original), native.segment)
        return item
    protected, events, parts, images = [], {}, [], {}

    def token(raw, event=None, asset=None):
        index = len(protected)
        protected.append(raw)
        if event is not None:
            events[str(index)] = event
        if asset is not None:
            images[str(index)] = asset
        return f'@@P{index}@@'

    for line_index, line in enumerate(lines):
        if line_index:
            parts.append(' ')
        line_spans = line['spans']
        i = 0
        while i < len(line_spans):
            span = line_spans[i]
            if is_math(span):
                group = [span]
                i += 1
                # Include adjacent operators / subscripts in the same crop.
                while i < len(line_spans) and (is_math(line_spans[i]) or
                      (not native.prose(line_spans[i]['text']) and
                       line_spans[i]['bbox'][0] - group[-1]['bbox'][2] < 4)):
                    group.append(line_spans[i]); i += 1
                r = fitz.Rect(group[0]['bbox'])
                for s in group[1:]:
                    r |= fitz.Rect(s['bbox'])
                parts.append(token(''.join(s['text'] for s in group),
                                   asset=crop_asset(page, r, directory)))
            else:
                parts.append(token('{', {'kind': 'open', 'style': styles.style(span)}))
                sub = native.segment(span['text'])
                offset = len(protected)
                parts.append(re.sub(r'@@P(\d+)@@', lambda m: f'@@P{offset+int(m[1])}@@', sub['text']))
                protected.extend(sub['protected'])
                parts.append(token('}', {'kind': 'close'}))
                i += 1
    item['segment'] = {'text': ''.join(parts), 'protected': protected,
                       'style_events': events, 'original': original, 'leading': '', 'trailing': '',
                       'protection_format': 'literal'}
    item['inline_images'] = images
    return item


def extract_page(page, regions, directory, ocr, ocr_cache, source_sha):
    items, issues, native_lines, bad_boxes = [], [], [], []
    visual_regions = []
    for region in regions:
        if region['kind'] in {'figure', 'table', 'isolate_formula'} and region['confidence'] >= .3:
            rect = fitz.Rect(region['bbox']) & page.rect
            if rect.is_empty or any(area_overlap(rect, x['bbox']) > .85 for x in visual_regions):
                continue
            visual_regions.append({'kind': region['kind'], 'bbox': list(rect)})
    blocks = page.get_text('dict')['blocks']
    # Raster images can be missed by the layout model. A full-page scan is not
    # itself a figure: its original already appears on the left.
    for block in blocks:
        if block['type'] == 1:
            # Flipped bitmaps report an inverted bbox; normalize before any
            # area/overlap math, which treats empty rectangles as zero.
            box = fitz.Rect(block['bbox'])
            box.normalize()
            if box.get_area() >= page.rect.get_area() * .8:
                continue
            if not any(area_overlap(box, x['bbox']) > .5 for x in visual_regions):
                visual_regions.append({'kind': 'figure', 'bbox': list(box)})
    for region in visual_regions:
        items.append({**region, 'asset': crop_asset(page, region['bbox'], directory), 'origin': 'visual'})
    formula_boxes = [fitz.Rect(v['bbox']) for v in visual_regions if v['kind'] == 'isolate_formula']

    for block in blocks:
        if block['type'] != 0:
            continue
        clean = []
        for raw_line in block['lines']:
            # A control character can be the only glyph on a line in a broken
            # font mapping. Detect it before whitespace trimming / splitting.
            raw = ''.join(s['text'] for s in raw_line['spans'])
            if invalid_text(raw):
                bad_boxes.append(raw_line['bbox'])
                continue
            for line in split_line(raw_line):
                if any(s['text'].strip() for s in line['spans']):
                    native_lines.append(line)
                    clean.append(line)
        if not clean:
            continue
        groups = paragraph_groups(clean, regions)
        for lines in groups:
            source = native.body_text(lines)
            if not source:
                continue
            bbox = fitz.Rect(lines[0]['bbox'])
            for line in lines[1:]:
                bbox |= fitz.Rect(line['bbox'])
            if not native.prose(source):
                # Pure values/math are images; preserve signs, fractions, fonts.
                if not any(area_overlap(bbox, v['bbox']) > .8 for v in visual_regions):
                    items.append({'kind': 'notation', 'bbox': list(bbox), 'origin': 'native',
                                  'asset': crop_asset(page, bbox, directory)})
                continue
            items.append(make_text_item(page, lines, directory))

    scan = sum(len(native.body_text([l])) for l in native_lines) < 20
    needs_ocr = scan or bool(bad_boxes) or any(b['type'] == 1 for b in blocks) or bool(visual_regions)
    ocr_count = 0
    if needs_ocr:
        print(f'[OCR] 第 {page.number+1} 页：检查扫描文字、图中标注和异常字体…', flush=True)
        recognized = ocr.recognize(page, ocr_cache, source_sha)
        recovered_boxes = []
        for line in recognized:
            rect = line['bbox']
            # Native text is authoritative where it exists. OCR is an adapter
            # for uncovered regions, not a second copy of every native sentence.
            if any(area_overlap(rect, n['bbox']) > .45 for n in native_lines):
                continue
            text = line['text'].strip()
            center = (fitz.Rect(rect).tl + fitz.Rect(rect).br) / 2
            if any(center in box for box in formula_boxes) and not any(area_overlap(rect, b) > .5 for b in bad_boxes):
                # OCR of mathematical notation is deliberately not treated as
                # uncertain English. Its source crop is already preserved.
                continue
            if not native.prose(text) and not invalid_text(text):
                # Numbers and formula glyphs need no semantic OCR decision:
                # keep the source pixels, never retype an uncertain value.
                if text and not any(area_overlap(rect, v['bbox']) > .8 for v in visual_regions):
                    items.append({'kind': 'notation', 'bbox': rect, 'origin': 'ocr',
                                  'asset': crop_asset(page, rect, directory)})
                recovered_boxes.append(rect)
                continue
            if line['confidence'] < .88 or invalid_text(text):
                issues.append({'kind': 'uncertain_ocr', **line})
                items.append({'kind': 'recognition_warning', 'bbox': rect, 'origin': 'ocr',
                              'asset': crop_asset(page, rect, directory)})
                continue
            items.append({'kind': 'text', 'bbox': rect, 'origin': 'ocr', 'source': text,
                          'confidence': line['confidence'], 'segment': native.segment(text),
                          'style': {'color': 0, 'bold': False, 'italic': False}, 'font': 10})
            recovered_boxes.append(rect)
            ocr_count += 1
        for rect in bad_boxes:
            if not any(area_overlap(rect, b) > .5 for b in recovered_boxes):
                issues.append({'kind': 'unresolved_font', 'bbox': rect})
                items.append({'kind': 'recognition_warning', 'bbox': rect, 'origin': 'native',
                              'asset': crop_asset(page, rect, directory)})
        if scan and not recognized:
            # A blank page is safe; an image-bearing page with zero OCR hits is not.
            if page.get_images() or page.get_drawings():
                issues.append({'kind': 'no_recognized_text', 'bbox': list(page.rect)})
    # Associate figure/table notes spatially; this keeps every label tied to its
    # source rather than interleaving it with an unrelated adjacent column.
    visuals = [x for x in items if x['origin'] == 'visual']
    top = []
    for item in items:
        parent = None
        if 'segment' in item:
            center = (fitz.Rect(item['bbox']).tl + fitz.Rect(item['bbox']).br) / 2
            candidates = [v for v in visuals if center in fitz.Rect(v['bbox'])]
            if candidates:
                parent = min(candidates, key=lambda v: fitz.Rect(v['bbox']).get_area())
        if parent is not None:
            parent.setdefault('notes', []).append(item)
        else:
            top.append(item)
    ordered = []
    for item in reading_order(top):
        notes = item.pop('notes', [])
        ordered.append(item)
        ordered.extend(sorted(notes, key=lambda x: (round(x['bbox'][1]/5), x['bbox'][0])))
    for i, item in enumerate(ordered):
        item['id'] = f'p{page.number+1}-b{i+1}'
    return {'width': page.rect.width, 'height': page.rect.height, 'items': ordered,
            'issues': issues, 'ocr_lines': ocr_count, 'ocr_used': needs_ocr,
            'native_lines': len(native_lines), 'bad_font_lines': len(bad_boxes)}


def prepare(pdf, out, *, layout=None, ocr=None):
    out.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    directory = out / ('reflow-assets-' + sha[:12])
    directory.mkdir(exist_ok=True)
    ocr_dir = out / 'ocr-cache'
    ocr_dir.mkdir(exist_ok=True)
    doc = fitz.open(pdf)
    try:
        if doc.needs_pass:
            raise ValueError('PDF 已加密；请先提供解密后的文件。')
        rotated = any(page.rotation for page in doc)
        for page in doc:
            if page.rotation:
                page.remove_rotation()
        if layout is None:
            layout_path = out / ('reflow-layout.json' if rotated else 'layout.json')
            layout = json.loads(layout_path.read_text(encoding='utf8')) if layout_path.exists() else {}
            if layout.get('sha256') != sha:
                from inspect_pdf_layout import inspect
                local_pdf = pdf
                if rotated:
                    local_pdf = out / 'orientation-normalized.pdf'
                    doc.save(local_pdf)
                inspect(local_pdf, layout_path)
                layout = json.loads(layout_path.read_text(encoding='utf8'))
                layout['sha256'] = sha
                translation.write_json(layout_path, layout)
        adapter = ocr or LocalOCR()
        pages = []
        for i, page in enumerate(doc):
            info = extract_page(page, layout['pages'][i]['regions'], directory, adapter,
                                ocr_dir/f'page-{i+1}.json', sha)
            pages.append(info)
            count = sum('segment' in x for x in info['items'])
            print(f'[结构] {i+1}/{len(doc)} 页：{count} 段文字，OCR 补充 {info["ocr_lines"]} 段，待复核 {len(info["issues"])} 项', flush=True)
        plan = {'version': VERSION, 'source_sha256': sha, 'source': str(pdf),
                'asset_directory': str(directory), 'pages': pages}
        translation.write_json(out/'document.json', plan)
        return plan
    finally:
        doc.close()
