import sys,json,pymupdf
sys.path.insert(0,r'C:\Users\Lenovo\Desktop\具身智能\pdfzh')
# usage: cmp.py <pdf> <outdir> <page>: slot kinds/text of keep.py vs current engine
pdf,out,pg=sys.argv[1],sys.argv[2],int(sys.argv[3])
lay=json.load(open(out+r'\layout.json',encoding='utf8'))['pages'][pg-1]['regions']
for name,path in (('keep',r'C:\Users\Lenovo\AppData\Local\Temp\opencode\keep.py'),('cur',r'C:\Users\Lenovo\Desktop\具身智能\pdfzh\pdfzh_nosource.py')):
    ns={'__name__':'x','__file__':r'C:\Users\Lenovo\Desktop\具身智能\pdfzh\pdfzh_nosource.py'}
    exec(compile(open(path,encoding='utf8').read(),path,'exec'),ns)
    page=pymupdf.open(pdf)[pg-1]
    s,r=ns['blocks_for_page'](page,lay)
    print('==',name)
    for x in sorted(s+r,key=lambda x:x['bbox'][1]):
        print(' ',x['kind'],[round(v) for v in x['bbox']],repr((x.get('source') or x.get('text',''))[:70]))
