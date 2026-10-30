"""Regression: unit tests + cache-miss count + offline run of the 4 base papers
and every finished 训练资料 output. usage: regress.py [--paid] (paid = fill cache misses)."""
import subprocess,sys,json,re
from pathlib import Path
HERE=Path(__file__).resolve().parent.parent;PY=str(HERE/'.venv-next/Scripts/python.exe')
T=HERE.parent/'测试新论文'
jobs=[(T/'2610.28941.pdf',T/'out-miner'),(T/'2610.28962.pdf',T/'out-ropt'),(T/'2610.28814.pdf',T/'out'),(HERE.parent/'2605.22536v2.pdf',HERE/'.test/iter/22536')]
for out in sorted((T/'train').glob('*')):
    if (out/'text-cache.json').exists():jobs.append((HERE.parent/'训练资料'/(out.name+'.pdf'),out))
paid='--paid' in sys.argv
u=subprocess.run([PY,'-X','utf8','-W','ignore','-m','unittest','test_pdfzh_nosource','test_pdfzh_user','test_pdfzh_reflow','test_pdfzh_latex'],cwd=HERE,capture_output=True,text=True,encoding='utf8',errors='replace')
print('unittest:',u.stderr.strip().splitlines()[-1])
procs=[]
for pdf,out in jobs:
    log=open(out/'regress.log','w',encoding='utf8')
    procs.append((pdf,out,subprocess.Popen([PY,'-X','utf8','-W','ignore','pdfzh_nosource.py',str(pdf),'--outdir',str(out)]+([] if paid else ['--offline']),
                 cwd=HERE,stdout=log,stderr=subprocess.STDOUT),log))
    if len([p for p in procs if p[2].poll() is None])>=4:
        for p in procs:p[2].wait()
for pdf,out,p,log in procs:
    p.wait();log.close()
    text=(out/'regress.log').read_text(encoding='utf8',errors='replace')
    cov=re.search(r'\[正文覆盖率\] ([\d.]+%)',text);err=re.findall(r'\[(?:错误|翻译未完成|本地结构错误)\] (.*)',text)
    print(f'{out.name:50s} rc={p.returncode} cov={cov[1] if cov else "-"} {err[-1][:150] if err else ""}')
