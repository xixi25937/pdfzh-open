import json,sys,pymupdf
from pathlib import Path
sys.path.insert(0,r'C:\Users\Lenovo\Desktop\具身智能\pdfzh')
import pdfzh_nosource as app,pdfzh_latex as latex
# usage: miss.py <pdf> <outdir> [pages]
pdf=Path(sys.argv[1]);out=Path(sys.argv[2])
cache=json.load(open(out/'text-cache.json',encoding='utf8'))
lay=json.load(open(out/'layout.json',encoding='utf8'))['pages']
old=json.load(open(out/'structure.json',encoding='utf8'))
miss=0;chars=0
with pymupdf.open(pdf) as d:
    for i,page in enumerate(d):
        if len(page.get_text().strip())<20:continue
        slots,ret=app.blocks_for_page(page,lay[i]['regions'])
        for s in slots:
            if latex.digest(s['segment']['text']) not in cache.get('items',cache):
                miss+=1;chars+=len(s['source']);print(i+1,s['kind'],s['source'][:90])
        ok=sum(1 for r in ret if r['kind']=='overlapping_text');ok0=sum(1 for r in old['pages'][i]['retained'] if r['kind']=='overlapping_text')
        if ok!=ok0:print('  page',i+1,'overlapping',ok0,'->',ok)
print('missing',miss,'chars',chars)
