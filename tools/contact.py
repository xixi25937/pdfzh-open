"""usage: contact.py out.pdf OUTDIR page [page ...]   -> OUTDIR/<stem>_p<N>.png at a readable size
Look at the pages themselves; metrics only tell you where to look."""
import sys
from pathlib import Path
import pymupdf

pdf, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
doc = pymupdf.open(pdf)
for n in map(int, sys.argv[3:]):
    path = out/f'{pdf.stem[:30]}_p{n}.png'
    doc[n-1].get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).save(path)
    print(path)
