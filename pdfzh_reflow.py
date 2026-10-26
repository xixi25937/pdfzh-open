"""Complete-text bilingual reading mode, with local pagination and OCR.

Images and equations remain source crops. Text alone reaches DeepSeek. Every
planned item has a source anchor and must finish rendering before publication.
"""
from __future__ import annotations

import argparse
from collections import Counter
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys

import pymupdf as fitz

import pdfzh_document as document
import pdfzh_latex as translation
import pdfzh_nosource as native
import pdfzh_styles as styles


def text_items(plan):
    for page in plan['pages']:
        yield from (x for x in page['items'] if 'segment' in x)


def load_cache(out, plan):
    # Geometry is not part of translation identity. Preserve the legacy cache;
    # the new renderer can reuse its exactly matching protected text for free.
    cache = {}
    for name in ('text-cache.json', 'reflow-text-cache.json'):
        path = out/name
        if path.exists():
            cache.update(json.loads(path.read_text(encoding='utf8')))
    pending = []
    for item in text_items(plan):
        segment = item['segment']; key = translation.digest(segment['text'])
        try:
            native.translated_text(segment, cache.get(key, ''))
        except (ValueError, TypeError):
            cache.pop(key, None)
            pending.append({'id': item['id'], 'cache_key': key, 'origin': item['origin'],
                            'source': item['source'], 'bbox': item['bbox']})
    translation.write_json(out/'reflow-text-cache.json', cache)
    translation.write_json(out/'pending-translations.json', pending)
    return cache, pending


def style_css(fmt):
    return (f'color:#{fmt.get("color", 0):06x};'
            f'font-weight:{"bold" if fmt.get("bold") else "normal"};'
            f'font-style:{"italic" if fmt.get("italic") else "normal"}')


def image_html(asset, max_width, max_height, *, inline=False):
    scale = min(1, max_width/asset['width'], max_height/asset['height'])
    return (f'<img src="{escape(asset["file"], quote=True)}" '
            f'style="width:{asset["width"]*scale:.2f}pt;height:{asset["height"]*scale:.2f}pt;'
            f'vertical-align:{"middle" if inline else "bottom"}">')


def translated_html(item, value):
    segment = item['segment']
    native.translated_text(segment, value)
    if not item.get('inline_images'):
        runs = styles.decoded_runs(segment, value) if segment.get('style_events') else [
            {'text': native.translated_text(segment, value), 'style': item['style']}]
        return ''.join(f'<span style="{style_css(r["style"])}">{escape(r["text"]).replace(chr(10), "<br>")}</span>' for r in runs)
    events = segment.get('style_events', {})
    images = item['inline_images']
    pieces = []; cursor = 0
    for match in translation.TOKEN.finditer(value):
        pieces.append(escape(value[cursor:match.start()]))
        key = match.group()[3:-2]
        event = events.get(key)
        if key in images:
            pieces.append(image_html(images[key], 200, 28, inline=True))
        elif event:
            if event['kind'] == 'open':
                pieces.append(f'<span style="{style_css(event["style"])}">')
            elif event['kind'] == 'close':
                pieces.append('</span>')
            else:
                pieces.append('<br>')
        else:
            pieces.append(escape(segment['protected'][int(key)]))
        cursor = match.end()
    pieces.append(escape(value[cursor:]))
    return ''.join(pieces)


def page_html(page, cache, width, height, preview):
    chunks = []
    for item in page['items']:
        # A source anchor includes vertical and horizontal position for tables
        # and figures whose notes share the same source page.
        box = item['bbox']
        anchor = f'{item["id"]} · x{round(box[0])} y{round(box[1])}'
        contents = '' if item['kind']=='notation' else f'<div class="anchor">{anchor}</div>'
        if 'segment' in item:
            key = translation.digest(item['segment']['text'])
            if key in cache:
                contents += translated_html(item, cache[key])
            else:
                # A missing translation shows the original, clearly marked;
                # the report counts it, so the result never claims completion.
                label = '缺译文缓存' if preview else '未取得合格译文'
                contents += f'<span class="warning">[{label} · 以下为原文]</span><br>' + escape(item['source'])
        else:
            label = {'figure': '原图；图中文字译注见下方', 'table': '原表；单元格译注见下方',
                     'isolate_formula': '公式原貌', 'notation': '原文数值 / 符号',
                     'recognition_warning': '文字识别不确定，请对照左侧原文'}[item['kind']]
            if item['kind']!='notation':
                contents += '<div class="caption">' + label + '</div>'
            contents += '<div style="line-height:0">'+image_html(item['asset'], width-64, height-160)+'</div>'
        chunks.append(f'<div class="item" id="{item["id"]}">{contents}</div>')
    if page['issues']:
        chunks.insert(0, '<p class="warning">本页有文字识别待复核区域，不能视为完整翻译；详情见质量报告。</p>')
    if preview:
        chunks.insert(0, '<p class="warning">缓存预览：仅展示已有译文，未调用翻译接口。</p>')
    return '<html><body>' + ''.join(chunks or ['<p>原页未提取到可翻译文字。</p>']) + '</body></html>'


