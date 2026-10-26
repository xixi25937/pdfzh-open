"""Cross-format regression: real OCR, pagination, styles and source preservation."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as fitz

import pdfzh_document as document
import pdfzh_latex as translation
import pdfzh_nosource as native
import pdfzh_reflow as reflow
import pdfzh_cli as cli


class NoOCR:
    def recognize(self, *args):
        return []


def empty_layout(count):
    return {'pages': [{'regions': []} for _ in range(count)]}


class ReflowTests(unittest.TestCase):
    def test_reading_order_full_width_heading_and_two_columns(self):
        boxes = [(10, 10, 390, 30), (10, 50, 190, 90), (210, 50, 390, 90),
                 (10, 100, 190, 140), (210, 100, 390, 140)]
        items = [{'bbox': b, 'name': i} for i, b in enumerate(boxes)]
        self.assertEqual([x['name'] for x in document.reading_order(items)], [0, 1, 3, 2, 4])

    def test_native_block_cannot_merge_two_columns_and_body(self):
        lines=[]
        for box,text in [([20,40,180,55],'FEATURES'),([220,40,380,55],'APPLICATIONS'),
                         ([20,60,180,75],'Stable voltage'),([220,60,380,75],'Battery chargers'),
                         ([20,130,380,145],'A separate full width paragraph starts here.')]:
            lines.append({'bbox':box,'spans':[{'bbox':box,'text':text,'size':10}]})
        regions=[{'kind':'plain text','confidence':.9,'bbox':r} for r in
                 [[15,35,185,80],[215,35,385,80],[15,125,385,150]]]
        groups=document.paragraph_groups(lines,regions)
        texts=[native.body_text(g) for g in groups]
        self.assertFalse(any('FEATURES' in t and 'APPLICATIONS' in t for t in texts))
        self.assertIn('A separate full width paragraph starts here.',texts)
        parts=document.split_line({'bbox':[20,20,400,35],'spans':[
            {'text':'Manufacturer','size':10,'bbox':[20,20,100,35]},
            {'text':' '*40,'size':10,'bbox':[100,20,300,35]},
            {'text':'Voltage regulator','size':10,'bbox':[300,20,400,35]}]})
        self.assertEqual([native.body_text([x]) for x in parts],['Manufacturer','Voltage regulator'])

    def test_real_scanned_pdf_ocr_and_cache_without_second_recognition(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'scan.pdf'
            original = fitz.open(); p = original.new_page(width=420, height=300)
            p.insert_text((35, 70), 'Safety instructions', fontsize=23)
            p.insert_text((35, 115), 'Disconnect power before cleaning.', fontsize=16)
            raster = p.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes('png')
            scan = fitz.open(); p = scan.new_page(width=420, height=300)
            p.insert_image(p.rect, stream=raster); scan.save(source); scan.close(); original.close()
            adapter = document.LocalOCR()
            plan = document.prepare(source, root/'job', layout=empty_layout(1), ocr=adapter)
            text = ' '.join(x['source'] for x in reflow.text_items(plan)).lower()
            self.assertIn('safety instructions', text)
            self.assertIn('disconnect power before cleaning', text)
            self.assertTrue(all(x['origin'] == 'ocr' for x in reflow.text_items(plan)))
            with patch.object(adapter, 'engine', side_effect=AssertionError('OCR cache not used')):
                again = document.prepare(source, root/'job', layout=empty_layout(1), ocr=adapter)
            self.assertEqual(plan, again)

    def test_mixed_sizes_rotation_long_translation_styles_and_original_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'mixed.pdf'; out = root/'job'
            src = fitz.open()
            p = src.new_page(width=420, height=400)
            p.insert_font(fontname='sourcefont',fontbuffer=fitz.Font('cjk').buffer)
            p.insert_text((28,380),'原始字体保留',fontname='sourcefont',fontsize=10)
            p.insert_text((28, 45), 'Important warning', fontsize=14, fontname='hebo', color=(.8,0,0))
            p.insert_text((28, 80), 'Read the instructions carefully.', fontsize=11)
            p.draw_rect(fitz.Rect(280, 150, 360, 210), color=(0, .5, .3), fill=(.7,.9,.8))
            p = src.new_page(width=300, height=500)
            p.insert_text((25, 70), 'Second page instructions', fontsize=13)
            p.set_rotation(90)
            src.new_page(width=220, height=330)  # valid blank page
            src.save(source); src.close()
            plan = document.prepare(source, out, layout=empty_layout(3), ocr=NoOCR())
            cache = {}
            for item in reflow.text_items(plan):
                value = '重要警告' if 'Important' in item['source'] else ('请仔细阅读说明，所有译文必须完整保留。'*300 + '结尾校验文字')
                cache[translation.digest(item['segment']['text'])] = value
            report = reflow.render(source, out, plan, cache)
            self.assertGreater(report['output_pages'], 3)
            self.assertTrue(report['translation_complete'])
            output = fitz.open(report['output_files']['bilingual'])
            source_doc = fitz.open(source)
            source_fonts={source_doc.extract_font(x[0])[3] for x in source_doc[0].get_fonts() if source_doc.extract_font(x[0])[3]}
            output_fonts={output.extract_font(x[0])[3] for x in output[0].get_fonts(full=True) if output.extract_font(x[0])[3]}
            self.assertTrue(source_fonts.issubset(output_fonts))
            right_text = ''
            for row in report['page_mapping']:
                page = output[row['output_page']-1]; original = source_doc[row['source_page']-1]
                left = page.get_pixmap(clip=fitz.Rect(0, 0, row['left_width'], row['left_height']), alpha=False)
                expected = original.get_pixmap(alpha=False)
                import numpy as np
                a=np.frombuffer(left.samples,dtype=np.uint8).astype(int)
                b=np.frombuffer(expected.samples,dtype=np.uint8).astype(int)
                self.assertLess(float(np.abs(a-b).mean()), .02, str(row))
                right_text += page.get_text(clip=fitz.Rect(row['left_width'],40,page.rect.width,page.rect.height-28))
            self.assertEqual(right_text.count('结尾校验文字'), 2)
            self.assertEqual(''.join(right_text.split()).count('请仔细阅读说明，所有译文必须完整保留。'), 600)
            spans = [s for b in output[0].get_text('dict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans']]
            chinese = [s for s in spans if '重要警告' in s['text']]
            self.assertTrue(chinese)
            self.assertEqual(chinese[0]['color'], 0xcc0000)
            self.assertTrue(chinese[0]['flags'] & 16)
            output.close(); source_doc.close()

    def test_inline_math_uses_source_image_and_escapes_model_html(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); doc = fitz.open(); page = doc.new_page()
            page.insert_text((30,50), 'Compute x for stability.', fontsize=12)
            # Different font / superscript is represented by the same extraction
            # structure used for mathematical glyphs in real PDF documents.
            lines = [{'bbox': [30,35,180,54], 'spans': [
                {'text':'Compute ', 'font':'Arial', 'size':12, 'bbox':[30,35,80,54], 'flags':0},
                {'text':'x', 'font':'CMMI10', 'size':12, 'bbox':[80,35,87,54], 'flags':0},
                {'text':' for stability.', 'font':'Arial', 'size':12, 'bbox':[87,35,180,54], 'flags':0}]}]
            item = document.make_text_item(page, lines, root)
            value = item['segment']['text'].replace('Compute ', '计算 ').replace(' for stability.', ' 以保证稳定性<script>')
            html = reflow.translated_html(item, value)
            self.assertIn('<img ', html)
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
            for asset in item['inline_images'].values():
                self.assertTrue((root/asset['file']).exists())
            doc.close()

    def test_many_short_labels_survive_page_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'labels.pdf'; out=root/'job'
            with fitz.open() as doc:
                p=doc.new_page(width=420,height=800)
                for i in range(65):
                    p.insert_text((25,25+i*11),f'Label {i}',fontsize=9)
                doc.save(source)
            plan=document.prepare(source,out,layout=empty_layout(1),ocr=NoOCR())
            cache={translation.digest(x['segment']['text']):x['segment']['text'].replace('Label','标签')
                   for x in reflow.text_items(plan)}
            report=reflow.render(source,out,plan,cache)
            with fitz.open(report['output_files']['bilingual']) as doc:
                text=''.join(p.get_text(clip=fitz.Rect(420,40,p.rect.width,p.rect.height-28)) for p in doc)
                import re
                self.assertEqual(sorted(map(int,re.findall(r'标签\s*(\d+)',text))),list(range(65)))
            self.assertEqual(report['rendered_items'],len(plan['pages'][0]['items']))

    def test_large_figure_is_not_cut_at_a_page_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'figure.pdf'; out=root/'job'
            with fitz.open() as doc:
                page=doc.new_page(width=420,height=600)
                page.insert_text((25,40),'Read the description before inspecting the figure.',fontsize=11)
                page.draw_rect((25,170,390,550),color=(1,0,0),width=3)
                page.draw_line((25,170),(390,550),color=(0,0,1),width=3)
                doc.save(source)
            layout={'pages':[{'regions':[{'kind':'figure','confidence':.99,'bbox':[23,168,392,552]}]}]}
            plan=document.prepare(source,out,layout=layout,ocr=NoOCR())
            cache={translation.digest(x['segment']['text']):'这是图的说明。'*65 for x in reflow.text_items(plan)}
            report=reflow.render(source,out,plan,cache)
            self.assertGreater(report['output_pages'],1)
            with fitz.open(report['output_files']['bilingual']) as doc:
                images=[(p,im) for p in doc for im in p.get_image_info() if im['bbox'][0]>=420]
                self.assertEqual(len(images),1)
                for p,im in images:
                    self.assertGreaterEqual(im['bbox'][1],42)
                    self.assertLessEqual(im['bbox'][3],p.rect.height-27)

    def test_offline_missing_cache_stops_and_preview_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'manual.pdf'; out = root/'job'
            doc = fitz.open(); p = doc.new_page(); p.insert_text((30,50),'Safety instructions'); doc.save(source); doc.close()
            plan = document.prepare(source, out, layout=empty_layout(1), ocr=NoOCR())
            with patch.object(document, 'prepare', return_value=plan), \
                 patch.object(translation, 'api_batch', side_effect=AssertionError('network forbidden')):
                with self.assertRaisesRegex(RuntimeError, '离线模式'):
                    reflow.main([str(source), '--outdir', str(out), '--offline'])
                self.assertEqual(reflow.main([str(source), '--outdir', str(out), '--preview-cache']), 0)
            report = json.loads((out/'reflow-qa.json').read_text(encoding='utf8'))
            self.assertFalse(report['translation_complete'])
            self.assertEqual(report['status'], 'preview')
            self.assertEqual(report['translated_items'], 0)
            result = fitz.open(report['output_files']['bilingual'])
            self.assertIn('缺译文缓存', result[0].get_text())
            result.close()

    def test_network_failure_still_publishes_marked_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'manual.pdf'; out = root/'job'
            doc = fitz.open(); p = doc.new_page(); p.insert_text((30,50),'Safety instructions'); doc.save(source); doc.close()
            plan = document.prepare(source, out, layout=empty_layout(1), ocr=NoOCR())
            with patch.object(document, 'prepare', return_value=plan), patch.object(translation.time, 'sleep'), \
                 patch('pdfzh_user.api_key', return_value='not-a-real-key'), \
                 patch.object(translation, 'api_batch', side_effect=OSError('offline')):
                self.assertEqual(reflow.main([str(source), '--outdir', str(out)]), 0)
            report = json.loads((out/'reflow-qa.json').read_text(encoding='utf8'))
            self.assertFalse(report['translation_complete'])
            self.assertEqual(report['status'], 'needs_review')
            with fitz.open(report['output_files']['bilingual']) as result:
                self.assertIn('未取得合格译文', result[0].get_text())

    def test_uncertain_ocr_keeps_crop_and_cannot_claim_completion(self):
        class WeakOCR:
            def recognize(self, *args):
                return [{'bbox':[20,20,100,40], 'text':'uncertain reading', 'confidence':.6}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'weak.pdf'; out = root/'job'
            doc = fitz.open(); p = doc.new_page(); p.draw_rect((20,20,100,40)); doc.save(source); doc.close()
            plan = document.prepare(source, out, layout=empty_layout(1), ocr=WeakOCR())
            self.assertEqual(list(reflow.text_items(plan)), [])
            report = reflow.render(source, out, plan, {})
            self.assertEqual(report['status'], 'needs_review')
            self.assertFalse(report['translation_complete'])

    def test_flipped_bitmap_bbox_is_normalized(self):
        with tempfile.TemporaryDirectory() as folder, fitz.open() as doc:
            root = Path(folder); page = doc.new_page()
            page.insert_text((20, 40), 'Safety')
            asset = document.crop_asset(page, [309.0, 329.5, 357.1, 307.2], root)
            self.assertGreater(asset['width'], 0)
            self.assertGreater(asset['height'], 0)
            self.assertTrue((root/asset['file']).exists())
            raw = {'blocks': [{'type': 1, 'bbox': [309.0, 329.5, 357.1, 307.2]}]}
            with patch.object(fitz.Page, 'get_text', return_value=raw):
                info = document.extract_page(page, [], root, NoOCR(), root/'ocr.json', 'source')
            figures = [x for x in info['items'] if x.get('origin') == 'visual']
            self.assertEqual(len(figures), 1)
            self.assertLess(figures[0]['bbox'][1], figures[0]['bbox'][3])

    def test_prepare_failure_publishes_original_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'broken.pdf'; out = root/'job'
            doc = fitz.open(); doc.new_page(); doc.save(source); doc.close()
            with patch.object(document, 'prepare', side_effect=ValueError('原文区域坐标为空')):
                self.assertEqual(reflow.main([str(source), '--outdir', str(out), '--offline']), 5)
            report = json.loads((out/'reflow-qa.json').read_text(encoding='utf8'))
            self.assertEqual(report['status'], 'prepare_failed')
            self.assertFalse(report['automated_checks_passed'])
            with fitz.open(report['output_files']['bilingual']) as result:
                self.assertEqual(len(result), 1)

    def test_failed_page_falls_back_to_notice_and_others_render(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'three.pdf'; out = root/'job'
            doc = fitz.open()
            for i, text in enumerate(['First page', 'Second page', 'Third page']):
                p = doc.new_page(width=420, height=300)
                p.insert_text((35, 70), text, fontsize=16)
            doc.save(source); doc.close()
            plan = document.prepare(source, out, layout=empty_layout(3), ocr=NoOCR())
            cache = {translation.digest(x['segment']['text']): '译文'+x['segment']['text'] for x in reflow.text_items(plan)}
            real_page_html = reflow.page_html
            calls = []
            def flaky_page_html(page, cache, width, height, preview):
                calls.append(page['items'][0]['id'])
                if len(calls) == 2:  # second block overall -> page 2 body fails
                    raise RuntimeError('mock layout failure')
                return real_page_html(page, cache, width, height, preview)
            with patch.object(reflow, 'page_html', side_effect=flaky_page_html):
                report = reflow.render(source, out, plan, cache)
            self.assertEqual(report['status'], 'needs_review')
            self.assertFalse(report['translation_complete'])
            self.assertFalse(report['automated_checks_passed'])
            failed = [x for x in report['recognition_issues'] if x['kind'] == 'layout_failed']
            self.assertEqual([x['page'] for x in failed], [2])
            self.assertIn('mock layout failure', failed[0]['text'])
            # every source page has at least one output page
            by_source = {}
            for row in report['page_mapping']:
                by_source.setdefault(row['source_page'], 0)
                by_source[row['source_page']] += 1
            self.assertEqual(sorted(by_source), [1, 2, 3])
            result = fitz.open(report['output_files']['bilingual'])
            self.assertEqual(len(result), report['output_pages'])
            failed_rows = [r for r in report['page_mapping'] if r['source_page'] == 2]
            self.assertEqual(len(failed_rows), 1)
            row = failed_rows[0]
            right = result[row['output_page']-1].get_text(clip=fitz.Rect(row['left_width'], 0, result[row['output_page']-1].rect.width, 600))
            self.assertIn('本页中文排版失败', right)
            # other pages still show translations
            good_rows = [r for r in report['page_mapping'] if r['source_page'] != 2]
            text = ''.join(result[r['output_page']-1].get_text(clip=fitz.Rect(r['left_width'], 0, 2000, 2000)) for r in good_rows)
            self.assertIn('译文', text)
            result.close()

    def test_whitespace_control_glyph_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as folder,fitz.open() as doc:
            root=Path(folder); page=doc.new_page()
            page.insert_text((20,40),'Safety')
            raw={'blocks':[{'type':0,'bbox':[20,20,90,45],'lines':[
                {'bbox':[20,20,90,45],'spans':[{'text':'\n','bbox':[20,20,90,45],'size':12}]}]}]}
            with patch.object(fitz.Page,'get_text',return_value=raw):
                info=document.extract_page(page,[],root,NoOCR(),root/'ocr.json','source')
            self.assertEqual(info['bad_font_lines'],1)
            self.assertIn('unresolved_font',[x['kind'] for x in info['issues']])


if __name__ == '__main__':
    unittest.main()
