"""usage: rbatch.py [--offline|--analyze-only] a.pdf ...  (cwd = pdfzh). Runs the unmodified
default reflow engine; seeds each job with the original-mode text cache when present."""
import subprocess,sys,json,re,shutil
from pathlib import Path
HERE=Path(r'C:\Users\Lenovo\Desktop\具身智能\pdfzh')
SRC=HERE.parent/'训练资料';TRAIN=HERE.parent/'测试新论文'/'train';ROOT=HERE.parent/'测试新论文'/'reflow'
args=[a for a in sys.argv[1:] if not a.startswith('--')];extra=[a for a in sys.argv[1:] if a.startswith('--')]
procs=[]
for n in args:
    stem=Path(n).stem;out=ROOT/stem;out.mkdir(parents=True,exist_ok=True)
    seed=TRAIN/stem/'text-cache.json'
    if seed.exists() and not (out/'text-cache.json').exists():shutil.copy2(seed,out/'text-cache.json')
    log=open(out/'run.log','w',encoding='utf8')
    p=subprocess.Popen([str(HERE/'.venv-next/Scripts/python.exe'),'-X','utf8','-W','ignore','pdfzh_reflow.py',str(SRC/n),'--outdir',str(out),*extra],
                       cwd=HERE,stdout=log,stderr=subprocess.STDOUT)
    procs.append((stem,out,p,log))
for stem,out,p,log in procs:
    p.wait();log.close()
    text=(out/'run.log').read_text(encoding='utf8',errors='replace')
    pend=re.search(r'\[缓存\] (\d+) 段文字，缺缓存 (\d+) 段',text)
    err=re.findall(r'\[错误\] (.*)|Error: (.*)|RuntimeError: (.*)',text)
    usage=json.loads((out/'last-translation-usage.json').read_text()) if (out/'last-translation-usage.json').exists() and '--analyze-only' not in extra else {}
    q=json.loads((out/'reflow-qa.json').read_text(encoding='utf8')) if (out/'reflow-qa.json').exists() and '--analyze-only' not in extra else {}
    info=f"items={q.get('text_items')} translated={q.get('translated_items')} issues={len(q.get('recognition_issues',[]))} pages={q.get('source_pages')}->{q.get('output_pages')} status={q.get('status')}" if q else ''
    print(f'{stem} rc={p.returncode} segs={pend[1] if pend else "-"} pending={pend[2] if pend else "-"} tokens={usage.get("total_tokens","-")} {info} {[e for e in err[-1] if e][0][:200] if err else ""}')