def rendering_environment(asset_directory):
    archive = fitz.Archive(str(asset_directory))
    css = '''body {margin:0;font-family:sans-serif;font-size:11pt;line-height:1.55;color:#18212c;}
    .item {margin:0 0 9pt 0;} .anchor {font-size:7pt;color:#7b8693;margin-bottom:2pt;}
    .caption {font-size:9pt;color:#546375;margin-bottom:4pt;}
    .warning {color:#a04217;font-size:10pt;}
    '''
    fonts = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'
    if (fonts/'msyh.ttc').is_file() and (fonts/'msyhbd.ttc').is_file():
        archive.add(str(fonts))
        css += '''@font-face {font-family:pdfzh;src:url(msyh.ttc);}
        @font-face {font-family:pdfzh;src:url(msyhbd.ttc);font-weight:bold;}
        body {font-family:pdfzh,sans-serif;}'''
    return archive, css


def render(pdf, out, plan, cache, *, preview=False):
    with fitz.open() as dual:
        return _render(pdf, out, plan, cache, dual, preview=preview)


def _render(pdf, out, plan, cache, dual, *, preview=False):
    archive, css = rendering_environment(plan['asset_directory'])
    mapping, finished_ids = [], set()
    with fitz.open(pdf) as original:
        for page in original:
            if page.rotation:
                page.remove_rotation()
        for pi, info in enumerate(plan['pages']):
            width = max(420, min(info['width'], 720))
            height = max(595, min(info['height'], 1000))
            buffer = BytesIO()
            writer = fitz.DocumentWriter(buffer)
            def position(pos):
                if pos.id and pos.open_close & 2:
                    finished_ids.add(pos.id)

            try:
                # Place independent content blocks into the remaining page.
                # A single giant HTML tree can skip sibling blocks in MuPDF
                # when an anonymous inline box crosses a page boundary.
                device = writer.begin_page(fitz.Rect(0, 0, width, height))
                y, continuation = 43, 0
                blocks = []
                if preview or info['issues']:
                    notice = '缓存预览：仅展示已有译文，未调用翻译接口。' if preview else ''
                    if info['issues']:
                        notice += '本页有文字识别待复核区域，详情见质量报告。'
                    blocks.append(('<html><body><p class="warning">'+notice+'</p></body></html>', 0))
                if not info['items']:
                    blocks.append(('<html><body><p>原页未提取到可翻译文字。</p></body></html>', 0))
                for item in info['items']:
                    html=page_html({'items': [item], 'issues': []}, cache, width, height, False if 'segment' not in item else preview)
                    html=html.replace('<p class="warning">缓存预览：仅展示已有译文，未调用翻译接口。</p>', '')
                    keep_height=0
                    if 'asset' in item:
                        asset=item['asset']
                        scale=min(1,(width-64)/asset['width'],(height-160)/asset['height'])
                        keep_height=asset['height']*scale+(20 if item['kind']=='notation' else 62)
                    blocks.append((html,keep_height))
                for html, keep_height in blocks:
                    story = fitz.Story(html=html, user_css=css, archive=archive)
                    if y>43 and height-28-y<keep_height:
                        writer.end_page(); device=writer.begin_page(fitz.Rect(0,0,width,height))
                        y=43; continuation+=1
                    while True:
                        if height-28-y < 65:
                            writer.end_page(); device = writer.begin_page(fitz.Rect(0, 0, width, height))
                            y = 43; continuation += 1
                        more, filled = story.place(fitz.Rect(28, y, width-28, height-28))
                        story.element_positions(position)
                        story.draw(device)
                        y = max(y+1, fitz.Rect(filled).y1)
                        if not more:
                            break
                        writer.end_page(); device = writer.begin_page(fitz.Rect(0, 0, width, height))
                        y = 43; continuation += 1
                        if continuation > 500:
                            raise RuntimeError(f'第 {pi+1} 页无法安全分页，已停止，缓存保留。')
                writer.end_page()
            finally:
                writer.close()
            with fitz.open(stream=buffer.getvalue(), filetype='pdf') as right:
                for page in right:
                    for picture in page.get_image_info():
                        bbox=fitz.Rect(picture['bbox'])
                        if bbox.x0<27 or bbox.x1>width-27 or bbox.y0<42 or bbox.y1>height-27:
                            raise RuntimeError(f'原文第 {pi+1} 页的图形超出中文页边界，已停止发布。')
                for j, page in enumerate(right):
                    header = f'原文第 {pi+1} 页 · 中文 {j+1}/{len(right)}' + (' · 缓存预览' if preview else '')
                    page.insert_htmlbox(fitz.Rect(28, 12, width-28, 37), escape(header),
                                        css='body{font-size:9pt;color:#596579}', scale_low=1)
                    page.draw_line((28, 38), (width-28, 38), color=(.8,.83,.86), width=.5)
                # Finish editing the source before MuPDF creates its graft map.
                # Adding font objects after the first copied page invalidates it.
                # Subset ONLY the generated side: source fonts can be malformed
                # yet render correctly and must never be rewritten.
                right.subset_fonts()
                for j, page in enumerate(right):
                    ow, oh = original[pi].rect.width, original[pi].rect.height
                    sheet = dual.new_page(width=ow+width, height=max(oh, height))
                    if original[pi].get_contents():
                        sheet.show_pdf_page(fitz.Rect(0, 0, ow, oh), original, pi)
                    sheet.show_pdf_page(fitz.Rect(ow, 0, ow+width, height), right, j)
                    mapping.append({'output_page': len(dual), 'source_page': pi+1, 'part': j+1,
                                    'parts': len(right), 'left_width': ow, 'left_height': oh})
            expected = {x['id'] for x in info['items']}
            missing = expected - finished_ids
            if missing:
                raise RuntimeError('排版没有完整放入这些区域：' + ', '.join(sorted(missing)))
            print(f'[排版] 原文 {pi+1}/{len(original)} 页 → {len(right) if not right.is_closed else mapping[-1]["parts"]} 页中文，全部区域已放入', flush=True)
    filename = pdf.stem + ('-缓存预览.pdf' if preview else '-中英重排.pdf')
    target = out/filename
    # Write to a temporary result first; an interrupted render never replaces
    # the previous useful PDF. publish_copy also handles reader file locks.
    temporary = out/'reflow-result.tmp.pdf'
    dual.set_toc([[1, f'原文第 {p["source_page"]} 页', p['output_page']] for p in mapping if p['part'] == 1])
    print('[保存] 正在压缩文件并写入质量报告…', flush=True)
    dual.save(temporary, garbage=4, deflate=True)
    target = native.publish_copy(temporary, target)
    temporary.unlink(missing_ok=True)
    issues = [{'page': i+1, **issue} for i,p in enumerate(plan['pages']) for issue in p['issues']]
    missing = [x['id'] for x in text_items(plan) if translation.digest(x['segment']['text']) not in cache]
    report = {'engine': 'reflow', 'source_sha256': plan['source_sha256'],
              'automated_checks_passed': True, 'preview': preview,
              'translation_complete': not preview and not missing and not issues,
              'status': 'preview' if preview else 'needs_review' if issues or missing else 'translated',
              'source_pages': len(plan['pages']), 'output_pages': len(mapping),
              'text_items': sum(1 for _ in text_items(plan)),
              'translated_items': sum(translation.digest(x['segment']['text']) in cache for x in text_items(plan)),
              'missing_ids': missing, 'recognition_issues': issues,
              'ocr_pages': [i+1 for i,p in enumerate(plan['pages']) if p['ocr_used']],
              'rendered_items': len(finished_ids), 'page_mapping': mapping,
              'output_files': {'bilingual': str(target)},
              'limitations': ['自动检查不保证 OCR 无漏识别或翻译语义正确；图表译注需结合原图阅读。']}
    translation.write_json(out/'reflow-qa.json', report)
    write_report(out, report)
    return report


