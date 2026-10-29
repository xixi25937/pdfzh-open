import unittest
import re
import unicodedata
from unittest.mock import patch
import tempfile
import hashlib
import json
import shutil
import os
import subprocess
from types import SimpleNamespace
from pathlib import Path
import pymupdf
import pdfzh_nosource as app
import pdfzh_latex as latex


class NativeTests(unittest.TestCase):
    def test_figure_callouts_merge_wrapped_lines_and_stacked_ticks(self):
        with pymupdf.open() as d:
            p=d.new_page(width=300,height=300)
            # Narrow Q/A callout: one question wrapped over three short lines.
            p.insert_text((20,40),'Q: Consider the relative\nrotation from A to B, what\nis the most prominent?\nA: Turn right.',fontsize=4)
            # Stacked chart tick label in two separate PDF blocks.
            p.insert_text((150,40),'Human',fontsize=4);p.insert_text((150,44.5),'Level',fontsize=4)
            slots,_=app.blocks_for_page(p,[])
            sources=[s['source'] for s in slots]
            self.assertIn('Q: Consider the relative rotation from A to B, what is the most prominent?',sources)
            self.assertIn('A: Turn right.',sources)
            self.assertIn('Human Level',sources)
        self.assertEqual(app.segment('A: South.')['text'],'A: South.')
        self.assertIn('@@P0@@',app.segment('vector A and B')['text'])

    def test_number_split_across_fonts_stays_one_token(self):
        line={'bbox':[0,0,300,10],'spans':[
            {'text':'success rate of ','font':'Times','size':10,'color':0,'flags':0},
            {'text':'50','font':'Times-Bold','size':10,'color':0,'flags':16},
            {'text':'.0%','font':'Times','size':10,'color':0,'flags':0},
            {'text':' with ','font':'Times','size':10,'color':0,'flags':0},
            {'text':'T-WLA','font':'Times-Bold','size':10,'color':0,'flags':16}]}
        runs=app.styles.source_runs([line])
        self.assertIn('50.0%',''.join(r['text'] for r in runs if not r.get('break')).replace(' ',''))
        self.assertTrue(any('50.0%' in r['text'] for r in runs))

    def test_link_number_does_not_swallow_sentence_period(self):
        line={'bbox':[0,0,300,10],'spans':[
            {'text':'in Eq. ','font':'Times','size':10,'color':0,'flags':0},
            {'text':'1','font':'Times','size':10,'color':0xff0000,'flags':0},
            {'text':'.','font':'Times','size':10,'color':0,'flags':0}]}
        runs=app.styles.source_runs([line])
        self.assertEqual([r['text'] for r in runs if r['style']['color']],['1'])

    def test_styled_run_keeps_leading_space_outside_group(self):
        st={'color':0x367DBD,'bold':False,'italic':False}
        tex=app.styles.to_tex([{'text':'2022.','style':{**st,'color':0}},{'text':' 2','style':st}],lambda v:v)
        self.assertIn('2022.} {',tex)

    def test_small_caps_restore_words_and_capital_size(self):
        def sp(t,size):return {'text':t,'font':'NimbusRomNo9L-Regu','size':size}
        blocks=[{'type':0,'lines':[{'spans':[sp('H',9.96),sp('ISTORY',7.97),sp('-A',9.96),
            sp('WARE',7.97),sp(' M',9.96),sp('EMORY',7.97),sp(' x',7.97)]}]}]
        app.small_caps(blocks)
        spans=blocks[0]['lines'][0]['spans']
        self.assertEqual(''.join(s['text'] for s in spans),'History-Aware Memory x')
        self.assertEqual({s['size'] for s in spans[:6]},{9.96})

    def test_adjacent_table_headers_stay_separate_rows(self):
        def ln(t,x0,x1):return {'bbox':(x0,79.4,x1,85.8),'dir':(1,0),'spans':[{'text':t,'bbox':(x0,79.4,x1,85.8)}]}
        rows=app.visual_rows([ln('tomato soup can',327,369.2),ln('mustard bottle',379.6,416.6)])
        self.assertEqual(len(rows),2)
        rows=app.visual_rows([ln('justified',327,369.2),ln('word',375,400)])
        self.assertEqual(len(rows),1)

    def test_island_gets_gap_next_to_hanzi_only(self):
        s={'islands':[{'bbox':(0,0,5,10),'baseline':8,'size':10,'page':1}]}
        out=app.island_escape(s)('令牌'+chr(app.ISLAND_BASE)+'，')
        self.assertEqual(out.count(r'\hskip'),1)

    def test_island_clipping_keeps_cmsy_symbols(self):
        # Adam p13: ‖g_t‖₂≤G. CMSY span boxes are tight in 'dict' (what
        # math_islands records as members) but font-tall in 'rawdict' (what
        # write_islands compares), so span-box equality dropped ‖ ≤ ∞.
        src=app.HERE/'test-island-cmsy.pdf'
        with pymupdf.open(src) as doc:
            slots,_=app.blocks_for_page(doc[0],[])
            plan={'pages':[{'slots':slots}]}
            with tempfile.TemporaryDirectory() as folder:
                out=Path(folder)/'islands.pdf'
                app.write_islands(doc,plan,out)
                with pymupdf.open(out) as islands:
                    text=''.join(p.get_text() for p in islands)
        for symbol in '∥≤∞∈−√≜':
            self.assertIn(symbol,text)
        self.assertNotIn('Lemma',text)  # neighbouring prose stays erased

    def test_inline_math_becomes_one_protected_island(self):
        def sp(t,font,size,x0,x1,y=(0,10)):
            return {'text':t,'font':font,'size':size,'flags':0,'color':0,'bbox':(x0,y[0],x1,y[1]),'origin':(x0,8)}
        blocks=[{'type':0,'lines':[
            {'bbox':(0,0,200,11),'dir':(1,0),'spans':[sp('the memory','NimbusRomNo9L-Regu',10,0,60),
             sp(' M','CMMI10',10,60,72),sp('t','CMMI7',7,72,75,(-2,5)),sp(' and more text','NimbusRomNo9L-Regu',10,76,140)]},
            {'bbox':(72,4,78,11),'dir':(1,0),'spans':[sp('V','CMR7',7,72,78,(4,11))]}]}]
        islands=app.math_islands(blocks)
        self.assertEqual(len(islands),1)
        self.assertEqual(len(islands[0]['members']),3)  # M, t and the detached V
        line=blocks[0]['lines'][0]
        text=''.join(s['text'] for s in line['spans'])
        self.assertEqual(text,'the memory '+chr(app.ISLAND_BASE)+' and more text')
        self.assertEqual(len(blocks[0]['lines']),1)
        seg=app.segment(text)
        self.assertIn(chr(app.ISLAND_BASE),seg['protected'])

    def test_glyph_code_named_font_gets_tounicode(self):
        # IRLZ44N (PageMaker + Distiller 6): no /ToUnicode, /Differences
        # [1 /G72 /G105] = "Hi"; MuPDF extracted control characters and every
        # page stayed English as unmapped_glyph.
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'g.pdf'
            with pymupdf.open() as d:
                page=d.new_page(width=200,height=100)
                f=d.get_new_xref();d.update_object(f,'<</Type/Font/Subtype/Type1/BaseFont/Helvetica/FirstChar 1/LastChar 2/Widths[722 222]'
                                                   '/Encoding<</Type/Encoding/Differences[1/G72/G105]>>>>')
                d.xref_set_key(page.xref,'Resources','<</Font<</F1 %d 0 R>>>>'%f)
                c=d.get_new_xref();d.update_object(c,'<<>>');d.update_stream(c,b'BT /F1 12 Tf 20 50 Td <0102> Tj ET')
                d.xref_set_key(page.xref,'Contents','%d 0 R'%c)
                d.save(src)
            with pymupdf.open(src) as d:self.assertNotIn('Hi',d[0].get_text())
            mapped=app.glyph_code_mapped(src,Path(folder))
            self.assertNotEqual(mapped,src)
            with pymupdf.open(mapped) as d:self.assertIn('Hi',d[0].get_text())
            # A font with ordinary glyph names is left alone.
            self.assertEqual(app.glyph_code_mapped(mapped,Path(folder)),mapped)

    def test_symbol_bullet_in_own_line_joins_item_row(self):
        # AP2112 p1: Symbol \uf0b7 bullet is its own PDF line, 12pt left of the
        # item text; it stayed an orphan formula and the whole list was kept
        # English as unmapped_glyph.
        def sp(t,font,x0,x1):
            return {'text':t,'font':font,'size':8,'flags':0,'color':0,'bbox':(x0,342,x1,351.5),'origin':(x0,349.5)}
        def build(glyph):
            return [{'type':0,'lines':[{'bbox':(55.5,342,63.3,351.5),'dir':(1,0),'spans':[sp(glyph,'Symbol',55.5,61.2),sp(' ','Arial',61.1,63.3)]}]},
                    {'type':0,'lines':[{'bbox':(73.3,342,191,351.5),'dir':(1,0),'spans':[sp('Output Voltage Accuracy','Arial',73.3,191)]}]}]
        blocks=build('\uf0b7\uf020');islands=app.math_islands(blocks)
        self.assertEqual(len(islands),1)
        self.assertTrue(app.starts_bullet(blocks[0]['lines'][0]))
        # The translation keeps the bullet's tab stop (12pt = 1.5 em) instead of
        # starting right after the bullet (k17: items misaligned with kept ones).
        isl={**islands[0],'page':1}
        self.assertAlmostEqual(isl['after'],(73.3-61.2)/8,places=2)
        tex=app.island_escape({'islands':[isl]})(chr(app.ISLAND_BASE)+' 输出电压精度')
        self.assertIn(r'\hskip'+f'{isl["after"]:.4f}'+r'\slotfont',tex)
        self.assertNotIn(r'\hskip.2em',tex)
        # Another Symbol glyph at the same distance (a detached formula) stays out.
        self.assertEqual(app.math_islands(build('\uf057')),[])

    def test_circled_number_digit_joins_its_circle_island(self):
        # Diffusion Policy: "1" (NimbusSanL) overprinted by a CMSY9 circle.
        def sp(t,font,size,x0,y0,x1,y1):
            return {'text':t,'font':font,'size':size,'flags':0,'color':0,'bbox':(x0,y0,x1,y1),'origin':(x0,y1-2)}
        blocks=[{'type':0,'lines':[{'bbox':(51,176,285,186),'dir':(1,0),'spans':[
            sp('needs to ','NimbusSanL-Regu',8.5,51,177,88,186),sp('1','NimbusSanL-Regu',8.5,88.3,177.7,93,186.2),
            sp('\u20dd','CMSY9',9,86.1,176.5,95.3,185.5),sp('push the bowl','NimbusSanL-Regu',8.5,97.7,177,285,186)]}]}]
        islands=app.math_islands(blocks)
        self.assertEqual(len(islands),1)
        self.assertEqual(len(islands[0]['members']),2)
        text=''.join(s['text'] for s in blocks[0]['lines'][0]['spans'])
        self.assertNotIn('1',text)

    def test_leading_raised_marker_is_an_island_not_fused_with_next_word(self):
        # EKT-Router: affiliation "1" (6pt, raised) + "The Chinese University"
        # became the protected token "1The" and the translation kept "The".
        def sp(t,size,x0,x1,y0):
            return {'text':t,'font':'NimbusRomNo9L-Regu','size':size,'flags':4,'color':0,'bbox':(x0,y0,x1,y0+9),'origin':(x0,y0+7+(0 if size<7 else 1.5))}
        def build(first_size):
            return [{'type':0,'lines':[{'bbox':(208,143,350,154),'dir':(1,0),'spans':[
                sp('1',first_size,208.4,211.3,143.5),sp('The Chinese University of Hong Kong',9,211.8,350.4,145)]}]}]
        blocks=build(6)
        islands=app.math_islands(blocks)
        self.assertEqual(len(islands),1)
        text=''.join(s['text'] for s in blocks[0]['lines'][0]['spans'])
        self.assertEqual(text,chr(app.ISLAND_BASE)+'The Chinese University of Hong Kong')
        seg=app.segment(text)
        self.assertNotIn('1The',seg['protected'])
        self.assertIn('The Chinese',seg['original'])
        # A same-size leading digit (list number) is not a marker.
        plain=build(9)
        self.assertEqual(app.math_islands(plain),[])

    def test_garbled_font_share_flags_untranslated_document(self):
        # IRLZ44N before 72c3183: 334 unmapped_glyph regions, 20 slots, body
        # coverage still 100%. Translated MCP9700 package pages stay below it.
        um=lambda n:{'kind':'unmapped_glyph','text':'x'*n}
        bad=app.garbled_text([um(4697),{'kind':'figure_text','text':'y'*900}],[{'source':'z'*2273}])
        self.assertTrue(bad['warning']);self.assertAlmostEqual(bad['share'],.674,places=3)
        ok=app.garbled_text([um(1892)],[{'source':'z'*26164}])
        self.assertFalse(ok['warning'])
        self.assertFalse(app.garbled_text([],[])['warning'])

    def test_cmex_control_code_delimiters_join_island_on_prose_row_with_islands(self):
        # 36539 p4: "segment, u_f = clip ( ... , 0, 1 )" - the CMEX10 big
        # parentheses are '\x00'/'\x01' in their own PDF lines next to a row
        # that already has an island (u_f); they stayed in the paragraph and
        # the whole paragraph was kept English as unmapped_glyph.
        def sp(t,font,size,x0,x1,y0,y1,base):
            return {'text':t,'font':font,'size':size,'flags':0,'color':0,'bbox':(x0,y0,x1,y1),'origin':(x0,base)}
        blocks=[{'type':0,'lines':[
            {'bbox':(58,703,136,714),'dir':(1,0),'spans':[sp('segment,','NimbusRomNo9L-Regu',10,58.5,94.3,703.2,713.2,710.8),
             sp(' u','CMMI10',10,94.3,102.5,703,713,710.8),sp('f','CMMI7',7,102.5,106.3,706.8,713.8,712.3),
             sp(' ','CMR10',10,106.3,110.4,704.5,714.5,712.3),sp('= clip','CMR10',10,110.4,136.4,703,713,710.8)]},
            {'bbox':(136,697,199,712),'dir':(1,0),'spans':[sp('\x00','CMEX10',10,136.4,141,702.1,712.1,702.7),
             sp(' (','CMR7',7,141,145.3,697.3,707.2,702.7),sp('c','CMMI7',7,145.3,148.9,700.2,707.2,705.7),sp(')','CMR7',7,195.9,199.1,700.2,707.2,705.7)]},
            {'bbox':(200,703,222,713),'dir':(1,0),'spans':[sp(',','CMMI10',10,200.3,203,703,713,710.8),sp(' 0','CMR10',10,203,211.3,703,713,710.8)]},
            {'bbox':(222,702,227,712),'dir':(1,0),'spans':[sp('\x01','CMEX10',10,222.4,227,702.1,712.1,702.7)]},
            {'bbox':(227,703,295,715),'dir':(1,0),'spans':[sp(', where the','NimbusRomNo9L-Regu',10,227,295,703.2,713.2,710.8)]}]}]
        app.math_islands(blocks)
        text=''.join(s['text'] for l in blocks[0]['lines'] for s in l['spans'])
        self.assertFalse(any(unicodedata.category(c)=='Cc' for c in text),ascii(text))
        self.assertIn('segment,',text)

    def test_tall_arrow_box_is_clamped_to_its_ink(self):
        # Adam's "←" (CMSY10 9.7pt) reports an 18pt box, 1.9x its size, and
        # swallowed the next algorithm row.
        span={'text':'←','size':9.7,'origin':(120.0,228.7),'bbox':(118.0,218.8,128.0,237.1)}
        line={'bbox':(118.0,218.8,128.0,237.1),'spans':[span]}
        ink={'blocks':[{'type':0,'lines':[{'spans':[{**span,'bbox':(118.0,222.0,128.0,227.0)}]}]}]}
        page=SimpleNamespace(get_text=lambda *a,**k:ink)
        app.tall_glyph_boxes(page,[{'type':0,'lines':[line]}])
        self.assertLess(span['bbox'][3],229)
        self.assertLess(line['bbox'][3]-line['bbox'][1],9.7*1.6)

    def test_stacked_script_digits_are_one_fraction_island(self):
        # GAN abstract: "equal to 1/2 everywhere", 1 and 2 in different PDF lines.
        def sp(t,font,size,x0,x1,y):
            return {'text':t,'font':font,'size':size,'flags':0,'color':0,'bbox':(x0,y[0],x1,y[1]),'origin':(x0,y[1]-2)}
        blocks=[{'type':0,'lines':[
            {'bbox':(144,355,259,367),'dir':(1,0),'spans':[sp('equal to','NimbusRomNo9L-Regu',10,144,251,(356.7,366.7)),sp('1','CMR7',7,255.2,259.2,(354.9,361.9))]},
            {'bbox':(255,357,468,370),'dir':(1,0),'spans':[sp('2','CMR7',7,255.2,259.2,(362.3,369.3)),sp(' everywhere.','NimbusRomNo9L-Regu',10,259.2,311,(356.7,366.7))]}]}]
        islands=app.math_islands(blocks)
        self.assertEqual(len(islands),1)
        text=' '.join(''.join(s['text'] for s in l['spans']) for l in blocks[0]['lines'])
        self.assertNotRegex(text,r'\d')

    def test_word_per_line_objects_join_into_rows(self):
        span=lambda t,x0,x1,y:{'text':t,'bbox':(x0,y,x1,y+10),'size':10,'font':'Arial','color':0,'flags':0}
        line=lambda t,x0,x1,y:{'bbox':(x0,y,x1,y+10),'dir':(1,0),'spans':[span(t,x0,x1,y)]}
        rows=app.visual_rows([line('The',0,17,0),line('LM35',26,51,0),line('series',59,86,0),line('next',0,20,11)])
        self.assertEqual(len(rows),2)
        self.assertEqual(app.body_text(rows[:1]),'The LM35 series')
        self.assertEqual(len(app.paragraph_parts(rows)),1)

    def test_loosely_spaced_lines_become_one_paragraph_slot(self):
        # At a 1.7 line pitch MuPDF opens a block per line; every line used to
        # be its own slot and sentences were translated in pieces.
        words=('Spacecraft-mounted robotic manipulators can support long-term operability of orbiting assets by conducting '
               'on-orbit assembly and repair. In this context, the integration of continuum robots promises safer operation. '*3).split()
        rows=[];cur=''
        for w in words:
            t=(cur+' '+w).strip()
            if pymupdf.get_text_length(t,fontsize=10)>200:rows.append(cur);cur=w
            else:cur=t
        rows.append(cur)
        with pymupdf.open() as d:
            p=d.new_page(width=300,height=600)
            p.insert_text((50,50),'Abstract',fontsize=10)
            for i,t in enumerate(rows):p.insert_text((50,50+17*(i+1)),t,fontsize=10)
            # Short stacked labels (legend entries) are not a paragraph.
            for i,t in enumerate(['Ours alpha','Base beta','Rand gamma']):p.insert_text((50,500+17*i),t,fontsize=10)
            slots,_=app.blocks_for_page(p,[])
        sources=[s['source'] for s in slots]
        self.assertGreater(len(rows),8)
        self.assertEqual(sources[0],'Abstract')
        self.assertEqual(sources[1],' '.join(words))
        self.assertEqual(sources[2:],['Ours alpha','Base beta','Rand gamma'])

    def test_tiny_label_touching_axis_tick_is_retained(self):
        with pymupdf.open() as d:
            p=d.new_page(width=300,height=300)
            p.insert_text((40,80),'Non-Image',fontsize=4);p.insert_text((36,77),'30',fontsize=5)
            slots,retained=app.blocks_for_page(p,[])
            self.assertFalse(any('Non-Image' in s['source'] for s in slots))
            self.assertTrue(any(r['kind']=='adjacent_math_collision' for r in retained))

    def test_invalid_font_mapping_is_retained_before_translation(self):
        with pymupdf.open() as d:
            p=d.new_page();p.insert_text((40,80),'Ordinary readable paragraph.')
            original=p.get_text
            def corrupt(kind,*args,**kwargs):
                result=original(kind,*args,**kwargs)
                if kind=='dict':result['blocks'][0]['lines'][0]['spans'][0]['text']='Text\x04\x15bad font'
                return result
            with patch.object(p,'get_text',side_effect=corrupt):
                slots,retained=app.blocks_for_page(p,[])
            self.assertEqual(slots,[])
            self.assertTrue(any(r['kind']=='unmapped_glyph' for r in retained))
        for c in ['\x00','\x04','\x7f','\x85']:
            with self.assertRaisesRegex(ValueError,'控制字符'):app.tex_escape('text'+c)

    def test_prose_does_not_erase_overlapping_adjacent_superscript(self):
        with pymupdf.open() as d:
            p=d.new_page()
            p.insert_text((40,80),'A useful description',fontsize=10)
            p.insert_text((42,71),'2',fontsize=6)
            r=p.search_for('2')[0]
            slots,retained=app.blocks_for_page(p,[{'kind':'isolate_formula','confidence':1,'bbox':list(r)}])
            # The erase strip runs through the prose line's centre only, so the
            # superscript survives redaction and the prose can be translated.
            s=next(x for x in slots if 'useful' in x['source'])
            p.add_redact_annot(app.erase_core(s['erase_rects'][0]),fill=None)
            p.apply_redactions(images=0,graphics=0)
            self.assertEqual(p.get_text().strip(),'2')

    def test_decimal_across_font_spans_is_restored_without_weakening_number_check(self):
        line={'bbox':[0,0,200,20],'spans':[
            {'text':'Threshold 0','font':'Arial','size':10,'color':0},
            {'text':'.','font':'Arial-Italic','size':10,'flags':2,'color':0},
            {'text':'55','font':'Arial','size':10,'color':0}]}
        s=app.styles.rich_segment([line],app.segment('Threshold 0.55'),app.segment)
        value=s['text'].replace('Threshold','阈值').replace('.', '。')
        self.assertEqual(app.translated_text(s,value),'阈值 0.55')
        with self.assertRaises(ValueError):app.translated_text(s,value+' 9')

    def test_rich_style_boundaries_and_numbers_are_validated(self):
        lines=[{'bbox':[10,10,200,25],'spans':[
            {'text':'Question: ','font':'Helvetica-Bold','flags':16,'color':0,'size':10},
            {'text':'Red detail','font':'Helvetica','flags':0,'color':0xff0000,'size':10}]},
            {'bbox':[10,25,200,40],'spans':[
            {'text':'Answer: ','font':'Helvetica-Bold','flags':16,'color':0,'size':10},
            {'text':'2.4','font':'Helvetica','flags':0,'color':0x00b050,'size':10}]}]
        s=app.styles.rich_segment(lines,app.segment(app.body_text(lines)),app.segment)
        value=s['text'].replace('Question:','问题：').replace('Red detail','红色细节').replace('Answer:','答案：')
        self.assertIn('\n',app.translated_text(s,value))
        runs=app.styles.decoded_runs(s,value)
        self.assertTrue(any(r['style']['color']==0xff0000 and '红色细节' in r['text'] for r in runs))
        self.assertTrue(any(r['style']['bold'] and '答案' in r['text'] for r in runs))
        phrases={k:s['text'][a:b].replace('Question:','问题：').replace('Red detail','红色细节').replace('Answer:','答案：')
                 for k,(a,b) in app.styles.phrase_ranges(s).items()}
        self.assertEqual(app.styles.assemble_phrases(s,phrases),value)
        self.assertEqual(app.styles.assemble_phrases(s,{**phrases,'context_order':list(phrases)}),value)
        self.assertEqual(app.styles.assemble_phrases(s,{'phrases':phrases,'context_order':list(phrases)}),value)
        with self.assertRaises(ValueError):app.styles.assemble_phrases(s,{**phrases,'unknown':'ignored?'})
        with self.assertRaises(ValueError):app.styles.assemble_phrases(s,{**phrases,'context_order':[]})
        with self.assertRaises(ValueError):app.styles.assemble_phrases(s,{})
        with self.assertRaises(ValueError):app.translated_text(s,value.replace('红色细节',''))
        with self.assertRaises(ValueError):app.translated_text(s,value.replace('@@P0@@','@@TEMP@@').replace('@@P1@@','@@P0@@').replace('@@TEMP@@','@@P1@@'))

    @unittest.skipUnless(shutil.which('xelatex') and shutil.which('latexmk'),'requires local TeX Live')
    def test_rich_styles_render_with_real_colors_and_bold(self):
        with tempfile.TemporaryDirectory(prefix='pdfzh-styles-') as folder:
            root=Path(folder);src=root/'styles.pdf';out=root/'out';out.mkdir()
            with pymupdf.open() as d:
                p=d.new_page(width=400,height=300)
                p.insert_text((30,60),'Question: ',fontname='hebo')
                p.insert_text((90,60),'Red detail',color=(1,0,0),fontname='heit')
                p.insert_text((30,85),'Answer: ',fontname='hebo')
                p.insert_text((90,85),'2.4',color=(0,176/255,80/255))
                d.save(src)
            latex.write_json(out/'layout.json',{'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'pages':[{'regions':[]}]})
            plan=app.prepare(src,out);cache={}
            for page in plan['pages']:
                for slot in page['slots']:
                    s=slot['segment'];cache[latex.digest(s['text'])]=s['text'].replace('Question:','问题：').replace('Red detail','红色细节').replace('Answer:','答案：')
            with patch.object(latex,'api_batch',side_effect=AssertionError('paid API forbidden')):
                report=app.compile_overlay(src,out,plan,cache)
            self.assertTrue(report['automated_checks_passed'])
            with pymupdf.open(report['output_files']['chinese']) as d:
                spans=[s for b in d[0].get_text('dict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans']]
                self.assertTrue(any('红色细节' in s['text'] and s['color']==0xff0000 for s in spans))
                self.assertTrue(any('问题' in s['text'] and s['flags'] & 16 for s in spans))
                self.assertTrue(any('2.4' in s['text'] and s['color']==0x00b050 for s in spans))

    def test_model_version_is_atomic_and_old_decimal_cache_is_reusable(self):
        source='Non-Image GPT-5.4'
        s=app.segment(source)
        self.assertEqual(s['protected'],['GPT-5.4'])
        old=app.segment(source,app.LEGACY_NATIVE_TOKEN)
        value='非图像 @@P0@@。@@P1@@'
        self.assertEqual(app.translated_text(old,value),'非图像 GPT-5.4')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            latex.write_json(root/'text-cache.json',{latex.digest(old['text']):value})
            app.reuse_previous_report(root,{'pages':[{'slots':[{'segment':s}]}]})
            cache=json.loads((root/'text-cache.json').read_text(encoding='utf8'))
            self.assertEqual(app.translated_text(s,cache[latex.digest(s['text'])]),'非图像 GPT-5.4')

    def test_month_conversion_does_not_allow_unrelated_extra_numbers(self):
        s=app.segment('June 2024.')
        self.assertEqual(app.translated_text(s,'@@P0@@ 年 6 月'),'2024. 年 6 月')
        for bad in ['@@P0@@ 年 7 月','@@P0@@ 年 6','@@P0@@ 年 6 月 6 月']:
            with self.assertRaises(ValueError):app.translated_text(s,bad)
        with self.assertRaises(ValueError):app.translated_text(app.segment('Value 5.4'),'数值 @@P0@@ 7')

    def test_roman_numeral_after_reference_word_may_become_arabic(self):
        s=app.segment('Ranges are in Table I and the rewards in Table II.')
        self.assertEqual(s['protected'],['II'])
        self.assertEqual(app.translated_text(s,'范围见表1，奖励见表@@P0@@。'),'范围见表1，奖励见表II。')
        self.assertEqual(app.translated_text(s,'范围见表I，奖励见表@@P0@@。'),'范围见表I，奖励见表II。')
        # Only the numeral after the reference word is exempt, once per occurrence.
        for bad in ['范围见表1，共1项，奖励见表@@P0@@。','范围见表1，共2项，奖励见表@@P0@@。','范围见表1和表1，奖励见表@@P0@@。']:
            with self.assertRaises(ValueError,msg=bad):app.translated_text(s,bad)
        f=app.segment('We use 3 of them in Figure I.')
        self.assertEqual(app.translated_text(f,'我们使用 @@P0@@ 个，见图1。'),'我们使用 3 个，见图1。')
        with self.assertRaises(ValueError):app.translated_text(f,'我们使用 @@P0@@ 个，见图1，共1项。')

    def test_number_word_may_become_arabic(self):
        # 36873 p1: "turning ones into alternating zeros and twos ... at most one"
        # came back as "把 1 变成交替的 0 和 2 ... 至多为 1"; the paragraph stayed English.
        s=app.segment('Turning ones into zeros and twos changes any sum by at most one (Figure 1).')
        self.assertEqual(app.translated_text(s,'把 1 变成 0 和 2，任何和的变化至多为 1（图 @@P0@@）。'),
                         '把 1 变成 0 和 2，任何和的变化至多为 1（图 1）。')
        app.translated_text(s,'把一变成零和二，任何和的变化至多为一（图 @@P0@@）。')
        # Only digits of words present in the source, once per occurrence.
        for bad in ['把 1 变成 0 和 2，任何和的变化至多为 1 或 1（图 @@P0@@）。','把 1 变成 0 和 3，至多为 1（图 @@P0@@）。']:
            with self.assertRaises(ValueError,msg=bad):app.translated_text(s,bad)
        # A protected source number still has to survive.
        with self.assertRaises(ValueError):app.translated_text(s,'把 1 变成 0 和 2，至多为 1（图）。')

    def test_fraction_with_three_prose_words_does_not_translate_numerator(self):
        with pymupdf.open() as d:
            p=d.new_page(width=500,height=300)
            p.insert_text((35,150),'RP x (R2+R1), RP = Parasitic Line Resistance',fontsize=10)
            r=p.search_for('R2+R1')[0]
            p.draw_line((r.x0,r.y1-1),(r.x1,r.y1-1))
            p.insert_text((r.x0+5,r.y1+10),'R1',fontsize=10)
            slots,retained=app.blocks_for_page(p,[])
            self.assertEqual([s['source'] for s in slots],['Parasitic Line Resistance'])
            self.assertTrue(any(r['kind']=='fraction' for r in retained))

    def test_merged_statistics_header_uses_subcolumns_below(self):
        with pymupdf.open() as d:
            p=d.new_page(width=400,height=300)
            for x,label in [(55,'Min'),(145,'Typ'),(245,'Max')]:p.insert_text((x,85),label)
            header=pymupdf.Rect(25,60,310,100)
            table=SimpleNamespace(rows=[SimpleNamespace(cells=[(25,100,110,130),(110,100,210,130),(210,100,310,130)])])
            columns=app.header_columns(p,header,table)
            self.assertEqual(len(columns),3)
            self.assertEqual(columns[1]['bbox'],[112,61,208,99])
            self.assertEqual(app.column_translations({'columns':columns,'translation':'最小值典型值最大值'}),['最小值','典型值','最大值'])

    def test_residual_words_are_word_erased_not_slot_rejected(self):
        # LoRA p19: a union span box of "Learning Rate 5E-04 5E-04 ..." skips
        # the short glyphs of the table note "rq = rv = 8"; after the line
        # strips the words survive inside the layout box. Word-level strips
        # through each word's centre remove them without touching the prose
        # on the next row, so the slot keeps its translation instead of a
        # retained_text_collision. Words of a plan-retained region must
        # survive: they are the only copy left on the page (Adam p2).
        with pymupdf.open() as d:
            p=d.new_page(width=400,height=200)
            p.insert_text((30,40),'Learning Rate 5E-04 5E-04 4E-04 4E-04',fontsize=9)
            # Short glyphs on a neighbouring line whose box ends up inside
            # the slot's union box (like rq/rv in LoRA's Table 9).
            p.insert_text((250,52),'rq = rv = 8',fontsize=4)
            p.insert_text((30,64),'Warmup ratio 0.1 0.1 0.1 0.1',fontsize=9)
            slots,retained=app.blocks_for_page(p,[])
            s=next(x for x in slots if 'Learning' in x['source'])
            for box in s['erase_rects']:
                p.add_redact_annot(app.erase_core(box),fill=None)
            p.apply_redactions(images=0,graphics=0)
            box=pymupdf.Rect(s['bbox'])
            hits=[w for w in p.get_text('words') if pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2) in box]
            if hits:
                for w in hits:
                    p.add_redact_annot(app.erase_core(w[:4]),fill=None)
                p.apply_redactions(images=0,graphics=0)
            self.assertFalse([w for w in p.get_text('words') if pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2) in box])
            # The untouched row below keeps every word at its place.
            self.assertTrue(p.search_for('Warmup'))
            self.assertTrue(p.search_for('0.1'))
        # A word the plan already keeps (overlapping_text) is neither erased
        # nor a collision: it is the only copy of that text on the page.
        with pymupdf.open() as d:
            p=d.new_page(width=400,height=200)
            p.insert_text((30,40),'The algorithm updates exponential moving averages.',fontsize=9)
            p.insert_text((30,52),'Note that the efficiency of algorithm 1 can be improved.',fontsize=9)
            slots,retained=app.blocks_for_page(p,[])
            s=next(x for x in slots if 'updates' in x['source'])
            other=next(x for x in slots if 'efficiency' in x['source'])
            slots.remove(other)
            retained.append({'kind':'overlapping_text','bbox':list(other['bbox']),'text':other['source']})
            for slot in slots:
                for box in slot.get('erase_rects',[slot['bbox']]):
                    p.add_redact_annot(app.erase_core(box),fill=None)
            p.apply_redactions(images=0,graphics=0)
            zones=[pymupdf.Rect(r['bbox']) for r in retained if r.get('bbox')]
            box=pymupdf.Rect(s['bbox'])
            # The engine's residual-word pass must leave the retained words
            # alone and report no collision. Simulate compile_overlay's loop.
            hits=[w for w in p.get_text('words') if pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2) in box]
            remaining=[w for w in hits if not any((z & pymupdf.Rect(w[:4])).get_area()>.5*pymupdf.Rect(w[:4]).get_area() for z in zones)]
            self.assertFalse(remaining)
            self.assertTrue(p.search_for('efficiency'))
            # compile_overlay's residual-word pass leaves plan-retained
            # words in place and reports no collision for this slot.
            import io, contextlib, tempfile, shutil, hashlib
            with tempfile.TemporaryDirectory(prefix='pdfzh-collision-') as folder:
                root=Path(folder);src=root/'t.pdf';out=root/'out';out.mkdir()
                with pymupdf.open() as d2:
                    p2=d2.new_page(width=400,height=200)
                    # Two paragraphs close enough that a union layout box
                    # covers the second one's words (like Adam p2).
                    p2.insert_text((30,40),'The algorithm updates exponential moving averages.',fontsize=9)
                    p2.insert_text((30,58),'Note that the efficiency of algorithm 1 can be improved upon by changing.',fontsize=9)
                    d2.save(src)
                latex.write_json(out/'layout.json',{'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
                    'pages':[{'regions':[]}]})
                plan=app.prepare(src,out)
                # Move the second paragraph into retained like the engine
                # does for overlapping_text, so its words are not erased.
                info=plan['pages'][0]
                self.assertEqual(len(info['slots']),2)
                moving=[x for x in info['slots'] if 'efficiency' in x['source']]
                for x in moving:
                    info['slots'].remove(x)
                    info['retained'].append({'kind':'overlapping_text','bbox':list(x['bbox']),'text':x['source']})
                cache={}
                for page in plan['pages']:
                    for slot in page['slots']:
                        value='该算法更新指数移动平均值。'
                        app.translated_text(slot['segment'],value)  # must validate
                        cache[latex.digest(slot['segment']['text'])]=value
                with patch.object(latex,'api_batch',side_effect=AssertionError('paid API forbidden')):
                    report=app.compile_overlay(src,out,plan,cache)
                collisions=[r for r in report['retained_regions'] if r['kind']=='retained_text_collision']
                self.assertFalse(collisions, [r.get('reason') for r in report['retained_regions']])
                with pymupdf.open(report['output_files']['chinese']) as cn:
                    text=cn[0].get_text()
                    self.assertIn('efficiency',text)
                    self.assertIn('算法',text,[r.get('kind') for r in report['retained_regions']])

    def test_locked_output_publishes_revision_without_changing_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=root/'new.pdf';dst=root/'old.pdf'
            src.write_bytes(b'new');dst.write_bytes(b'old')
            replace=os.replace
            def locked(a,b):
                if b==dst:raise PermissionError('open in reader')
                return replace(a,b)
            with patch.object(app.os,'replace',side_effect=locked):actual=app.publish_copy(src,dst)
            self.assertEqual(dst.read_bytes(),b'old')
            self.assertEqual(actual.read_bytes(),b'new')
            self.assertNotEqual(actual,dst)

    def test_pdf_quote_expansion_keeps_explicit_order_and_spacing(self):
        ops=[([3],b'TL'),(['first'],b"'"),([1,2,'next'],b'"'),([1,0,0,1,30,50],b'Tm')]
        self.assertEqual(app.expand_text_operators(ops),[
            ([3],b'TL'),([],b'T*'),(['first'],b'Tj'),([1],b'Tw'),([2],b'Tc'),
            ([],b'T*'),(['next'],b'Tj'),([1,0,0,1,30,50],b'Tm')])

    def test_retained_position_audit_rejects_shifted_values(self):
        with pymupdf.open() as a,pymupdf.open() as b:
            p=a.new_page();p.insert_text((50,100),'1.3 V')
            q=b.new_page();q.insert_text((50,80),'1.3 V')
            self.assertEqual(len(app.retained_position_errors(p,q,[])),2)

    def test_position_audit_ignores_regrouped_double_drawn_label(self):
        # Mask R-CNN draws figure labels twice at one spot; after any
        # redaction MuPDF reports them as one word "RoIAlignRoIAlign".
        box=(393.1,259.2,415.6,266.9)
        chars=[{'c':c,'bbox':(393.1+i*2.8,259.2,395.9+i*2.8,266.9)} for i,c in enumerate('RoIAlign')]
        def page(words,dy=0):
            moved=[{**c,'bbox':(c['bbox'][0],c['bbox'][1]+dy,c['bbox'][2],c['bbox'][3]+dy)} for c in chars]
            raw={'blocks':[{'type':0,'lines':[{'spans':[{'chars':moved},{'chars':moved}]}]}]}
            return SimpleNamespace(get_text=lambda kind,**k:words if kind=='words' else raw)
        source=page([(*box,'RoIAlign'),(*box,'RoIAlign')])
        self.assertEqual(app.retained_position_errors(source,page([(*box,'RoIAlignRoIAlign')]),[]),[])
        # Glyphs that really moved are still reported.
        self.assertTrue(app.retained_position_errors(source,page([(*box,'RoIAlignRoIAlign')],dy=3),[]))

    def test_soft_hyphen_reextracted_as_dash_is_not_a_shift(self):
        # ACS712 p10: "operat\xad" comes back as "operat-" after any redaction.
        box=(36,100,80,110)
        def page(word):
            raw={'blocks':[{'type':0,'lines':[{'spans':[{'chars':[{'c':word[-1],'bbox':(76,100,80,110)}]}]}]}]}
            return SimpleNamespace(get_text=lambda kind,**k:[(*box,word)] if kind=='words' else raw)
        self.assertEqual(app.retained_position_errors(page('operat\xad'),page('operat-'),[]),[])

    def test_tab_and_newline_are_not_unmapped_glyphs(self):
        # ACS712 p1: InDesign bullets are "▪\t", titles carry "\n".
        with pymupdf.open() as d:
            p=d.new_page()
            line={'bbox':(54,145,300,158),'dir':(1,0),'spans':[
                {'text':'▪\t','font':'ArialMT','size':10,'bbox':(54,145,62,158),'flags':0,'color':0,'origin':(54,156)},
                {'text':'Low-noise analog signal path for current sensing','font':'ArialMT','size':10,'bbox':(62,145,300,158),'flags':0,'color':0,'origin':(62,156)}]}
            with patch.object(p,'get_text',side_effect=lambda kind,**k:{'blocks':[{'type':0,'bbox':line['bbox'],'lines':[line]}]} if kind=='dict' else []):
                slots,retained=app.blocks_for_page(p,[])
            self.assertEqual([r['kind'] for r in retained if r['kind']=='unmapped_glyph'],[])
            self.assertEqual(len(slots),1)

    def test_zero_spacing_survives_redaction_rewrite(self):
        # ACS712 p11: the footer form resets "0 Tc", which MuPDF's redaction
        # rewrite drops as a default, so the page number's -0.074 Tc leaked
        # into the form and every footer word moved.
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'a.pdf'
            with pymupdf.open() as d:
                p=d.new_page(width=300,height=200);p.insert_text((10,10),'x')
                fx,_,_,_,font,*_=p.get_fonts()[0]
                form=d.get_new_xref()
                d.update_object(form,f'<</Type/XObject/Subtype/Form/BBox[0 0 300 200]/Resources<</Font<</{font} {fx} 0 R>>>>>>')
                d.update_stream(form,f'BT /{font} 10 Tf 0 Tc 50 50 Td (Allegro MicroSystems Road) Tj ET'.encode())
                d.update_stream(p.get_contents()[0],f'BT /{font} 10 Tf -2 Tc 50 150 Td (11) Tj ET q /Fm0 Do Q'.encode())
                d.xref_set_key(p.xref,'Resources',f'<</Font<</{font} {fx} 0 R>>/XObject<</Fm0 {form} 0 R>>>>')
                d.save(path)
            def words(pg):return [round(w[2],1) for w in pg.get_text('words') if w[1]>100]
            with pymupdf.open(path) as src:before=words(src[0])
            doc=app.normalized_document(path);page=doc[0]
            page.add_redact_annot(pymupdf.Rect(200,40,200.2,40.2),fill=None)
            page.apply_redactions(images=0,graphics=0,text=0)
            self.assertEqual(words(page),before)

    def test_rotated_stamp_survives_body_erasure(self):
        # 37297 p1: the arXiv side stamp overlaps a narrow left margin; erasing
        # body lines removed its "27"/"2026" and the page check blamed them.
        def build():
            d=pymupdf.open();p=d.new_page(width=300,height=400)
            p.insert_text((40,380),'arXiv:2610.37297v1 27 Oct 2026',fontsize=20,rotate=90)
            for y in range(60,380,12):p.insert_text((43,y),'limited by their sequential nature',fontsize=10)
            return d
        stamp=lambda pg:[''.join(s['text'] for s in l['spans']) for b in pg.get_text('dict')['blocks']
                         for l in b.get('lines',[]) if l['dir']!=(1.0,0.0)]
        with build() as d,build() as src:
            page=d[0]
            slots=[{'bbox':list(l['bbox'])} for b in page.get_text('dict')['blocks'] for l in b['lines'] if l['dir']==(1.0,0.0)][5:10]
            for s in slots:page.add_redact_annot(app.erase_core(s['bbox']),fill=None)
            page.apply_redactions(images=0,graphics=0,text=0)
            self.assertNotEqual(stamp(page),['arXiv:2610.37297v1 27 Oct 2026'])
            app.restore_rotated(page,src[0],slots)
            self.assertEqual(stamp(page),['arXiv:2610.37297v1 27 Oct 2026'])

    def test_islands_do_not_join_across_gutter_into_another_block(self):
        # 37297 p4: a body formula "P" ending at x=289 absorbed the algorithm
        # box line number "17" at x=293 (another block), so the body paragraph
        # read "17 Function FollowContour..." and failed the number check.
        def span(text,font,size,x0,x1,y0=332,y1=342):
            return {'text':text,'font':font,'size':size,'bbox':(x0,y0,x1,y1),'origin':(x0,y1-2),'flags':0,'color':0}
        body={'type':0,'bbox':(38,332,289,342),'lines':[{'bbox':(38,332,289,342),'dir':(1,0),'spans':[
            span('neighbor is labeled ','CMR10',10,38,280),span('P','CMMI10',10,280,289)]}]}
        algo={'type':0,'bbox':(293,330,516,341),'lines':[{'bbox':(293,330,516,341),'dir':(1,0),'spans':[
            span('17','CMBX7',7,293,302,333,340),span(' Function FollowContour','CMBX10',10,302,400),span('P','CMMI10',10,400,410)]}]}
        islands=app.math_islands([body,algo])
        self.assertTrue(all(not(i['bbox'][0]<290 and i['bbox'][2]>292) for i in islands))
        self.assertEqual([l['bbox'][2] for l in body['lines']],[289])

    def test_computer_modern_prose_rows_do_not_merge_into_neighbour_row(self):
        # 37297 p6: a paper whose body text is CMR10 (a "weak" math font) has
        # rows with no text-font span; each became a detached-formula island and
        # the row under a script-size "end" was absorbed into the row above, so
        # "other thread does." came before "end). We prove ..." in the source.
        def sp(t,font,size,x0,x1,y0,y1):
            return {'text':t,'font':font,'size':size,'flags':0,'color':0,'bbox':(x0,y0,x1,y1),'origin':(x0,y1-2)}
        a={'bbox':(375.3,389.9,557.7,402),'dir':(1,0),'spans':[
            sp('end','CMR7',7,375.3,387.6,395.1,402),sp(').','CMR10',10,388.1,394.8,389.9,399.9),
            sp(' We prove that ','CMR10',10,394.8,465.1,389.9,399.9),sp(' T','CMMI10',10,465.1,475,389.9,399.9),
            sp('k','CMMI7',7,475,479.2,393.7,400.7),sp(' claims and no','CMR10',10,479.2,557.7,389.9,399.9)]}
        b={'bbox':(306.6,401.9,386.1,411.8),'dir':(1,0),'spans':[sp('other thread does.','CMR10',10,306.6,386.1,401.9,411.8)]}
        blocks=[{'type':0,'bbox':(306.6,389.9,557.7,411.8),'lines':[a,b]}]
        app.math_islands(blocks)
        text=[''.join(s['text'] for s in l['spans']) for l in blocks[0]['lines']]
        self.assertEqual(len(text),2)
        self.assertEqual(text[1],'other thread does.')
        self.assertNotIn('other',text[0])

    def test_computer_modern_script_tails_of_two_rows_do_not_swap(self):
        # 37297 p7: "i_start^k' - 1. The" / "... - 1 only." are the tails of two
        # prose rows, set as their own PDF lines under a superscript. Both
        # were merged into one island and came back on the lower row, in merge
        # order: "... only.start -1. The Thus, ...".
        def sp(t,font,size,x0,x1,base):
            return {'text':t,'font':font,'size':size,'flags':0,'color':0,'bbox':(x0,base-size*.8,x1,base+size*.2),'origin':(x0,base)}
        def line(box,*spans):return {'bbox':box,'dir':(1,0),'spans':list(spans)}
        row1=line((57.5,478.7,237.9,491.4),sp('In all cases, the trace reaches a row','CMR10',10,57.5,203,489.2),
            sp(' i < i','CMMI10',10,203,220,489.2),sp('k','CMMI7',7,220,230,485.6),sp('′','CMSY5',5,230,237.9,482.6))
        tail1=line((231.3,481.3,288.7,493.7),sp('start','CMMI7',7,231.3,250,491.7),sp(' ','CMSY10',10,250,253,491.7),
            sp('−','CMSY10',10,253,261,489.2),sp('1.','CMR10',10,261,272,489.2),sp(' ','CMR10',10,272,275,489.2),sp('The','CMR10',10,275,288.7,489.2))
        row2=line((57.5,490.6,235.1,503.4),sp('exception strictly allows contact with','CMR10',10,57.5,200,501.2),
            sp(' i','CMMI10',10,200,212,501.2),sp('k','CMMI7',7,212,222,497.6),sp('′','CMSY5',5,222,235.1,494.6))
        tail2=line((228.5,493.3,288.7,505.7),sp('start','CMMI7',7,228.5,248,503.7),sp(' ','CMSY10',10,248,251,503.7),
            sp('−','CMSY10',10,251,259,501.2),sp('1','CMR10',10,259,267,501.2),sp(' ','CMR10',10,267,270,501.2),sp('only.','CMR10',10,270,288.7,501.2))
        row3=line((57.5,504.9,236.4,516.2),sp('Thus, the condition fails, and','CMR10',10,57.5,200,513.1),
            sp(' T','CMMI10',10,200,212,513.1),sp('k','CMMI7',7,212,222,514.6),sp('′','CMSY5',5,222,236.4,512.6))
        blocks=[{'type':0,'bbox':(57.5,478.7,237.9,491.4),'lines':[row1]},
                {'type':0,'bbox':(57.5,481.3,288.7,503.4),'lines':[tail1,row2]},
                {'type':0,'bbox':(57.5,493.3,288.7,516.2),'lines':[tail2,row3]}]
        app.math_islands(blocks)
        texts=[''.join(s['text'] for s in l['spans']) for b in blocks for l in b['lines']]
        self.assertTrue(any('The' in t for t in texts))
        self.assertFalse(any('The' in t and 'only.' in t for t in texts))

    def test_computer_modern_formula_row_with_operator_name_stays_island(self):
        # DPO p6: "r(x,y) = beta log pi/pi_ref" has a body-size CMR10 "log"; that made
        # the whole row prose, so "log" was sent to the model and came back "日志".
        # Prose rows from word-per-line PDFs (37043 p2) still stay text.
        def sp(t,font,size,x0,x1,y0,y1):
            return {'text':t,'font':font,'size':size,'flags':0,'color':0,'bbox':(x0,y0,x1,y1),'origin':(x0,y1-2)}
        def line(*spans):
            return {'bbox':(spans[0]['bbox'][0],spans[0]['bbox'][1],spans[-1]['bbox'][2],spans[0]['bbox'][3]),'dir':(1,0),'spans':list(spans)}
        prose=line(sp('Theorem 1. Under mild assumptions, all rewards','Times-Roman',10,100,300,100,110))
        formula=line(sp('r','CMMI10',10,100,106,120,130),sp('=','CMR10',10,108,116,120,130))
        log=line(sp('log','CMR10',10,118,134,120,130))
        tail=line(sp('for some model','Times-Roman',10,150,230,120,130))
        blocks=[{'type':0,'bbox':(100,100,300,130),'lines':[prose,formula,log,tail]}]
        app.math_islands(blocks)
        self.assertNotIn('log',''.join(s['text'] for s in blocks[0]['lines'][2]['spans']))
        # `out` p4: "Append_K(B_{t-1})" is an operator-like identifier: one long word is not prose.
        ident=line(sp('Append','CMR10',10,100,140,120,130),sp('K','CMMI7',7,140,145,123,130),sp('(','CMR10',10,145,150,120,130))
        blocks=[{'type':0,'bbox':(100,100,300,130),'lines':[prose,ident,tail]}]
        app.math_islands(blocks)
        self.assertNotIn('Append',''.join(s['text'] for s in blocks[0]['lines'][1]['spans']))
        words=[line(sp(w,'LMRoman10-Regular',10,100+40*i,130+40*i,120,130)) for i,w in enumerate(['model','for','a','space'])]
        blocks=[{'type':0,'bbox':(100,100,300,130),'lines':[prose]+words}]
        app.math_islands(blocks)
        self.assertEqual([l['spans'][0]['text'] for l in blocks[0]['lines'][1:]],['model','for','a','space'])

    def test_not_slash_fuses_with_following_relation(self):
        # 37297 p6: "Tk' \not= Tk" is a zero-width CMSY slash, a space glyph, then
        # "="; the loose slash stayed after Tk' and the translation showed "=".
        def sp(t,font,size,x0,x1):
            return {'text':t,'font':font,'size':size,'flags':0,'color':0,'bbox':(x0,544.7,x1,554.7),'origin':(x0,552.7)}
        line={'bbox':(407,544.7,456,555),'dir':(1,0),'spans':[
            sp('T','CMMI10',10,413.5,419.4),sp('\u2032','CMSY5',5,423.8,426.0),sp('\u0338','CMSY10',10,426.0,426.0),
            sp(' ','CMR10',10,426.0,432.5),sp('=','CMR10',10,432.5,440.2),sp(' T','CMMI10',10,440.2,451.5)]}
        app.merge_negation([{'type':0,'bbox':line['bbox'],'lines':[line]}])
        self.assertEqual([s['text'] for s in line['spans']],['T','\u2032',' \u2260',' T'])
        self.assertEqual(line['spans'][2]['bbox'][0],426.0)
        self.assertEqual(line['spans'][2]['bbox'][2],440.2)
        # \not before a letter has no precomposed form; \in -> \notin; far apart stays.
        for base,fused in (('\u2208','\u2209'),('\u2264','\u2270')):
            l={'bbox':(0,544,50,555),'dir':(1,0),'spans':[sp('\u0338','CMSY10',10,10,10),sp(base,'CMSY10',10,10,18)]}
            app.merge_negation([{'type':0,'bbox':l['bbox'],'lines':[l]}])
            self.assertEqual([s['text'] for s in l['spans']],[fused])
        l={'bbox':(0,544,50,555),'dir':(1,0),'spans':[sp('\u0338','CMSY10',10,10,10),sp('a','CMMI10',10,10,18)]}
        app.merge_negation([{'type':0,'bbox':l['bbox'],'lines':[l]}])
        self.assertEqual(len(l['spans']),2)

    def test_comma_between_merged_islands_joins_the_island(self):
        # 37178 p5: "[Z,E,x]^p , [Z,E,x]^q" - two coloured formulas merged into one
        # island while the text-font comma between them stayed behind inside the
        # island box: never erased, so the whole paragraph kept English.
        def sp(t,font,size,x0,x1):
            return {'text':t,'font':font,'size':size,'flags':0,'color':0,'bbox':(x0,177.2,x1,187.2),'origin':(x0,185)}
        first={'bbox':(10,177.2,36,187.2),'dir':(1,0),'spans':[sp('Let ','NimbusRomNo9L-Regu',10,10,30),sp('x','CMMI10',10,30,36)]}
        second={'bbox':(36.3,177.2,58,187.2),'dir':(1,0),'spans':[sp(',','NimbusRomNo9L-Regu',10,36.3,38.8),sp(' y','CMMI10',10,39,45),sp(' are','NimbusRomNo9L-Regu',10,45,58)]}
        blocks=[{'type':0,'bbox':(10,177.2,58,187.2),'lines':[first,second]}]
        islands=app.math_islands(blocks)
        self.assertEqual(len(islands),1)
        self.assertTrue(any(m[0]==36.3 for m in islands[0]['members']))
        rest=[s['text'] for l in blocks[0]['lines'] for s in l['spans'] if ord(s['text'][:1] or ' ')<app.ISLAND_BASE and s['text'].strip()]
        self.assertEqual(rest,['Let ',' are'])
    def test_pseudo_code_rows_stay_separate_slots(self):
        # Adam p2: each Require/step row of Algorithm 1 must keep its own line
        # and box; merging them into one paragraph reflows rows onto each other.
        line=lambda y,text,x1=260:{'bbox':(110,y,x1,y+12),'dir':(1,0),'spans':[
            {'text':text,'size':9.7,'bbox':(110,y,x1,y+12),'flags':0,'color':0,'font':'NimbusRomNo9L-Regu'}]}
        rows=[line(0,'Require: α: Stepsize'),
              line(12,'m0 ← 0 (Initialize 1st moment vector)'),
              line(24,'while θt not converged do'),
              line(36,'t ← t + 1'),
              line(48,'end while',180),
              line(60,'return θt (Resulting parameters)')]
        self.assertEqual(len(app.paragraph_parts(rows)),6)
        self.assertTrue(app.pseudo_code_line(rows[1]))
        self.assertFalse(app.pseudo_code_line({'bbox':(10,0,390,10),'dir':(1,0),'spans':[
            {'text':'A normal sentence that happens to mention (something) at the end.','size':10,'bbox':(10,0,390,10)}]}))
        # End-to-end through blocks_for_page: each row of an algorithm box
        # becomes its own slot (not merged into a paragraph).
        with pymupdf.open() as d:
            p=d.new_page(width=500,height=300)
            # Adam's Require block has no arrows; two Require rows mark it.
            p.insert_text((110,80),'Require: a: Stepsize, the base step size used every iteration',fontsize=9)
            p.insert_text((110,93),'Require: f: Stochastic objective function with parameters',fontsize=9)
            p.insert_text((110,106),'Require: t: Initial parameter vector of the optimisation',fontsize=9)
            p.insert_text((110,119),'while not converged do iterate the whole procedure again',fontsize=9)
            slots,_=app.blocks_for_page(p,[])
            sources=[s['source'] for s in slots]
            self.assertEqual(len(sources),4)
        # Ordinary prose with parentheses and "for"/"if" openers stays merged.
        with pymupdf.open() as d:
            p=d.new_page(width=500,height=300)
            p.insert_text((110,80),'We train the network with Adam (see Section 2) and',fontsize=9)
            p.insert_text((110,93),'for every task we report the mean (over five seeds)',fontsize=9)
            p.insert_text((110,106),'if the variance is large we also show the error (Table 3)',fontsize=9)
            slots,_=app.blocks_for_page(p,[])
            self.assertEqual(len(slots),1)
        with pymupdf.open() as d:
            p=d.new_page(width=500,height=300)
            p.insert_text((110,80),'Require: a: Stepsize, the base step size used every iteration',fontsize=9)
            p.insert_text((110,93),'Require: f: Stochastic objective function with parameters',fontsize=9)
            p.insert_text((110,106),'Require: t: Initial parameter vector of the optimisation',fontsize=9)
            p.insert_text((110,119),'Stepsize schedule follows the original paper defaults',fontsize=9)
            slots,_=app.blocks_for_page(p,[])
            sources=[s['source'] for s in slots]
            self.assertGreaterEqual(len(sources),3)
            self.assertTrue(any('base step size' in s and 'Stochastic' not in s for s in sources))

    def test_dingbat_bullets_start_new_items(self):
        line=lambda y,text,dingbat:{'bbox':(10,y,200,y+10),'spans':[
            {'text':'\U000f0000','size':10,'bbox':(10,y,15,y+10),'members':[[10,y,15,y+10]],'dingbat':dingbat},
            {'text':text,'size':10,'bbox':(17,y,200,y+10),'flags':0,'color':0,'font':'ArialMT'}]}
        runs=app.continuation_runs([line(0,'Off',True),line(11,'On',True)])
        self.assertEqual(len(runs),2)
        parts=app.paragraph_parts([line(0,'16 LED drivers, each output programmable',True),line(11,'1 MHz bus interface',True)])
        self.assertEqual(len(parts),2)

    def test_uppercase_headings_are_text_not_model_identifiers(self):
        self.assertTrue(app.prose('ELECTRICAL CHARACTERISTICS'))
        self.assertTrue(app.prose('FEATURES'))
        s=app.segment('AMS1117 CMOS OUTPUT VOLTAGE VRIPPLE')
        self.assertIn('OUTPUT VOLTAGE',s['text'])
        for name in ['AMS1117','CMOS','VRIPPLE']:self.assertIn(name,s['protected'])
        self.assertFalse(app.prose('VRIPPLE ≤ 3V'))
        self.assertFalse(app.prose('TO-252'))
        self.assertFalse(app.prose('IN OUT A'))
        s=app.segment('THERMAL RESISTANCE (JUNCTION-TO-AMBIENT), TEMPERATURE (°C), CURRENT (A)')
        self.assertIn('JUNCTION-TO-AMBIENT',s['text'])
        self.assertIn('°C',s['protected']);self.assertIn('A',s['protected'])

    def test_borderless_table_does_not_discard_adjacent_body(self):
        with pymupdf.open() as d:
            p=d.new_page(width=400,height=400)
            p.insert_text((25,60),'ABSOLUTE MAXIMUM RATINGS')
            p.insert_text((25,74),'Power Dissipation')
            p.insert_text((210,74),'Internally limited')
            p.insert_text((25,88),'Input Voltage')
            p.insert_text((210,88),'15V')
            slots,_=app.blocks_for_page(p,[{'kind':'table','confidence':1,'bbox':[20,64,310,94]}])
            alltext=' '.join(s['source'] for s in slots)
            for word in ['ABSOLUTE','Dissipation','Internally','Voltage']:self.assertIn(word,alltext)

    def test_table_intersection_only_excludes_covered_lines(self):
        with pymupdf.open() as d:
            p=d.new_page(width=400,height=400)
            # One PDF text block crosses a protected formula region.
            p.insert_text((25,80),'First sentence\nProtected formula\nLast sentence',fontsize=10)
            slots,retained=app.blocks_for_page(p,[{'kind':'isolate_formula','confidence':1,'bbox':[20,82,200,97]}])
            text=' '.join(s['source'] for s in slots)
            self.assertIn('First sentence',text);self.assertIn('Last sentence',text)
            self.assertNotIn('Protected formula',text)

    def test_figure_region_keeps_labels_and_body_outside_still_translates(self):
        with pymupdf.open() as d:
            p=d.new_page(width=400,height=400)
            p.insert_text((50,60),'Vision Transformer',fontsize=8)  # label inside figure
            p.insert_text((25,180),'The quick brown fox jumps over the lazy dog.',fontsize=10)  # body outside
            slots,retained=app.blocks_for_page(p,[{'kind':'figure','confidence':0.95,'bbox':[40,40,200,100]}])
            text=' '.join(s['source'] for s in slots)
            self.assertNotIn('Vision Transformer',text)
            self.assertIn('quick brown fox',text)
            fig=[r for r in retained if r['kind']=='figure_text']
            self.assertEqual(len(fig),1)
            self.assertIn('Vision Transformer',fig[0]['text'])

    def test_caption_outside_figure_is_untouched(self):
        with pymupdf.open() as d:
            p=d.new_page(width=400,height=400)
            p.insert_text((50,60),'MLP Head',fontsize=8)  # inside figure
            p.insert_text((25,120),'Figure 1: Model architecture overview.',fontsize=10)  # caption below figure
            slots,retained=app.blocks_for_page(p,[{'kind':'figure','confidence':0.95,'bbox':[40,40,200,100]}])
            text=' '.join(s['source'] for s in slots)
            self.assertIn('Figure 1: Model architecture overview.',text)
            self.assertNotIn('MLP Head',text)

    def test_formula_prose_and_vertical_labels_are_selectable(self):
        with pymupdf.open() as d:
            p=d.new_page(width=400,height=400)
            p.insert_text((50,120),'CURRENT VOLTAGE',rotate=90,fontsize=8)
            p.insert_text((90,170),'VIN = 5V (Note 4)',fontsize=10)
            slots,_=app.blocks_for_page(p,[])
            self.assertTrue(any(s.get('rotation')==90 for s in slots))
            self.assertTrue(any('Note' in s['source'] and s['kind']=='formula_prose' for s in slots))
            self.assertFalse(any('VIN' in s['source'] for s in slots))

    def test_wrapped_short_label_keeps_context_and_two_line_box(self):
        with pymupdf.open() as d:
            p=d.new_page(width=300,height=300)
            p.insert_text((50,80),'TAB IS\nOUTPUT',fontsize=8)
            slots,_=app.blocks_for_page(p,[])
            self.assertEqual(len(slots),1)
            self.assertEqual(slots[0]['source'],'TAB IS OUTPUT')
            self.assertGreater(slots[0]['bbox'][3]-slots[0]['bbox'][1],12)

    def test_parameter_tokens_are_immutable(self):
        s=app.segment('Supply voltage 2.2V to 5.5V, RT9013, accuracy 2%.')
        self.assertNotIn('2.2V',s['text'])
        self.assertNotIn('RT9013',s['text'])
        translated=app.translated_text(s,s['text'])
        self.assertEqual(translated,s['original'])
        with self.assertRaises(ValueError):app.translated_text(s,s['text'].replace('@@P0@@','3.3V'))

    def test_table_numeric_cells_never_selected(self):
        with pymupdf.open() as d:
            p=d.new_page(width=300,height=300)
            for x in [20,150,280]:p.draw_line((x,70),(x,130))
            for y in [70,100,130]:p.draw_line((20,y),(280,y))
            p.insert_text((25,87),'Parameter');p.insert_text((155,87),'Value')
            p.insert_text((25,117),'Supply voltage');p.insert_text((155,117),'5.5')
            slots,retained=app.blocks_for_page(p,[{'kind':'table','confidence':1,'bbox':[20,70,280,130]}])
            self.assertEqual(len(slots),3)
            self.assertFalse(any(s['source']=='5.5' for s in slots))

    def test_signed_values_and_units_are_protected(self):
        s=app.segment('Ambient temperature -40°C to +85°C, current 5 mA and gain -2%.')
        for token in ['-40°C','+85°C','mA','-2']:
            self.assertIn(token,s['protected'])
        self.assertEqual(app.translated_text(s,s['text']),s['original'])

    def test_phantom_table_edge_cannot_split_an_english_word(self):
        with pymupdf.open() as d:
            p=d.new_page(width=300,height=300)
            p.insert_text((30,80),'Dimensions In Inches')
            fake=SimpleNamespace(bbox=(20,60,200,90),rows=[SimpleNamespace(cells=[
                (20,60,105,90),(105,60,200,90)])])
            cells=app.validated_table_cells(p,fake)
            self.assertEqual(len(cells),1)
            self.assertEqual(p.get_text(clip=cells[0]).strip(),'Dimensions In Inches')

    @unittest.skipUnless(os.name=='nt' and (app.HERE/'.venv-next/Scripts/python.exe').is_file(),'Windows launcher requires installed environment')
    def test_windows_launchers_show_help(self):
        for name in ['pdfzh-nosource.cmd','pdfzh-latex.cmd']:
            result=subprocess.run([os.environ.get('COMSPEC','cmd.exe'),'/d','/c',str(app.HERE/name),'--help'],
                capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf8',errors='replace'))
            self.assertIn(b'usage:',result.stdout)

    def test_missing_typesetter_stops_before_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'input.pdf';src.write_bytes(b'%%EOF')
            with patch.object(app,'check_typesetting_environment',side_effect=RuntimeError('missing TeX')), \
                 patch.object(latex,'build_translation') as translate,patch.object(app,'prepare') as prepare:
                with self.assertRaisesRegex(RuntimeError,'missing TeX'):
                    app.main([str(src),'--outdir',str(Path(folder)/'out')])
                translate.assert_not_called();prepare.assert_not_called()

    def test_cache_remasking_preserves_signs_repeated_values_and_rejects_loss(self):
        s=app.segment('Voltage -40V to +85V, current 5 mA and 5 mA.')
        value='电压 -40V 至 +85V，电流 5 mA 和 5 mA。'
        masked=app.remask_translation(s,value)
        self.assertEqual(app.translated_text(s,masked),value)
        with self.assertRaises(ValueError):app.remask_translation(s,value.replace('-40V','40V'))

    def test_analyze_only_never_calls_api(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'x.pdf';out=root/'output';out.mkdir()
            with pymupdf.open() as d:
                p=d.new_page();p.insert_text((50,100),'Supply voltage description with enough native text.');d.save(src)
            (out/'layout.json').write_text(json.dumps({'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
                'pages':[{'regions':[]}]}))
            with patch.object(latex,'build_translation') as translate:
                app.main([str(src),'--outdir',str(out),'--analyze-only'])
                translate.assert_not_called()
            self.assertTrue((out/'structure.json').exists())

    def test_scan_is_rejected_instead_of_reported_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'scan.pdf'
            with pymupdf.open() as d:d.new_page();d.save(src)
            (root/'layout.json').write_text(json.dumps({'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
                'pages':[{'regions':[]}]}))
            with self.assertRaisesRegex(ValueError,'OCR'):app.prepare(src,root)

    @unittest.skipUnless(shutil.which('xelatex') and shutil.which('latexmk'),'requires local TeX Live')
    def test_two_page_offline_pipeline_preserves_graphics_numbers_and_original(self):
        """Real TeX build, no sample-specific dimensions, no network or LLM."""
        with tempfile.TemporaryDirectory(prefix='pdfzh-regression-') as folder:
            root=Path(folder);src=root/'independent.pdf';out=root/'output';out.mkdir()
            with pymupdf.open() as d:
                for number in [1,2]:
                    p=d.new_page(width=360,height=440)
                    p.insert_text((25,55),'Hardware description')
                    p.insert_text((25,410),'June 2024.')
                    p.insert_text((25,80),f'Supply voltage is {number}.5V.')
                    for x in [20,170,330]:p.draw_line((x,110),(x,170))
                    for y in [110,140,170]:p.draw_line((20,y),(330,y))
                    for point,text in [((25,127),'Parameter'),((175,127),'Value'),
                                       ((25,157),'Operating voltage'),((175,157),'5.5')]:
                        p.insert_text(point,text)
                    p.draw_rect((50,220,100,250))
                    p.draw_line((100,235),(200,235))
                    p.insert_text((65,240),'Driver',fontsize=8)
                    p.insert_text((25,380),'Output voltage',fontsize=8,rotate=90)
                    p.insert_text((50,310),'RP x (R2+R1), RP = Parasitic Line Resistance',fontsize=8)
                    math=p.search_for('R2+R1')[0]
                    p.draw_line((math.x0,math.y1-1),(math.x1,math.y1-1))
                    p.insert_text((math.x0+4,math.y1+8),'R1',fontsize=8)
                d.save(src)
            regions=[{'kind':'table','confidence':1,'bbox':[20,110,330,170]}]
            latex.write_json(out/'layout.json',{'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
                'pages':[{'regions':regions},{'regions':regions}]})
            plan=app.prepare(src,out)
            known={'Hardware description':'硬件说明 ∈ ≪ ≫ 𝜋𝜃𝑥★','June @@P0@@':'@@P0@@ 年 6 月','Parameter':'参数','Value':'数值',
                   'Operating voltage':'工作电压','Driver':'驱动程序','Output voltage':'输出电压',
                   'Parasitic Line Resistance':'寄生线路电阻'}
            cache={}
            for page in plan['pages']:
                for s in page['slots']:
                    source=s['segment']['text']
                    value=known.get(source,source.replace('Supply voltage is','供电电压为'))
                    cache[latex.digest(source)]=value
            latex.write_json(out/'text-cache.json',cache)
            with patch.object(latex,'api_batch',side_effect=AssertionError('paid API forbidden')) as api, \
                 patch('urllib.request.urlopen',side_effect=AssertionError('network forbidden')):
                app.main([str(src),'--outdir',str(out),'--offline'])
                api.assert_not_called()
            q=json.loads((out/'qa.json').read_text(encoding='utf8'))
            self.assertTrue(q['automated_checks_passed'])
            self.assertEqual(q['this_run_translation_usage'],{})
            self.assertEqual(q['missing_numeric_tokens'],[])
            self.assertEqual(q['extra_numeric_tokens'],[])
            self.assertTrue(any(s.get('rotation')==90 for s in q['slots']))
            self.assertTrue(any('Driver' in r['text'] for r in q['untranslated_text_candidates']))
            self.assertTrue(any(r['kind']=='translation_overflow' for r in q['retained_regions']))
            self.assertGreaterEqual(q['min_font'],5.5)
            with pymupdf.open(src) as original, pymupdf.open(out/'independent-中文-无源码试验.pdf') as cn, \
                 pymupdf.open(out/'independent-中英-无源码试验.pdf') as dual:
                self.assertEqual(len(dual),2)
                for i in range(2):
                    self.assertEqual(dual[i].rect.width,720)
                    self.assertIn('硬件说明',cn[i].get_text())
                    for symbol in '∈≪≫𝜋𝜃𝑥★':self.assertIn(symbol,cn[i].get_text())
                    self.assertIn('Driver',cn[i].get_text())
                    self.assertIn('输出电压',cn[i].get_text())
                    self.assertFalse(cn[i].search_for('Output voltage'))
                    self.assertIn('寄生线路电阻',cn[i].get_text())
                    for token in ['R2+R1','R1']:
                        for expected in original[i].search_for(token):
                            self.assertTrue(any(max(abs(a-b) for a,b in zip(expected,actual))<.5
                                for actual in cn[i].search_for(token)))
                    self.assertGreaterEqual(len(cn[i].get_drawings()),len(original[i].get_drawings()))
                    a=original[i].get_pixmap()
                    b=dual[i].get_pixmap(clip=pymupdf.Rect(0,0,360,440))
                    self.assertEqual(a.samples,b.samples)

    def two_page_job(self,root):
        src=root/'two.pdf';out=root/'out';out.mkdir()
        with pymupdf.open() as d:
            for n in (1,2):
                p=d.new_page(width=360,height=300);p.insert_text((25,60),f'Supply voltage is {n}.5V.')
            d.save(src)
        latex.write_json(out/'layout.json',{'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
            'pages':[{'regions':[]},{'regions':[]}]})
        return src,out

    @unittest.skipUnless(shutil.which('xelatex') and shutil.which('latexmk'),'requires local TeX Live')
    def test_network_failure_still_publishes_with_original_text(self):
        with tempfile.TemporaryDirectory() as folder:
            src,out=self.two_page_job(Path(folder))
            with patch.object(latex,'api_batch',side_effect=OSError('offline')),patch.object(latex.time,'sleep'), \
                 patch.object(app,'check_typesetting_environment'):
                app.main([str(src),'--outdir',str(out)])
            q=json.loads((out/'qa.json').read_text(encoding='utf8'))
            self.assertTrue(Path(q['output_files']['bilingual']).is_file())
            self.assertIn('translation_check_failed',q['retained_by_kind'])
            with pymupdf.open(q['output_files']['chinese']) as cn:self.assertIn('Supply voltage',cn[0].get_text())

    @unittest.skipUnless(shutil.which('xelatex') and shutil.which('latexmk'),'requires local TeX Live')
    def test_failed_page_check_restores_only_that_page(self):
        with tempfile.TemporaryDirectory() as folder:
            src,out=self.two_page_job(Path(folder))
            plan=app.prepare(src,out)
            cache={latex.digest(s['segment']['text']):s['segment']['text'].replace('Supply voltage is','供电电压为')
                   for p in plan['pages'] for s in p['slots']}
            real=latex.compile_project;calls=[]
            def corrupt(project,main):
                pdf,w,c=real(project,main);calls.append(1)
                if len(calls)==1:
                    with pymupdf.open(pdf) as d:
                        d[1].insert_text((25,200),'999');d.save(pdf.with_suffix('.tmp.pdf'))
                    os.replace(pdf.with_suffix('.tmp.pdf'),pdf)
                return pdf,w,c
            with patch.object(latex,'compile_project',side_effect=corrupt):
                q=app.compile_overlay(src,out,plan,cache)
            self.assertTrue(q['automated_checks_passed'])
            self.assertEqual(q['fallback_pages'],[2])
            with pymupdf.open(q['output_files']['chinese']) as cn:
                self.assertIn('供电电压',cn[0].get_text());self.assertIn('Supply voltage',cn[1].get_text())

    @unittest.skipUnless(shutil.which('xelatex') and shutil.which('latexmk'),'requires local TeX Live')
    def test_number_erased_under_a_slot_box_restores_that_slot_not_the_page(self):
        # 36312 p7: a table-header slot box covered the next row and erased
        # "200.92", a number its own text lacks; the whole page went English.
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'one.pdf';out=root/'out';out.mkdir()
            with pymupdf.open() as d:
                p=d.new_page(width=360,height=300)
                p.insert_text((25,60),'Supply voltage is high.');p.insert_text((25,200),'Ground pin is low.')
                p.insert_text((40,66),'200.92',fontsize=2.5)
                d.save(src)
            latex.write_json(out/'layout.json',{'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'pages':[{'regions':[]}]})
            plan=app.prepare(src,out)
            cache={latex.digest(s['segment']['text']):'中文译文' for p in plan['pages'] for s in p['slots']}
            top=[s for s in plan['pages'][0]['slots'] if 'Supply' in s['source']][0]
            top['bbox']=[top['bbox'][0],top['bbox'][1],top['bbox'][2],70]
            real=latex.compile_project;calls=[]
            def erase(project,main):
                pdf,w,c=real(project,main);calls.append(1)
                if len(calls)==1:
                    with pymupdf.open(pdf) as d:
                        for r in d[0].search_for('200.92'):d[0].add_redact_annot(r)
                        d[0].apply_redactions();d.save(pdf.with_suffix('.tmp.pdf'))
                    os.replace(pdf.with_suffix('.tmp.pdf'),pdf)
                return pdf,w,c
            with patch.object(latex,'compile_project',side_effect=erase):
                q=app.compile_overlay(src,out,plan,cache)
            self.assertTrue(q['automated_checks_passed'])
            self.assertEqual(q['fallback_pages'],[])
            self.assertIn('number_check_fallback',q['retained_by_kind'])
            with pymupdf.open(q['output_files']['chinese']) as cn:self.assertIn('中文译文',cn[0].get_text())

    @unittest.skipUnless(shutil.which('xelatex') and shutil.which('latexmk'),'requires local TeX Live')
    def test_merged_formula_digits_blame_the_surplus_slot_not_every_slot_with_a_digit(self):
        # 37043 p7: "I3"+"1" rendered as "31", so "3"/"1" went missing and every
        # body box holding a "1" or "3" was restored (10 boxes, 97.1%).
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'one.pdf';out=root/'out';out.mkdir()
            with pymupdf.open() as d:
                p=d.new_page(width=360,height=300)
                p.insert_text((25,60),'Matrix value 31 holds here.');p.insert_text((25,200),'Ground pin is 1 and 3 low.')
                d.save(src)
            latex.write_json(out/'layout.json',{'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'pages':[{'regions':[]}]})
            plan=app.prepare(src,out)
            cache={latex.digest(s['segment']['text']):('矩阵' if 'Matrix' in s['source'] else '接地')+' '.join(re.findall(r'@@\w+@@|\d+',s['segment']['text']))
                   for p in plan['pages'] for s in p['slots']}
            real=latex.compile_project
            def merge(project,main):
                # While the formula box is translated, its digits steal the
                # body box's "1"/"3" and extract as one extra "31".
                pdf,w,c=real(project,main)
                with pymupdf.open(pdf) as d:
                    pg=d[0];hit='矩阵' in pg.get_text()
                    if hit:
                        for r in pg.search_for('1')+pg.search_for('3'):
                            if r.y0>150:pg.add_redact_annot(r)
                        pg.apply_redactions();pg.insert_text((250,60),'31',fontsize=6);d.save(pdf.with_suffix('.tmp.pdf'))
                if hit:os.replace(pdf.with_suffix('.tmp.pdf'),pdf)
                return pdf,w,c
            with patch.object(latex,'compile_project',side_effect=merge):
                q=app.compile_overlay(src,out,plan,cache)
            self.assertTrue(q['automated_checks_passed'])
            self.assertEqual([r['text'] for r in q['retained_regions']],['Matrix value 31 holds here.'])

    @unittest.skipUnless(shutil.which('xelatex') and shutil.which('latexmk'),'requires local TeX Live')
    def test_number_lost_inside_one_slot_restores_only_that_slot(self):
        # 36599 p8: one formula island lost its "3D" subscripts; with only
        # missing numbers, every slot holding a "3" was restored (7 boxes, 91.8%).
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'one.pdf';out=root/'out';out.mkdir()
            with pymupdf.open() as d:
                p=d.new_page(width=360,height=300)
                p.insert_text((25,60),'Matrix value 3 holds here.');p.insert_text((25,200),'Ground pin is 3 low.')
                d.save(src)
            latex.write_json(out/'layout.json',{'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'pages':[{'regions':[]}]})
            plan=app.prepare(src,out)
            cache={latex.digest(s['segment']['text']):('矩阵' if 'Matrix' in s['source'] else '接地')+' '.join(re.findall(r'@@\w+@@|\d+',s['segment']['text']))
                   for p in plan['pages'] for s in p['slots']}
            real=latex.compile_project
            def lose(project,main):
                pdf,w,c=real(project,main)
                with pymupdf.open(pdf) as d:
                    pg=d[0];hit='矩阵' in pg.get_text()
                    if hit:
                        for r in pg.search_for('3'):
                            if r.y1<120:pg.add_redact_annot(r)
                        pg.apply_redactions();d.save(pdf.with_suffix('.tmp.pdf'))
                if hit:os.replace(pdf.with_suffix('.tmp.pdf'),pdf)
                return pdf,w,c
            with patch.object(latex,'compile_project',side_effect=lose):
                q=app.compile_overlay(src,out,plan,cache)
            self.assertTrue(q['automated_checks_passed'])
            self.assertEqual([r['text'] for r in q['retained_regions']],['Matrix value 3 holds here.'])

    def test_offline_uses_cached_plain_translation_of_styled_segment(self):
        # Regression rc=3: the styled key is missing but its plain_segment is
        # cached; offline must swap to it, not stop on "cache incomplete".
        plain={'text':'Hello world','protected':[]}
        styled={'text':'@@P0@@Hello@@P1@@ world','protected':['{','}'],'plain_segment':plain}
        plan={'pages':[{'slots':[{'segment':styled,'bbox':[0,0,1,1],'source':'Hello world'}],'retained':[]}]}
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);latex.write_json(out/'text-cache.json',{latex.digest('Hello world'):'你好世界'})
            def build(files,path,tracking,cfg,offline):
                if files[0][2][0] is styled:raise latex.TranslationIncompleteError('missing')
                return {'ok':1},{}
            with patch.object(latex,'build_translation',side_effect=build):
                self.assertEqual(app.translate_plan(out,plan,None,{},True)[0],{'ok':1})
        self.assertIs(plan['pages'][0]['slots'][0]['segment'],plain)

    def test_bad_key_keeps_original_without_plain_retry_and_flags_failure(self):
        # Real run with DEEPSEEK_API_KEY=invalid: every request failed, the
        # styled paragraphs were retried as plain text (a second full round)
        # and the run ended rc=0 with nothing translated.
        plain={'text':'Hello world','protected':[]}
        styled={'text':'@@P0@@Hello@@P1@@ world','protected':['{','}'],'plain_segment':plain}
        plan={'pages':[{'slots':[{'segment':styled,'bbox':[0,0,1,1],'source':'Hello world'}],'retained':[]}]}
        calls=[]
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);latex.write_json(out/'text-cache.json',{})
            def build(*a):
                calls.append(1);raise latex.TranslationIncompleteError('密钥无效或无权限；请运行 pdfzh.cmd configure 更换密钥后重试，已有译文不重译。')
            with patch.object(latex,'build_translation',side_effect=build):
                cache,_=app.translate_plan(out,plan,None,{},False)
        self.assertEqual(cache,{})
        self.assertEqual(len(calls),1)
        self.assertIs(plan['pages'][0]['slots'][0]['segment'],styled)
        self.assertIn('密钥无效',plan['api_failure'])

    def test_long_metadata_string_is_blanked_for_xdvipdfmx(self):
        # 36022: a figure's InfoDict /Subject is a 4 MB hex string; xdvipdfmx
        # stops at 65535 bytes and the whole document failed (rc=5).
        with pymupdf.open() as d:
            d.new_page();x=d.get_new_xref()
            d.update_object(x,'<</Title (fig.pdf)/Subject <'+'AB'*70000+'>>>')
            self.assertEqual(app.drop_long_strings(d),1)
            self.assertEqual(d.xref_get_key(x,'Subject')[1],'')
            self.assertEqual(d.xref_get_key(x,'Title')[1],'fig.pdf')

    def test_truncated_download_is_rejected_but_trailing_junk_is_fine(self):
        with pymupdf.open() as d:
            d.new_page().insert_text((72,72),'hello');data=d.tobytes()
        with tempfile.TemporaryDirectory() as t:
            good=Path(t)/'good.pdf';good.write_bytes(data+b'\r\n'+b'\0'*500)
            app.check_complete(good)
            cut=Path(t)/'cut.pdf';cut.write_bytes(data[:len(data)//2])
            with self.assertRaisesRegex(ValueError,'不完整'):app.check_complete(cut)
            with self.assertRaisesRegex(ValueError,'不完整'):app.main([str(cut),'--outdir',str(Path(t)/'o'),'--offline'])

    def test_references_stay_english_but_appendix_is_translated(self):
        with pymupdf.open() as d:
            p=d.new_page(width=600,height=800)
            for k in range(12):p.insert_text((50,200+k*12),'Body prose line number with plenty of ordinary words in it.',fontsize=10)
            p.insert_text((50,400),'References',fontsize=10)
            p.insert_text((50,430),'Our method follows the references listed below closely.',fontsize=10)
            p.insert_text((50,500),'References',fontsize=12,fontname='hebo')
            p.insert_text((50,530),'[1] Faye Y Wu and H Harry Asada. Hold and grasp. In ICRA, 2024.',fontsize=9)
            p.insert_text((50,545),'A',fontsize=9)
            p.insert_text((50,560),'[2] Kaiming He, Xiangyu Zhang. Deep residual learning. CVPR 2016.',fontsize=9)
            q=d.new_page(width=600,height=800)
            q.insert_text((50,60),'[3] Diederik Kingma and Jimmy Ba. Adam: a method. ICLR 2015.',fontsize=9)
            q.insert_text((50,100),'A. Additional Details',fontsize=12,fontname='hebo')
            q.insert_text((50,130),'The appendix explains the training setup in more detail here.',fontsize=10)
            ranges=app.references_ranges(d)
            self.assertEqual(len(ranges),1)
            def inside(pi,y):return app.in_references(ranges,pi,pymupdf.Rect(50,y-8,300,y),600)
            self.assertFalse(inside(0,430) or inside(0,300))
            self.assertTrue(inside(0,530) and inside(0,560) and inside(1,60))
            self.assertFalse(inside(1,130))
        # Without a year on its lines a "References" contents entry is no list.
        with pymupdf.open() as d:
            p=d.new_page(width=600,height=800)
            p.insert_text((50,100),'References',fontsize=12,fontname='hebo')
            for k in range(8):p.insert_text((50,130+k*14),'Section heading of the table of contents',fontsize=10)
            self.assertEqual(app.references_ranges(d),[])


if __name__=='__main__':unittest.main()
