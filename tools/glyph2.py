import pymupdf,sys,json
# usage: glyph2.py <pdf> <outdir> <page> <x0> <y0>: per-char keep/drop for the island starting near (x0,y0)
pdf,out,pg=sys.argv[1],sys.argv[2],int(sys.argv[3]);X,Y=float(sys.argv[4]),float(sys.argv[5])
plan=json.load(open(out+r'\structure.json',encoding='utf8'))
d=pymupdf.open(pdf)
chars=[(ch['c'],tuple(ch['bbox'])) for b in d[pg-1].get_text('rawdict')['blocks'] for l in b.get('lines',[]) for sp in l['spans'] for ch in sp['chars']]
for s in plan['pages'][pg-1]['slots']:
    for isl in s.get('islands',[]):
        if abs(isl['bbox'][0]-X)>1.5 or abs(isl['bbox'][1]-Y)>1.5:continue
        print('ISL',[round(v,1) for v in isl['bbox']]);print(' members',[[round(v,1) for v in m] for m in isl['members']])
        clip=pymupdf.Rect(isl['bbox'])+(0,-.4,0,.4)
        boxes=[tuple(pymupdf.Rect(m)+(-.2,-.2,.2,.2)) for m in isl['members']]
        for c,(x0,y0,x1,y1) in chars:
            if x1<clip.x0 or x0>clip.x1 or y1<clip.y0 or y0>clip.y1:continue
            full=any(a<=x0 and x1<=e and b<=y0 and y1<=f for a,b,e,f in boxes)
            print('  ',repr(c),[round(v,1) for v in (x0,y0,x1,y1)],'keep' if full else 'drop')
