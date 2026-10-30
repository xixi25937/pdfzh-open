"""usage: batch.py [--offline] a.pdf b.pdf ...  (cwd = pdfzh). Runs the unmodified
engine on 训练资料 files in parallel; prints rc, body coverage, retained kinds, cost."""
import subprocess,sys,json,re
from pathlib import Path
HERE=Path(__file__).resolve().parent.parent
SRC=HERE.parent/'训练资料';ROOT=HERE.parent/'测试新论文'/'train'
args=[a for a in sys.argv[1:] if not a.startswith('--')];extra=[a for a in sys.argv[1:] if a.startswith('--')]
procs=[]
for n in args:
    out=ROOT/Path(n).stem;out.mkdir(parents=True,exist_ok=True)
    log=open(out/'run.log','w',encoding='utf8')
    p=subprocess.Popen([str(HERE/'.venv-next/Scripts/python.exe'),'-X','utf8','-W','ignore','pdfzh_nosource.py',str(SRC/n),'--outdir',str(out),*extra],
                       cwd=HERE,stdout=log,stderr=subprocess.STDOUT)
    procs.append((n,out,p,log))
for n,out,p,log in procs:
    p.wait();log.close()
    text=(out/'run.log').read_text(encoding='utf8',errors='replace')
    cov=re.search(r'\[正文覆盖率\] (.*)',text);err=re.findall(r'\[(?:错误|翻译未完成|本地结构错误)\] (.*)',text)
    usage=json.loads((out/'last-translation-usage.json').read_text()) if (out/'last-translation-usage.json').exists() else {}
    kinds=''
    if (out/'qa.json').exists() and p.returncode==0:
        kinds=json.loads((out/'qa.json').read_text(encoding='utf8')).get('retained_by_kind','')
    print(f'{Path(n).stem} rc={p.returncode} cov={cov[1] if cov else "-"} tokens={usage.get("total_tokens","-")} {err[-1][:200] if err else ""}')
    if kinds:print('   ',kinds)