def write_report(out, report):
    title = {'preview': '缓存预览，尚未完成翻译', 'needs_review': '译文已排版，识别仍需复核',
             'translated': '已提取文字已翻译并排版'}[report['status']]
    rows = ''.join(f'<tr><td>{x["page"]}</td><td>{escape(x["kind"])}</td><td>{escape(x.get("text", ""))}</td></tr>'
                   for x in report['recognition_issues'])
    html = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>PDFZH 质量报告</title>
    <style>body{{font:16px/1.7 sans-serif;max-width:900px;margin:40px auto;padding:20px}}td,th{{padding:8px;border:1px solid #ccc}}table{{border-collapse:collapse}}</style>
    <h1>{title}</h1><p>原文 {report['source_pages']} 页，双语输出 {report['output_pages']} 页。
    已提取文字 {report['text_items']} 段，已翻译 {report['translated_items']} 段，缺缓存 {len(report['missing_ids'])} 段。
    识别待复核 {len(report['recognition_issues'])} 项。</p>
    <p>续页重复展示对应原页。右侧小字坐标是原页的位置（PDF 点），便于查找图表标注。
    图形、公式原貌保留；图表中的译文以译注方式列在原图之后。</p>
    <p>这些数字只覆盖程序已检测的区域，不能证明 OCR 没有漏字，也不能证明译文语义正确。</p>
    <table><tr><th>原页</th><th>问题</th><th>识别候选</th></tr>{rows}</table></html>'''
    (out/'质量报告.html').write_text(html, encoding='utf8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='本地 OCR 与完整文字重排；DeepSeek 仅翻译文字。')
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--outdir', required=True, type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--analyze-only', action='store_true')
    parser.add_argument('--preview-cache', action='store_true', help='仅已有缓存预览，缺译文明示为原文，禁止翻译调用')
    args = parser.parse_args(argv)
    pdf, out = args.pdf.resolve(), args.outdir.resolve()
    print('[启动] 完整文字重排模式；公式和图形本地保护，中文可续页。', flush=True)
    try:
        plan = document.prepare(pdf, out)
    except (translation.SourceValidationError, KeyboardInterrupt):
        raise
    except Exception as exc:
        out.mkdir(parents=True, exist_ok=True)
        copy = native.publish_copy(pdf, out/(pdf.stem+'-提取失败-原文.pdf'))
        translation.write_json(out/'reflow-qa.json', {'status': 'prepare_failed', 'automated_checks_passed': False,
            'error': str(exc)[:500], 'output_files': {'bilingual': str(copy)}})
        print(f'[错误] 结构提取失败（{str(exc)[:120]}）；已输出原文副本 {copy}', flush=True)
        return 5
    cache, pending = load_cache(out, plan)
    print(f'[缓存] {sum(1 for _ in text_items(plan))} 段文字，缺缓存 {len(pending)} 段（清单已保存）。', flush=True)
    if args.analyze_only:
        print('[分析完成] document.json 与 pending-translations.json；没有翻译调用。', flush=True)
        return 0
    usage = {}
    if not args.preview_cache:
        from pdfzh_user import api_key
        cfg = {'key': '' if args.offline else api_key(), 'model': 'deepseek-chat', 'url': 'https://api.deepseek.com'}
        try:
            cache, usage = translation.build_translation(
                [(Path('document'), '', [x['segment'] for x in text_items(plan)])],
                out/'reflow-text-cache.json', out/'unused-tracking.json', cfg, args.offline)
        except translation.TranslationIncompleteError as exc:
            # Network, quota or repeated model errors: publish what was
            # translated; the rest is shown as marked original text.
            if args.offline: raise
            cache = json.loads((out/'reflow-text-cache.json').read_text(encoding='utf8'))
            print(f'[翻译] 部分片段未取得合格译文（{str(exc)[:80]}），以原文标注显示；重新运行只补这些片段', flush=True)
        # Validate numeric integrity before spending any time rendering.
        for item in text_items(plan):
            key = translation.digest(item['segment']['text'])
            if key in cache: native.translated_text(item['segment'], cache[key])
    try:
        report = render(pdf, out, plan, cache, preview=args.preview_cache)
    except Exception as exc:
        # Last resort: the user still gets a document (the original) and the
        # cause; the translation cache is kept for a later rerun.
        copy = native.publish_copy(pdf, out/(pdf.stem+'-排版失败-原文.pdf'))
        translation.write_json(out/'reflow-qa.json', {'status': 'layout_failed', 'automated_checks_passed': False,
            'error': str(exc)[:500], 'output_files': {'bilingual': str(copy)}})
        print(f'[错误] 排版失败（{str(exc)[:120]}）；已输出原文副本 {copy}，译文缓存已保留', flush=True)
        return 5
    report['this_run_translation_usage'] = usage
    translation.write_json(out/'reflow-qa.json', report)
    print('[结果] '+report['output_files']['bilingual'], flush=True)
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf8', errors='replace')
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('[取消] 缓存已保留。'); sys.exit(130)
    except translation.SourceValidationError as exc:
        print('[本地结构错误] '+str(exc), file=sys.stderr, flush=True); sys.exit(4)
    except translation.TranslationIncompleteError as exc:
        print('[翻译未完成] '+str(exc), file=sys.stderr, flush=True); sys.exit(3)
    except Exception as exc:
        print('[错误] '+str(exc), file=sys.stderr, flush=True); sys.exit(2)
