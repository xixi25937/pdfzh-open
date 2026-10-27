"""usage: layout_check.py [--src DIR] out1.pdf out2.pdf ...   (original-mode side-by-side outputs)
For every page: left half = source, right half = Chinese. Reports
  overlap  - pairs of different words on top of each other in the right half (figure labels drawn twice, etc.)
  match    - share of source text blocks whose area is mostly covered by right-half text (layout correspondence)
Pages are listed worst first so they can be looked at with tools/contact.py."""
import sys
from pathlib import Path
import pymupdf

SRC = Path(__file__).resolve().parent.parent.parent/'训练资料'


def overlap_pairs(words):
    words = sorted((w for w in words if w[2] > w[0] and w[3] > w[1]), key=lambda w: w[0])
    pairs = []
    for i, a in enumerate(words):
        ra = pymupdf.Rect(a[:4])
        for b in words[i+1:]:
            if b[0] > a[2]:
                break
            if a[4] == b[4]:
                continue
            rb = pymupdf.Rect(b[:4])
            if (ra & rb).get_area() > .3*min(ra.get_area(), rb.get_area()):
                pairs.append((a[4], b[4]))
    return pairs


def blocks(page, x0, x1):
    return [pymupdf.Rect(b[:4]) for b in page.get_text('blocks', clip=pymupdf.Rect(x0, 0, x1, page.rect.height))
            if b[6] == 0 and len(b[4].split()) >= 2]


def page_stats(page, half):
    words = page.get_text('words')
    right_pairs = overlap_pairs([w for w in words if w[0] >= half-1])
    # Overlaps already present in the source (dense figure labels) are not our fault.
    extra = len(right_pairs) - len(overlap_pairs([w for w in words if w[2] <= half+1]))
    pairs = right_pairs if extra > 0 else []
    left = blocks(page, 0, half)
    right = [r for r in blocks(page, half, page.rect.width)]
    covered = 0
    for b in left:
        area = b.get_area()
        got = sum((b & pymupdf.Rect(r.x0-half, r.y0, r.x1-half, r.y1)).get_area() for r in right)
        covered += got/area >= .5 if area else 0
    return len(pairs), pairs[:3], len(left), covered


def main(paths):
    rows = []
    for p in paths:
        pdf = Path(p)
        stem = pdf.stem.split('-中英')[0].split('-中英')[0]
        src = SRC/(stem+'.pdf')
        try:
            out = pymupdf.open(pdf); orig = pymupdf.open(src) if src.exists() else None
        except Exception as exc:
            print(pdf.name, 'ERROR', exc); continue
        total = bad_overlap = left_n = matched = 0
        worst = []
        for i, page in enumerate(out):
            half = orig[i].rect.width if orig is not None and i < len(orig) else page.rect.width/2
            n, ex, ln, cov = page_stats(page, half)
            total += 1; bad_overlap += n > 0; left_n += ln; matched += cov
            if n:
                worst.append((n, i+1, ex))
        worst.sort(reverse=True)
        rate = matched/left_n if left_n else 1
        rows.append((stem, len(out), bad_overlap, rate, worst[:4]))
    print(f'{"doc":44s} pages overlap_pages match')
    for stem, pages, bad, rate, worst in sorted(rows, key=lambda r: (-r[2], r[3])):
        print(f'{stem[:44]:44s} {pages:5d} {bad:13d} {rate:5.0%}  ' + ' '.join(f'p{pg}:{n}{ex[:1]}' for n, pg, ex in worst))


if __name__ == '__main__':
    args = sys.argv[1:]
    if args and args[0] == '--src':
        SRC = Path(args[1]); args = args[2:]
    main(args)
