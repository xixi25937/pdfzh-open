"""Simple Windows front door; imports the heavy engine only when needed."""
from __future__ import annotations
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pdfzh_user as user


def choose_pdf():
    import tkinter as tk
    from tkinter import filedialog
    root=tk.Tk();root.withdraw()
    try:return filedialog.askopenfilename(title='选择英文 PDF',filetypes=[('PDF 文档','*.pdf')])
    finally:root.destroy()


def configure():
    print('DeepSeek Chat API 配置（不会调用接口，不会验证或产生费用）')
    try:
        import tkinter as tk
    except ImportError:
        key=getpass.getpass('DeepSeek API Key（不回显）: ')
        user.set_key(key);return True
    try:
        from tkinter import simpledialog
        root=tk.Tk();root.withdraw()
        try:key=simpledialog.askstring('PDFZH · DeepSeek API',
            '粘贴 DeepSeek API Key（输入隐藏，仅保存在当前 Windows 用户的加密配置中）',show='*',parent=root)
        finally:root.destroy()
        if key is None:return False
    except (tk.TclError,RuntimeError):
        key=getpass.getpass('DeepSeek API Key（不回显）: ')
    user.set_key(key)
    print('[配置] 已加密保存。更换密钥可运行：pdfzh.cmd configure')
    return True


def seed_previous_cache(pdf,out):
    """Upgrade existing local installs without charging for cached text again."""
    if (out/'text-cache.json').exists():return
    previous=Path(__file__).parent/'output'/(pdf.stem+'-nosource')
    report=previous/'qa.json'
    if not report.is_file():return
    import hashlib
    q=json.loads(report.read_text(encoding='utf8'))
    if q.get('source_sha256')!=hashlib.sha256(pdf.read_bytes()).hexdigest():return
    out.mkdir(parents=True,exist_ok=True)
    for name in ['text-cache.json','layout.json','qa.json']:
        if (previous/name).is_file():shutil.copy2(previous/name,out/name)
    print('[缓存] 已复用旧入口的同一文档缓存，不重新翻译已有文字。')


def reveal(path):
    path=Path(path).resolve()
    if not path.exists():raise ValueError('结果路径不存在')
    if os.name=='nt':
        # Open the folder rather than the PDF, avoiding PDF reader file locks.
        os.startfile(str(path if path.is_dir() else path.parent))
    else:print('[目录] '+str(path.parent if path.is_file() else path))


def doctor():
    print('Python: '+sys.version.split()[0])
    missing=[]
    for name in ['xelatex','latexmk','fc-match']:
        path=shutil.which(name);print(name+': '+(path or '未安装 / 不在 PATH'))
    for name in ['pymupdf','pypdf','pylatexenc','babeldoc','rapidocr_onnxruntime']:
        import importlib.util
        found=importlib.util.find_spec(name) is not None
        print(name+': '+('已安装' if found else '缺少'))
        if not found:missing.append(name)
    print('密钥: '+('已配置（未联网验证）' if user.api_key() else '未配置'))
    if missing:raise RuntimeError('依赖缺少，请双击 install.cmd。')
    print('[检查] 重排模式本地依赖可用；TeX Live 仅用于 --mode original。版面模型首次使用时下载。')


