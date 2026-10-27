"""usage: audit.py SOURCE.pdf OUTPUT.pdf [--limit N] [--json out.json] [--qa qa.json]
       audit.py --batch OUT_DIR SRC_DIR [--limit N]
Automatic quality audit of original-mode side-by-side outputs (left = source, right = Chinese).
Checks, all on the right half of every page:
  untranslated - a line/block of English prose still sitting in the Chinese half
  leftover     - English function words or stray capital letters glued to Chinese ("1The 香港中文大学", "第X节")
  tiny         - Chinese prose lines set much smaller than the source body text (< 0.6x median source size)
  overlap      - different words drawn on top of each other on the right but not on the left
Pages are ranked by weighted findings so the worst ones can be rendered with tools/contact.py."""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

CJK = re.compile(r'[一-鿿㐀-䶿]')
LATIN3 = re.compile(r'[A-Za-z]{3,}')
FUNC = {'the', 'of', 'and', 'for', 'in', 'on', 'to', 'a', 'an', 'with', 'from', 'by', 'as', 'at', 'is', 'are'}
URLISH = re.compile(r'@|www\.|https?://|\.com|\.edu|\.org|\.cs\.', re.I)
WEIGHTS = {'untranslated': 3, 'leftover': 2, 'tiny': 2, 'overlap': 1}


def find(page, kind, bbox, text, detail=''):
    return {'page': page, 'kind': kind, 'bbox': [round(v, 1) for v in bbox],
            'text': text[:80], 'detail': detail}


