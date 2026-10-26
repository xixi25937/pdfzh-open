import sys,pymupdf
sys.path.insert(0,r'C:\Users\Lenovo\Desktop\具身智能\pdfzh')
# usage: nat.py <pdf> <page> <y0> <y1>: lines of blocks starting in [y0,y1] after math_islands, and which islands went native
src=open(r'C:\Users\Lenovo\Desktop\具身智能\pdfzh\pdfzh_nosource.py',encoding='utf8').read()
pdf,pg,y0,y1=sys.argv[1],int(sys.argv[2]),float(sys.argv[3]),float(sys.argv[4])
page=pymupdf.open(pdf)[pg-1]
for variant in ('orig','nonative'):
    s=src if variant=='orig' else src.replace("if x.get('merged') or x.get('native'):continue","if x.get('merged'):continue")
    ns={'__name__':'x','__file__':r'C:\Users\Lenovo\Desktop\具身智能\pdfzh\pdfzh_nosource.py'}
    exec(compile(s,'pdfzh_nosource.py','exec'),ns)
    bl=page.get_text('dict')['blocks'];ns['tall_glyph_boxes'](page,bl);ns['small_caps'](bl);ns['math_islands'](bl)
    for b in bl:
        if b['type']==0 and y0<b['bbox'][1]<y1:
            print(variant,[round(v) for v in b['bbox']],[''.join(x['text'] for x in l['spans'])[:60] for l in b['lines']])
