"""Build an allowlisted, auditable source release; never archive the workspace."""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT=Path(__file__).resolve().parent
FILES=['pdfzh_cli.py','pdfzh_user.py','pdfzh_runtime.py','pdfzh_nosource.py','pdfzh_latex.py','pdfzh_styles.py',
       'pdfzh_document.py','pdfzh_reflow.py','test_pdfzh_reflow.py',
       'inspect_pdf_layout.py','test_pdfzh_user.py','test_pdfzh_nosource.py','test_pdfzh_latex.py',
       'start.cmd','configure.cmd','results.cmd','install.cmd','install.ps1',
       'pdfzh-nosource.cmd','pdfzh-latex.cmd','requirements.txt','LICENSE',
       'THIRD_PARTY_NOTICES.md','SECURITY.md','.gitignore','.gitattributes','build_release.py']


def audit_text(name,text):
    if re.search(r'\bsk-[A-Za-z0-9_-]{16,}',text):raise ValueError('疑似密钥：'+name)
    if re.search(r'[A-Za-z]:[\\/]Users[\\/][^\\/\s"\']+',text):raise ValueError('个人绝对路径：'+name)


def build():
    sources={name:ROOT/name for name in FILES}
    sources['README.md']=ROOT/'release/README.md' if (ROOT/'release/README.md').exists() else ROOT/'README.md'
    sources['pdfzh.cmd']=ROOT/'release/pdfzh.cmd' if (ROOT/'release/pdfzh.cmd').exists() else ROOT/'pdfzh.cmd'
    sources['CONTRIBUTING.md']=ROOT/'release/CONTRIBUTING.md' if (ROOT/'release/CONTRIBUTING.md').exists() else ROOT/'CONTRIBUTING.md'
    prepared={}
    for name,path in sources.items():
        text=path.read_text(encoding='utf8');audit_text(name,text)
        if name.endswith(('.cmd','.ps1')):text=text.replace('\r\n','\n').replace('\n','\r\n')
        prepared[name]=text.encode('utf8')
    root=ROOT/'dist';root.mkdir(exist_ok=True)
    name='pdfzh-0.2.2-'+datetime.now().strftime('%Y%m%d-%H%M%S')
    destination=root/name;destination.mkdir()
    for relative,data in prepared.items():
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    hashes={name:hashlib.sha256(data).hexdigest() for name,data in prepared.items()}
    (destination/'MANIFEST.json').write_text(json.dumps(hashes,indent=2),encoding='utf8')
    archive=root/(name+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(destination.iterdir()):z.write(path,name+'/'+path.name)
    sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(sha+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps({'source_folder':str(destination),'zip':str(archive),'sha256':sha,
                      'files':len(prepared),'bytes':archive.stat().st_size},ensure_ascii=False,indent=2))
    return destination,archive


if __name__=='__main__':build()