def body_median(page):
    sizes = [s['size'] for b in page.get_text('dict')['blocks'] if b['type'] == 0
             for ln in b['lines'] for s in ln['spans'] if s['text'].strip()]
    sizes.sort()
    return sizes[len(sizes) // 2] if sizes else 10


def right_lines(page, half):
    out = []
    for b in page.get_text('dict', clip=pymupdf.Rect(half, 0, page.rect.width, page.rect.height))['blocks']:
        if b['type'] != 0:
            continue
        for ln in b['lines']:
            spans = [s for s in ln['spans'] if s['text'].strip()]
            if spans:
                out.append((pymupdf.Rect(ln['bbox']), ''.join(s['text'] for s in spans),
                            max(s['size'] for s in spans)))
    return out


def structural_english(text):
    """English-looking by shape, not by vocabulary: mostly latin letters, dotted/underscored tokens, math."""
    letters = sum(c.isalpha() and c.isascii() for c in text)
    return letters >= 8 and letters >= 0.6 * len(text.replace(' ', '')) and (
        '_' in text or re.search(r'[a-z]\.[a-z]', text) or len(re.findall(r'[=<>()\\]|\.[a-z]', text)) >= 2)


RETAINED_KINDS = {'figure_text', 'formula', 'page_furniture', 'identical_translation',
                  'fraction', 'protected_region_text', 'retained_text_collision'}


def retained_zones(qa, page_no, half):
    """qa.json retained regions live in source-page coordinates; shift to output-page right half.
    Kinds like unmapped_glyph/overlapping_text mean the pipeline *failed* there - keep them auditable."""
    zones = []
    for r in qa.get('retained_regions', []):
        if r.get('page') != page_no or not r.get('bbox') or r.get('kind') not in RETAINED_KINDS:
            continue
        z = pymupdf.Rect(r['bbox'])
        if z.is_empty:
            continue
        zones.append(pymupdf.Rect(z.x0 + half, z.y0, z.x1 + half, z.y1))
    return zones


def in_zones(bbox, zones):
    r = pymupdf.Rect(bbox)
    return any((r & z).get_area() > 0.3 * min(r.get_area(), z.get_area()) for z in zones)


def overlap_pairs(words):
    words = sorted((w for w in words if w[2] > w[0] and w[3] > w[1]), key=lambda w: w[0])
    pairs = []
    for i, a in enumerate(words):
        ra = pymupdf.Rect(a[:4])
        for b in words[i + 1:]:
            if b[0] > a[2]:
                break
            if a[4] == b[4]:
                continue
            rb = pymupdf.Rect(b[:4])
            if (ra & rb).get_area() > .3 * min(ra.get_area(), rb.get_area()):
                pairs.append((a, b))
    return pairs


def audit_page(page, page_no, half, src_page, zones, src_words):
    findings = []
    lines = right_lines(page, half)

    # --- untranslated: prose-ish English lines grouped into blocks ---
    cand = []
    for bbox, text, size in lines:
        t = text.strip()
        if len(t) < 12 or in_zones(bbox, zones) or URLISH.search(t):
            continue
        words = LATIN3.findall(t)
        cjk = len(CJK.findall(t))
        if cjk > len(words) // 3:
            continue
        funcs = sum(1 for w in words if w.lower() in FUNC)
        lowers = sum(1 for w in words if w[0].islower())
        caps = sum(1 for w in words if w[0].isupper())
        prose = (funcs >= 2 and lowers >= 0.3 * len(words)) or (caps < 0.5 * len(words) and lowers >= 0.5 * len(words))
        if len(words) >= 6 and prose:
            cand.append((bbox, text))
        elif len(words) >= 5 and prose and funcs >= 1:
            # shorter leftover sentence fragment ("Gaussian noise added at each iteration.")
            cand.append((bbox, text))
    merged = []
    for bbox, text in sorted(cand, key=lambda c: c[0].y0):
        if merged and bbox.y0 - merged[-1][0].y1 < 12 and abs(bbox.x0 - merged[-1][0].x0) < 60:
            m = merged[-1]
            m[0] |= bbox
            m[1] += ' ' + text
        else:
            merged.append([pymupdf.Rect(bbox), text])
    for bbox, text in merged:
        findings.append(find(page_no, 'untranslated', bbox, text, f'{len(LATIN3.findall(text))} english words'))

    # --- leftover ---
    src_text = ' '.join(w[4] for w in src_words)
    for bbox, text, size in lines:
        if in_zones(bbox, zones):
            continue
        t = text.strip()
        m = re.search(r'(?:^|\d)\s*(The|Of|And|For|With|From|In|On|To|A|An|By|As|At|Is)\s*(?=[一-鿿])', t)
        if m:
            findings.append(find(page_no, 'leftover', bbox, t, f'english "{m.group(1)}" glued to chinese'))
            continue
        m = re.search(r'第\s*([A-Z]{1,3})\s*[节章条]?|[一-鿿]\s([A-Z])\s[一-鿿]', t)
        if m:
            letter = m.group(1) or m.group(2)
            if not re.search(rf'(?<![A-Za-z]){letter}(?![A-Za-z])', src_text):
                findings.append(find(page_no, 'leftover', bbox, t, f'stray letter "{letter}" not in source page'))
                continue
        # truncated translation: chinese bullet/paragraph ends at a section reference like "…在第7.".
        # A sentence-final reference is "Sec. 7." followed by a capital or page end; if the same
        # "Sec. N." appears in the source page *mid-sentence* (followed by lowercase or the source
        # line at this y is longer), the chinese got cut off.
        m2 = re.search(r'第\s*(\d+(?:\.\d+)*)\.?\s*$', t)
        if m2 and re.search(rf'(?:Sec\.|Section|§)\s*{re.escape(m2.group(1))}', src_text):
            src_line = ' '.join(w[4] for w in src_words if abs(w[1] - bbox.y0) < 40 and w[1] < bbox.y0 + 15)
            if len(t.strip()) < 0.5 * len(src_line.strip()) and len(src_line.strip()) > 25:
                findings.append(find(page_no, 'leftover', bbox, t,
                                     f'chinese "{t.strip()[-12:]}" ({len(t.strip())} chars) truncates source line ({len(src_line.strip())} chars)'))

    # --- tiny: line is much smaller than the source line at the same y ---
    src_lines = []
    for b in src_page.get_text('dict')['blocks']:
        if b['type'] != 0:
            continue
        for ln in b['lines']:
            spans = [s for s in ln['spans'] if s['text'].strip()]
            text = ''.join(s['text'] for s in spans)
            # skip stray artifacts (watermarks etc.) unless they are huge display lines
            if not (spans and (len(text.strip()) >= 12 or max(s['size'] for s in spans) >= 1.3 * body_median(src_page))):
                continue
            # inline math can inflate the line bbox beyond the prose baseline; use span bottoms
            y1 = min(max(s['bbox'][3] for s in spans), ln['bbox'][1] + 1.6 * max(s['size'] for s in spans))
            src_lines.append((pymupdf.Rect(ln['bbox'][0], ln['bbox'][1], ln['bbox'][2], y1),
                              max(s['size'] for s in spans)))
    seen = set()
    for bbox, text, size in lines:
        if len(CJK.findall(text)) < 4 or in_zones(bbox, zones):
            continue
        near = [sz for bb, sz in src_lines
                if bb.y0 - 12 < bbox.y0 < bb.y1 + 6 and not (
                    sz > 1.3 * body_median(src_page) and bb.x1 - bb.x0 < 0.4 * src_page.rect.width)]
        if not near:
            continue
        ref = max(near)
        if size < 0.68 * ref:
            key = round(bbox.y0 / 5)
            if key not in seen:
                seen.add(key)
                findings.append(find(page_no, 'tiny', bbox, text.strip(),
                                     f'{size:.1f}pt vs source line {ref:.1f}pt'))

    # --- overlap ---
    words = page.get_text('words')
    rpairs = overlap_pairs([w for w in words if w[0] >= half - 1])
    extra = len(rpairs) - len(overlap_pairs([w for w in words if w[2] <= half + 1]))
    if extra > 0:
        for a, b in rpairs[:extra]:
            findings.append(find(page_no, 'overlap', pymupdf.Rect(a[:4]) | pymupdf.Rect(b[:4]),
                                 f'{a[4]} / {b[4]}', 'words overlap >30% (not present in source)'))
    return findings


def audit(src_path, out_path, qa_path=None):
    src = pymupdf.open(src_path)
    out = pymupdf.open(out_path)
    qa = json.loads(Path(qa_path).read_text(encoding='utf-8')) if qa_path else {}
    findings = []
    for i, page in enumerate(out):
        if i >= len(src):
            break
        half = src[i].rect.width
        if page.rect.width <= half + 1:  # not a side-by-side page; audit whole page against source
            half = 0
        findings += audit_page(page, i + 1, half, src[i], retained_zones(qa, i + 1, half), src[i].get_text('words'))
    return findings


def report(out_path, findings, limit):
    by_page = defaultdict(list)
    for f in findings:
        by_page[f['page']].append(f)
    ranked = sorted(by_page.items(), key=lambda kv: -sum(WEIGHTS[f['kind']] for f in kv[1]))
    kinds = Counter(f['kind'] for f in findings)
    print(f'{Path(out_path).name}: findings={len(findings)} ' +
          ' '.join(f'{k}={n}' for k, n in sorted(kinds.items())) +
          f' pages_with_issues={len(by_page)}')
    print('worst pages: ' + ', '.join(
        f"p{p}({sum(WEIGHTS[f['kind']] for f in fs)})" for p, fs in ranked[:limit]))
    for p, fs in ranked[:limit]:
        for f in fs[:3]:
            print(f"  p{p:<3d} {f['kind']:<13s} {f['bbox']} {f['text']}" +
                  (f"  [{f['detail']}]" if f['detail'] else ''))
    return {'output': str(out_path), 'findings': findings,
            'summary': {k: n for k, n in sorted(kinds.items())},
            'worst_pages': [{'page': p, 'score': sum(WEIGHTS[f['kind']] for f in fs)} for p, fs in ranked]}


def batch(out_dir, src_dir, limit):
    rows = []
    for pdf in sorted(Path(out_dir).glob('*-中英.pdf')):
        stem = pdf.stem.split('-中英')[0]
        src = Path(src_dir) / (stem + '.pdf')
        if not src.exists():
            print(f'{pdf.name}: source missing, skipped')
            continue
        qa = Path(out_dir).parent / 'train' / stem / 'qa.json'
        findings = audit(src, pdf, qa if qa.exists() else None)
        kinds = Counter(f['kind'] for f in findings)
        rows.append((stem, kinds, findings))
        print(f"{stem[:52]:52s} " + ' '.join(f'{k}={n}' for k, n in sorted(kinds.items())) +
              f' total={len(findings)}')
    tot = Counter()
    for _, k, _ in rows:
        tot.update(k)
    print('TOTAL ' + ' '.join(f'{k}={n}' for k, n in sorted(tot.items())) + f' docs={len(rows)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--limit', type=int, default=15)
    ap.add_argument('--json')
    ap.add_argument('--qa')
    ap.add_argument('--batch', nargs=2, metavar=('OUT_DIR', 'SRC_DIR'))
    a = ap.parse_args()
    if a.batch:
        batch(a.batch[0], a.batch[1], a.limit)
        return
    if len(a.paths) != 2:
        ap.error('need SOURCE.pdf OUTPUT.pdf')
    findings = audit(a.paths[0], a.paths[1], a.qa)
    r = report(a.paths[1], findings, a.limit)
    if a.json:
        Path(a.json).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding='utf-8')
        print('wrote', a.json)


if __name__ == '__main__':
    main()