def main(argv=None):
    parser=argparse.ArgumentParser(description='PDFZH：选 PDF，使用 DeepSeek Chat 翻译，本地生成左英右中 PDF。')
    parser.add_argument('input',nargs='?',help='PDF 路径，或 configure / results / doctor；省略则打开文件选择框')
    parser.add_argument('--offline',action='store_true',help='只用已有译文缓存，不调用翻译 API')
    parser.add_argument('--analyze-only',action='store_true',help='只检查结构，不翻译')
    parser.add_argument('--mode',choices=['reflow','original'],help='默认 original：中文排在原文位置，版式与原文一致，需要 TeX Live；未安装时自动用 reflow：中文可续页')
    parser.add_argument('--preview-cache',action='store_true',help='重排模式仅用缓存生成预览；缺译文明确标注，不调用翻译接口')
    parser.add_argument('--output',type=Path,help='输出根目录；默认在原 PDF 旁的“翻译结果”')
    parser.add_argument('--no-open',action='store_true',help='完成后不打开结果文件夹')
    args=parser.parse_args(argv)
    if args.mode is None:
        args.mode='reflow' if args.preview_cache or not all(shutil.which(n) for n in ('xelatex','latexmk','fc-match')) else 'original'
        if args.mode=='reflow' and not args.preview_cache and args.input not in ('configure','doctor','results'):
            print('[提示] 未找到 TeX Live（xelatex/latexmk），改用重排模式；版式与原文不同。安装 TeX Live 后可获得与原文一致的版式。')
    if args.preview_cache and args.mode!='reflow':parser.error('--preview-cache 仅用于 reflow 模式')
    if args.input=='configure':configure();return 0
    if args.input=='doctor':doctor();return 0
    if args.input=='results':
        result=user.load_settings().get('last_result')
        if not result:raise RuntimeError('还没有成功结果，请先选择一个 PDF。')
        print('[最近结果] '+result);reveal(result);return 0
    if not args.offline and not args.analyze_only and not args.preview_cache and not user.api_key():
        if not sys.stdin.isatty():raise RuntimeError('未配置 API Key；先运行 pdfzh.cmd configure 或设置 DEEPSEEK_API_KEY。')
        if not configure():print('[取消] 未保存密钥。');return 0
    selected=args.input or choose_pdf()
    if not selected:print('[取消] 未选择 PDF。');return 0
    pdf=Path(selected.strip('"')).expanduser().resolve()
    if not pdf.is_file() or pdf.suffix.lower()!='.pdf':raise ValueError('请选择存在的 PDF 文件。')
    from pdfzh_nosource import check_complete
    check_complete(pdf)
    out=user.job_directory(pdf,args.output)
    seed_previous_cache(pdf,out)
    print('[说明] 解析、OCR 与排版在本机进行，只有文字翻译消耗 DeepSeek 额度。')
    print('[结果目录] '+str(out))
    engine='pdfzh_reflow.py' if args.mode=='reflow' else 'pdfzh_nosource.py'
    command=[sys.executable,str(Path(__file__).with_name(engine)),str(pdf),'--outdir',str(out)]
    if args.offline:command.append('--offline')
    if args.analyze_only:command.append('--analyze-only')
    if args.preview_cache:command.append('--preview-cache')
    env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';env['PYTHONUTF8']='1'
    if not args.offline and not args.analyze_only and not args.preview_cache:env['DEEPSEEK_API_KEY']=user.api_key()
    else:env.pop('DEEPSEEK_API_KEY',None)
    rc=subprocess.run(command,env=env).returncode
    if rc==5:
        report=json.loads((out/('reflow-qa.json' if args.mode=='reflow' else 'qa.json')).read_text(encoding='utf8'))
        print('[未完成] 排版失败，已输出原文副本：'+report['output_files']['bilingual']+'。译文缓存已保留，诊断目录：'+str(out))
        if not args.no_open:reveal(out)
        return rc
    if rc:
        if rc==4:
            print('[未完成] 原文的本地保护结构有问题，本次没有调用翻译接口。请保留缓存与诊断文件；重复翻译无效。诊断目录：'+str(out))
        elif rc==3:
            qa=out/('reflow-qa.json' if args.mode=='reflow' else 'qa.json')
            report=json.loads(qa.read_text(encoding='utf8')) if qa.exists() else {}
            if report.get('translation_failure'):
                # Bad key / no network: a document with the original text is still delivered.
                print('[未完成] '+report['translation_failure']+' 已输出 PDF（未译部分保留原文）：'+report['output_files']['bilingual']+'。诊断目录：'+str(out))
                if not args.no_open:reveal(out)
            elif args.offline:
                print('[未完成] 仍有片段没有译文缓存，--offline 不能补齐。去掉 --offline 重新运行即可，只翻译缺失片段，已有缓存不重译。诊断目录：'+str(out))
            else:
                print('[未完成] 仍有片段缺少合格译文。修正问题后重新运行，只补缺失片段；已有合格缓存不重译。诊断目录：'+str(out))
        else:
            print('[未完成] 译文缓存已保留。若是编译或质检失败，重复翻译不能修复；可用 --offline 零翻译费用重排。诊断目录：'+str(out))
        return rc
    if args.analyze_only:
        if not args.no_open:reveal(out)
        return 0
    report=json.loads((out/('reflow-qa.json' if args.mode=='reflow' else 'qa.json')).read_text(encoding='utf8'))
    output=Path(report['output_files']['bilingual'])
    # Friendly copy lives above caches; authoritative output remains in the job.
    from pdfzh_nosource import publish_copy
    checked=report.get('automated_checks_passed')
    suffix='-缓存预览.pdf' if args.preview_cache else '-中英对照.pdf' if checked else '-中英对照-需复核.pdf'
    if report.get('garbled_text',{}).get('warning'):
        print('[警告] 这份 PDF 的字体编码异常，大部分文字无法提取，没有翻译、保留英文原样。需要 OCR 或换一份 PDF。')
    elif not checked:print('[提示] 部分自动检查未通过，仍输出结果；问题位置见 '+str(out/'qa.json'))
    friendly=publish_copy(output,out.parent/(pdf.stem+suffix))
    settings=user.load_settings();settings['last_result']=str(friendly);save_error=None
    try:user.save_settings(settings)
    except OSError:save_error='未能保存最近结果记录；PDF 已生成。'
    status='缓存预览，未完成翻译' if args.preview_cache else '已生成，仍有区域需复核' if report.get('recognition_issues') or report.get('retained_regions') else '已生成'
    print('\n['+status+'] '+str(friendly))
    if args.mode=='reflow':print('[质量报告] '+str(out/'质量报告.html'))
    print('[以后找回] 双击 results.cmd，或运行 pdfzh.cmd results')
    if save_error:print('[提示] '+save_error)
    if not args.no_open:
        try:reveal(friendly)
        except OSError:print('[提示] 无法自动打开文件夹，请使用上方路径。')
    return 0


if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf8',errors='replace')
    try:sys.exit(main())
    except KeyboardInterrupt:print('\n[取消] 已结束。');sys.exit(130)
    except Exception as exc:print('[错误] '+str(exc),file=sys.stderr);sys.exit(2)
