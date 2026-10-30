"""Native-text PDF -> local layout analysis -> XeLaTeX text overlay.

No author source, paper-specific coordinates, remote OCR or paid layout API.
Graphics, numeric cells and uncertain mathematical regions remain original.
This backend deliberately rejects scans until an OCR backend is configured.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict, deque
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

import pymupdf
from pypdf import PdfWriter
import pdfzh_latex as latex
import pdfzh_styles as styles
from inspect_pdf_layout import inspect

HERE=Path(__file__).resolve().parent
LEGACY_NATIVE_TOKEN=re.compile(
    r'https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+|'
    r'°\s*[CF]|'
    r'(?:[+−-]?[A-Za-z]*\d[\w.μµΩΩ°/+−-]*|[−+-]?\d+(?:\.\d+)?%?)|'
    r'(?<!\w)(?:[pnumkMGTμµ]?(?:V|A|W|F|H|Hz|Pa|Ohm|Ω|Ω)|mm|cm|nm|kg|ms|ns|us|dB|ppm)(?!\w)|'
    r'\b[A-Z][A-Z_\d-]+\b|[Δθμµ][A-Za-z_]*|[%±≤≥°ΩΩ\\{}$&#_^]'
)
ISLAND_BASE=0xF0000  # supplementary private use: one char per inline-math island
MATH_FONT=re.compile(r'^(?:CMMI|CMSY|CMEX|CMBSY|MSAM|MSBM|EU[FRS][MB]|RSFS|LMMath|LatinModernMath|'
    r'STIX\w*Math|\w*[-_]Math$|Cambria-?Math|N?TXMI|N?TXSY|N?TXEX|NewTX(?:MI|SY|EX)|ZXMI|ZXSY|'
    r'MTMI|MTSY|MTEX|RTXMI|PXMI|PXSY|'
    # Symbol/dingbat fonts map µ, −, °, Ω and bullets to private-use codes.
    r'Symbol(?:MT)?\b|Wingdings|ZapfDingbats)',re.I)
WEAK_MATH_FONT=re.compile(r'^(?:CMR|CMBX|LMRoman)\d',re.I)
MATH_OPERATORS=frozenset('log lim max min exp sin cos tan sinh cosh tanh arg sup inf det ker dim deg gcd ref ln lg sgn tr diag softmax argmax argmin limsup liminf var cov'.split())
SYMBOLIC=re.compile(r'[\s\d=+\-−×·()\[\]{}|,.;:/<>′\'*∗]+')
NATIVE_TOKEN=re.compile(
    r'[\U000F0000-\U000FFFFD]|'
    r'\b[A-Z][A-Z_\d-]*\d(?:\.\d+)+[\w.μµΩΩ°/+−-]*|'+LEGACY_NATIVE_TOKEN.pattern)
NUMBERS=re.compile(r'(?<!\d)\d+(?:\.\d+)?')
ROMAN_VALUES={r:i for i,r in enumerate('I II III IV V VI VII VIII IX X XI XII XIII XIV XV XVI XVII XVIII XIX XX'.split(),1)}
REFERENCE_ROMAN=re.compile(r'\b(?i:tables?|figs?\.?|figures?|sections?|secs?\.?|algorithms?|eqs?\.?|equations?)\s+('+'|'.join(sorted(ROMAN_VALUES,key=len,reverse=True))+r')\b')
WORD_VALUES={w:i for i,w in enumerate('zero one two three four five six seven eight nine ten'.split())}
NUMBER_WORDS=re.compile(r'\b(?i:(zero|one|two|three|four|five|six|seven|eight|nine|ten)s?)\b')
MONTHS={name:i for i,name in enumerate('January February March April May June July August September October November December'.split(),1)}
ACRONYMS=set('CMOS NMOS PMOS MOSFET JFET JEDEC ROHS ESD TTL SCSI SOIC SOP TSSOP SOT DFN QFN LDO ESR PSRR PCB GND VIN VOUT IOUT ILOAD VREF ADJ CADJ COUT CIN TJ TA JA RMS DRW TYP AMS IN OUT'.split())
SHORT_ENGLISH=set('LOW HIGH LINE LOAD VIEW LEAD CASE TYPE TAB TOP SIDE PIN PINS INCH NOTE NOT DOES MOLD PER FOR FROM WITH THE AND THIS THAT SHALL TO OF IS AS AT BY ON OR AN'.split())


def uppercase_prose(word):
    if '-' in word and all(p.isalpha() for p in word.split('-')):
        return any(uppercase_prose(p) for p in word.split('-'))
    variable=re.fullmatch(r'[VICR](?:IN|OUT|ADJ|LOAD|REF|RIPPLE|MAX|MIN|LIM|DROP|REG|SHDN)',word)
    return word.isalpha() and word.isupper() and (len(word)>=5 or word in SHORT_ENGLISH) and word not in ACRONYMS and not variable


def body_text(lines):
    texts=[]
    for line in lines:
        text=''.join(s['text'] for s in line['spans']).strip()
        text=re.sub(r'^z\s+', '• ', text)
        texts.append(text)
    text=' '.join(texts)
    # Only undo a hyphen at a PDF line boundary followed by a lowercase letter.
    for a,b in zip(texts,texts[1:]):
        if a.endswith('-') and b and b[0].islower():
            text=text.replace(a+' '+b,a[:-1]+b)
    return re.sub(r'\s+',' ',text).strip()


def prose(text):
    text=re.sub(r'https?://\S+|www\.\S+|\b\w*\d\w*\b|\b[a-z][A-Z]{2,}\b','',text)
    return bool(re.search(r'[A-Za-z][a-z]{2,}',text)) or any(len(w)>=3 and uppercase_prose(w) for w in re.findall(r'\b[A-Z]+\b',text))


def segment(text,token_pattern=NATIVE_TOKEN):
    protected=[]
    def keep(m):
        if uppercase_prose(m.group()): return m.group()
        # A leading "Q:"/"A:" dialogue prefix is prose, not a variable name.
        if m.start()==0 and m.group() in ('Q','A') and text[1:2]==':': return m.group()
        # The article "A" opening a sentence ("A major ...") is a word too.
        if m.group()=='A' and re.match(r'\s+[a-z]{2,}',text[m.end():]) and (
                not text[:m.start()].strip() or re.search(r'[.!?:;]\s+$',text[:m.start()])): return m.group()
        token=f'@@P{len(protected)}@@';protected.append(m.group());return token
    message=token_pattern.sub(keep,text)
    return {'text':message,'protected':protected,'leading':'','trailing':'','original':text,
            'protection_format':'literal'}


def erase_core(bbox,vertical=False):
    """Strip through a span's centre along its writing direction. MuPDF
    redacts every glyph whose box intersects the rect; full span boxes of
    tight lines and superscripts overlap neighbouring rows by ~1pt."""
    r=pymupdf.Rect(bbox)
    if vertical:
        c=(r.x0+r.x1)/2;w=max(r.width*.15,.1)
        return pymupdf.Rect(c-w,r.y0+.1,c+w,max(r.y0+.2,r.y1-.1))
    c=(r.y0+r.y1)/2;h=max(r.height*.15,.1)
    return pymupdf.Rect(r.x0+.1,c-h,max(r.x0+.2,r.x1-.1),c+h)


def island_escape(s):
    """tex_escape for one slot: island sentinels become their original glyphs."""
    def escape(value):
        out=[];start=0
        for i,c in enumerate(value):
            k=ord(c)-ISLAND_BASE
            if 0<=k<len(s.get('islands',[])):
                out.append(tex_escape(value[start:i]))
                isl=s['islands'][k];x0,y0,x1,y1=isl['bbox']
                # Height/depth relative to the source prose size: the clip
                # scales with the slot font when TeX shrinks the paragraph.
                h=(isl['baseline']-y0+.4)/isl['size'];d=(y1-isl['baseline']+.4)/isl['size']
                # Chinese has no word spaces: a formula touching a hanzi needs
                # the small gap TeX would put around inline math.
                gap=r'\hskip.2em\relax '
                if i and is_hanzi(value[i-1]):out.append(gap)
                out.append(r'\pdfzhisland{'+str(isl['page'])+'}{'+f'{h:.4f}'+'}{'+f'{d:.4f}'+'}')
                start=i+1
                if 'after' in isl:
                    # A list bullet keeps its source gap (tab stop) to the item text.
                    out.append(r'\hskip'+f'{isl["after"]:.4f}'+r'\slotfont\relax ')
                    while start<len(value) and value[start]==' ':start+=1
                elif i+1<len(value) and is_hanzi(value[i+1]):out.append(gap)
        out.append(tex_escape(value[start:]))
        return ''.join(out)
    return escape


def drop_long_strings(doc,limit=60000):
    """xdvipdfmx aborts the whole document on a PDF string over 65535 bytes
    (a figure's PTEX.InfoDict /Subject holding a 4 MB drawio dump). Such
    strings are only metadata: blank them before the PDF goes to TeX."""
    dropped=0
    for x in range(1,doc.xref_length()):
        try:
            if len(doc.xref_object(x,compressed=True))<=limit:continue
            keys=doc.xref_get_keys(x)
        except Exception:
            continue
        for k in keys:
            kind,value=doc.xref_get_key(x,k)
            if kind=='string' and len(value)>limit//2:
                doc.xref_set_key(x,k,'()');dropped+=1
    return dropped


def write_islands(doc,plan,path):
    """One page per island: its member glyphs only, clipped from the source."""
    number=0
    with pymupdf.open() as out:
        for pi,info in enumerate(plan['pages']):
            single=None
            for s in info['slots']:
                for isl in s.get('islands',[]):
                    clip=pymupdf.Rect(isl['bbox'])+(0,-.4,0,.4)
                    members=[pymupdf.Rect(m) for m in isl['members']]
                    if single is None:
                        # insert_pdf and full-page text extraction are slow;
                        # do both once per page, not once per island.
                        with pymupdf.open() as one:
                            one.insert_pdf(doc,from_page=pi,to_page=pi);single=one.tobytes()
                            def chars(flags):
                                return [(tuple(ch['bbox']),tuple(sp['bbox']),ch['c'],(round(ch['origin'][0],2),round(ch['origin'][1],2),ch['c']))
                                        for w in one[0].get_text('rawdict',flags=flags)['blocks']
                                        for l in w.get('lines',[]) for sp in l['spans'] for ch in sp['chars']]
                            # Font-height boxes (a CMEX hat's is 3x tall) pick the
                            # glyphs; ink boxes are where erasing is safe.
                            base=pymupdf.TEXTFLAGS_RAWDICT&~pymupdf.TEXT_PRESERVE_IMAGES
                            ink={k:g for g,_,_,k in chars(base|pymupdf.TEXT_ACCURATE_BBOXES)}
                            glyphs=[(g,sp,c,ink.get(k,g)) for g,sp,c,k in chars(base)]
                    with pymupdf.open(stream=single,filetype='pdf') as tmp:
                        page=tmp[0]
                        # Keep only this island's glyphs: neighbouring prose
                        # inside the clip rectangle would otherwise be printed twice.
                        boxes=[tuple(m+(-.2,-.2,.2,.2)) for m in members]
                        near=[x for x in glyphs if not(x[0][2]<clip.x0 or x[0][0]>clip.x1 or x[0][3]<clip.y0 or x[0][1]>clip.y1)]
                        # Member glyphs are those of the member spans themselves.
                        # Span-box equality misses CMSY/CMEX symbols: their rawdict
                        # span box is the tall font box while math_islands recorded
                        # the tight 'dict' box. The ink centre sits in the recorded
                        # member box either way.
                        def own(sp):return any(max(abs(u-v) for u,v in zip(sp,m))<.3 for m in members)
                        def centred(ink):return any(a-.2<=(ink[0]+ink[2])/2<=c+.2 and b-.2<=(ink[1]+ink[3])/2<=d+.2 for a,b,c,d in boxes)
                        mine=[x for x in near if own(x[1]) or (x[2].strip() and centred(x[3]))]
                        kept={x[0] for x in mine}
                        # MuPDF erases by font-height box; spaces have no ink.
                        keep=[x[0] for x in mine if x[2].strip()]
                        # Show only the rows the island's ink covers: an oversized
                        # hat box would otherwise pull the next row into the clip.
                        inks=[x[3] for x in mine if x[2].strip()]
                        show=pymupdf.Rect(clip.x0,max(clip.y0,min(g[1] for g in inks)-.3),
                                          clip.x1,min(clip.y1,max(g[3] for g in inks)+.3)) if inks else clip
                        for g,sp,c,(x0,y0,x1,y1) in near:
                            if g in kept or not c.strip():continue
                            # A tiny box inside the glyph's ink: a full glyph box
                            # could also hit a member glyph. MuPDF tests the glyph
                            # outline, so one point can fall in the gap of an
                            # italic "i" or beside the stem of a "1" (36082 p3:
                            # the subscript "i=1" stayed in the \sum island and
                            # was printed twice): try every point.
                            for fx,fy in ((.5,.5),(.25,.5),(.75,.5),(.5,.25),(.5,.75),(.1,.5),(.9,.5),(.1,.1),(.9,.9)):
                                cx=x0+(x1-x0)*fx;cy=y0+(y1-y0)*fy
                                if not any(a<=cx<=c and b<=cy<=d for a,b,c,d in keep):
                                    page.add_redact_annot(pymupdf.Rect(cx-.05,cy-.05,cx+.05,cy+.05),fill=None)
                        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,text=pymupdf.PDF_REDACT_TEXT_REMOVE)
                        target=out.new_page(width=clip.width,height=clip.height)
                        target.show_pdf_page(show-(clip.x0,clip.y0,clip.x0,clip.y0),tmp,0,clip=show)
                    number+=1;isl['page']=number
        if number:
            drop_long_strings(out)
            out.save(path,garbage=4,deflate=True)
    return number


def tex_escape(value):
    if any(unicodedata.category(c)=='Cc' and c not in '\n\t\r' for c in value):
        raise ValueError('文本包含不可排版的控制字符；需检查原 PDF 字体映射，不能直接删除字符')
    mapping={'\\':r'\textbackslash{}','{':r'\{','}':r'\}', '%':r'\%',
             '&':r'\&','#':r'\#','$':r'\$','_':r'\_', '^':r'\textasciicircum{}',
             '~':r'\textasciitilde{}','\u00ad':'','\x00':''}
    def one(c):
        if c=='★':return r'{\upshape\fontspec{Segoe UI Symbol} '+c+'}'
        if ord(c)>127 and (unicodedata.category(c)=='Sm' or 0x1D400<=ord(c)<=0x1D7FF):
            return r'{\pdfzhsymbol\upshape '+c+'}' if has_glyph('cambria.ttc',c) else r'{\upshape\fontspec{Segoe UI Symbol} '+c+'}'
        # Any other symbol Arial lacks (⌈, ✓, arrows): Segoe UI Symbol, else
        # XeLaTeX silently drops it. CJK goes through xeCJK as usual.
        if ord(c)>127 and not is_cjk(c) and not has_glyph('arial.ttf',c) and has_glyph('seguisym.ttf',c):
            return r'{\upshape\fontspec{Segoe UI Symbol} '+c+'}'
        return mapping.get(c,c)
    return ''.join(one(c) for c in value)


_FONTS={}
def has_glyph(name,c):
    if name not in _FONTS:
        try:_FONTS[name]=pymupdf.Font(fontfile=str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name))
        except Exception:_FONTS[name]=None
    font=_FONTS[name]
    return font is None or font.has_glyph(ord(c))>0


def is_cjk(c):
    o=ord(c)
    return 0x2E80<=o<=0x9FFF or 0xF900<=o<=0xFAFF or 0xFE30<=o<=0xFE4F or 0xFF00<=o<=0xFFEF or 0x20000<=o<=0x2FA1F


def is_hanzi(c):
    o=ord(c)
    return 0x3400<=o<=0x9FFF or 0xF900<=o<=0xFAFF or 0x20000<=o<=0x2FA1F


def translated_text(s,value):
    latex.validate_translation(s,value)
    # Old caches split model versions into two placeholders. Restore only a
    # decimal separator explicitly present between the same protected digits.
    for i,left in enumerate(s['protected']):
        for j,right in enumerate(s['protected']):
            bridge=f'@@P{i}@@.@@P{j}@@'
            if left[-1:].isdigit() and right[:1].isdigit() and bridge in s['text']:
                for dot in ('。','．'):
                    value=value.replace(f'@@P{i}@@{dot}@@P{j}@@',bridge)
    if s.get('style_events'):
        value=''.join(r['text'] for r in styles.decoded_runs(s,value))
    else:
        for i,raw in enumerate(s['protected']): value=value.replace(f'@@P{i}@@',raw)
    # Numeric assertions are per segment: prevents swapping values across rows.
    expected=Counter(NUMBERS.findall(s['original']))
    actual=Counter(NUMBERS.findall(value))
    # A named English month may legitimately become an Arabic Chinese month.
    # Only allow the exact month, its source occurrence count, and the 月 suffix.
    additions=Counter()
    for name,number in MONTHS.items():
        # Full name or the standard 3-letter abbreviation (Dec, Sept).
        count=len(re.findall(r'\b(?:'+name+'|'+name[:3]+r't?\.?)(?![a-z])',s['original'],re.I))
        chinese=len(re.findall(r'(?<![\d.])'+str(number)+r'\s*月',value))
        additions[str(number)]=min(count,chinese,max(0,actual[str(number)]-expected[str(number)]))
    # "Table I" -> "表1": a Roman numeral after a reference word may become Arabic.
    for roman in REFERENCE_ROMAN.findall(s['text']):
        n=str(ROMAN_VALUES[roman.upper()])
        if actual[n]-expected[n]-additions[n]>0: additions[n]+=1
    # "turning ones into zeros and twos" -> "把 1 变成 0 和 2": each spelled-out
    # number word may become its Arabic digit, once per source occurrence.
    for word in NUMBER_WORDS.findall(s['text']):
        n=str(WORD_VALUES[word.lower()])
        if actual[n]-expected[n]-additions[n]>0: additions[n]+=1
    if actual!=expected+additions:
        raise ValueError('数字完整性检查失败：'+s['original'][:80]+
                         '；缺少 '+str(dict(expected-actual))+'，多出 '+str(dict(actual-(expected+additions))))
    return value


def remask_translation(s,value):
    """Migrate previously restored prose after protection rules change.

    Exact immutable strings must still be present, including signs and units.
    Never repair a missing value or infer it from a different PDF.
    """
    tokens=defaultdict(deque)
    for i,raw in enumerate(s['protected']): tokens[raw].append(f'@@P{i}@@')
    def keep(m):
        q=tokens[m.group()]
        if not q: raise ValueError('保护内容出现额外次数')
        return q.popleft()
    if tokens:
        # Match complete identifiers, not VL inside VLMEvalKit or 7 in 77.
        pattern='|'.join(('(?<![A-Za-z0-9_])' if t[:1].isalnum() else '')+
                         re.escape(t)+('(?![A-Za-z0-9_])' if t[-1:].isalnum() else '')
                         for t in sorted(tokens,key=len,reverse=True))
        masked=re.sub(pattern,keep,value)
    else: masked=value
    if translated_text(s,masked)!=value: raise ValueError('缓存迁移不可逆')
    return masked


def reuse_previous_report(out,plan):
    """Protection updates must not silently force another paid translation."""
    path=out/'qa.json';cache_path=out/'text-cache.json'
    # Upgrade tokenization from the immutable current source and existing
    # masked cache; never require a successful old qa.json or a paid retry.
    cache=json.loads(cache_path.read_text(encoding='utf8')) if cache_path.exists() else {}
    migrated=0
    for page in plan['pages']:
        for slot in page['slots']:
            s=slot['segment'];key=latex.digest(s['text'])
            if s.get('style_events'):continue
            old=segment(s['original'],LEGACY_NATIVE_TOKEN);oldkey=latex.digest(old['text'])
            if key in cache or oldkey not in cache: continue
            try: cache[key]=remask_translation(s,translated_text(old,cache[oldkey]))
            except ValueError: continue
            migrated+=1
    if migrated:
        latex.write_json(cache_path,cache)
        print(f'[缓存] 本地升级 {migrated} 个保护规则，未调用翻译接口',flush=True)
    if not path.is_file(): return
    previous=json.loads(path.read_text(encoding='utf8'))
    if previous.get('source_sha256')!=plan['source_sha256']: return
    cache=json.loads(cache_path.read_text(encoding='utf8')) if cache_path.exists() else {}
    values={s['source']:s['translation'] for s in previous.get('slots',[])}
    values.update({r['text']:r['translation'] for r in previous.get('retained_regions',[]) if 'translation' in r})
    # A newly isolated definition can reuse the unambiguous translated suffix
    # of an old "expression = prose" entry, without paying to translate again.
    for source,value in list(values.items()):
        match=re.search(r'=\s*([A-Za-z][A-Za-z -]+)$',source)
        parts=re.split(r'(?:=|等于)\s*',value)
        if match and len(parts)>1 and re.fullmatch(r'[\u4e00-\u9fff\s，。；：]+',parts[-1]):
            values.setdefault(match[1].strip(),parts[-1].strip())
    migrated=0
    for page in plan['pages']:
        for s in page['slots']:
            key=latex.digest(s['segment']['text'])
            if s['segment'].get('style_events'):continue
            if key in cache or s['source'] not in values: continue
            # Old protection may have left a heading in English. Such an entry
            # is not a translation for the newly unprotected words.
            if any(uppercase_prose(w) and w in values[s['source']] for w in re.findall(r'\b[A-Z]+\b',s['source'])): continue
            try: cache[key]=remask_translation(s['segment'],values[s['source']])
            except ValueError: continue
            migrated+=1
    if migrated:
        latex.write_json(cache_path,cache)
        print(f'[缓存] 本地迁移 {migrated} 个旧译文，未调用翻译接口',flush=True)


def validated_table_cells(page,table):
    """Repair spurious cell splits, but never split a native word for translation.

    Some PDF paths produce phantom grid edges. Only join adjacent cells of
    identical height when a native word actually crosses their shared edge.
    More ambiguous topology is rejected and the whole table remains original.
    """
    cells=[pymupdf.Rect(c) for row in table.rows for c in row.cells if c is not None]
    words=page.get_text('words',clip=table.bbox)
    for word in words:
        wr=pymupdf.Rect(word[:4]);cy=(wr.y0+wr.y1)/2
        hits=[c for c in cells if c.y0<=cy<=c.y1 and min(c.x1,wr.x1)-max(c.x0,wr.x0)>.1]
        if len(hits)<=1: continue
        hits.sort(key=lambda c:c.x0)
        if (any(abs(c.y0-hits[0].y0)>.5 or abs(c.y1-hits[0].y1)>.5 for c in hits)
            or any(abs(a.x1-b.x0)>.5 for a,b in zip(hits,hits[1:]))):
            raise ValueError('单词跨越了无法安全合并的单元格')
        merged=pymupdf.Rect(hits[0])
        for c in hits[1:]: merged |= c
        cells=[c for c in cells if c not in hits]+[merged]
    for word in words:
        wr=pymupdf.Rect(word[:4]);cy=(wr.y0+wr.y1)/2
        if not any(c.y0<=cy<=c.y1 and c.x0-.5<=wr.x0 and c.x1+.5>=wr.x1 for c in cells):
            raise ValueError('表格文字不完整落在单元格内')
    return cells


def untranslated_candidates(document,plan):
    """Heuristic coverage audit, separate from numeric/layout correctness.

    Proper names and technical terms can be false positives, so expose actual
    lines and coordinates for review rather than claiming a completeness score.
    """
    found=[]
    for i,page in enumerate(document):
        for block in page.get_text('dict')['blocks']:
            if block['type']!=0: continue
            for line in block['lines']:
                text=body_text([line]);rect=pymupdf.Rect(line['bbox'])
                if not prose(text):continue
                if re.search(r'[\u4e00-\u9fff]',text):continue
                if any(r['kind'] in ('page_furniture','references') and (rect.tl+rect.br)/2 in pymupdf.Rect(r['bbox'])
                       for r in plan['pages'][i]['retained']):continue
                found.append({'page':i+1,'bbox':list(rect),'text':text})
    return found


GARBLED_SHARE=.25


def garbled_text(retained,slots):
    """Share of the page text (chars) kept back as unmapped_glyph. Garbled text
    never counts as body, so body coverage stays 100% even when nothing was
    translated (IRLZ44N before 72c3183: 67%). Translated documents peak at 7%
    (MCP9700 package drawings), so 25% separates the two."""
    bad=sum(len(r.get('text','').strip()) for r in retained if r['kind']=='unmapped_glyph')
    total=bad+sum(len(s['source'].strip()) for s in slots)
    share=round(bad/total,4) if total else 0.0
    return {'unmapped_chars':bad,'share':share,'threshold':GARBLED_SHARE,'warning':share>GARBLED_SHARE}


def body_coverage(source,plan,valid,layout):
    """Share of body-size prose (chars) inside translated slots.

    Body = the most common font size, >=4 English words, centred in a layout
    'plain text' region: tables, figures, captions and furniture fall out.
    """
    sizes=Counter();rows=[]
    for i,page in enumerate(source):
        regions=[pymupdf.Rect(r['bbox']) for r in layout[i]['regions'] if r['kind']=='plain text'] if layout else None
        for b in page.get_text('dict')['blocks']:
            for l in b.get('lines',[]):
                text=body_text([l])
                for s in l['spans']:sizes[round(s['size'],1)]+=len(s['text'].strip())
                r=pymupdf.Rect(l['bbox']);c=(r.tl+r.br)/2
                if len(re.findall(r'[A-Za-z]{2,}',text))<4 or (regions is not None and not any(c in g for g in regions)):continue
                if in_references(plan.get('references',[]),i,r,page.rect.width):continue
                rows.append((i,c,text,Counter(round(s['size'],1) for s in l['spans'] for _ in s['text'].strip()).most_common(1)[0][0]))
    if not sizes:return {'body_font':None,'body_chars':0,'coverage':1.0,'untranslated_body':[]}
    body=sizes.most_common(1)[0][0]
    done={};total=covered=0;missed=[]
    for s in valid:done.setdefault(s['page']-1,[]).append(pymupdf.Rect(s['bbox']))
    for i,c,text,size in rows:
        if abs(size-body)>.6:continue
        total+=len(text)
        if any(c in r for r in done.get(i,[])):covered+=len(text);continue
        kind=next((r['kind'] for r in plan['pages'][i]['retained'] if c in pymupdf.Rect(r['bbox'])),None)
        missed.append({'page':i+1,'kind':kind,'text':text})
    return {'body_font':body,'body_chars':total,'coverage':round(covered/total,4) if total else 1.0,'untranslated_body':missed}


def merge_continued_labels(slots):
    """Keep a short wrapped phrase such as 'TAB IS / OUTPUT' together.

    A fragment ending in a linking word is not a complete label. Joining only
    close, aligned labels improves context and provides the real two-line box.
    """
    result=list(slots)
    def word(x):return re.fullmatch(r'[A-Za-z][A-Za-z-]*',x['source'].strip()) is not None
    for s in list(result):
        if s not in result or s['kind']!='label' or s.get('rotation') or s['segment'].get('style_events') or len(s['source'])>24:continue
        linking=re.search(r'\b(?:IS|ARE|OF|TO|THE|FOR|WITH|AND)\s*$',s['source'])
        # Chart tick labels such as "Human / Level": a single word stacked
        # directly on another single word of the same size, centred together.
        stacked=word(s) and len(s['source'])<=16
        if not linking and not stacked:continue
        r=pymupdf.Rect(s['bbox']);choices=[]
        for t in result:
            if t is s or t['kind']!='label' or t.get('rotation') or t['segment'].get('style_events') or t.get('style')!=s.get('style'):continue
            tr=pymupdf.Rect(t['bbox'])
            if linking:
                ok=0<=tr.y0-r.y1<=s['font'] and abs(tr.x0-r.x0)<=s['font']
            else:
                ok=(word(t) and len(t['source'])<=16 and abs(t['font']-s['font'])<.1 and
                    -s['font']*.3<=tr.y0-r.y1<=s['font']*.5 and
                    abs((tr.x0+tr.x1)/2-(r.x0+r.x1)/2)<=max(r.width,tr.width)*.35)
            if ok:
                union=r|tr
                if any(union.intersects(pymupdf.Rect(u['bbox'])) for u in result if u is not s and u is not t):continue
                choices.append(t)
        if not choices:continue
        t=min(choices,key=lambda x:x['bbox'][1]);text=s['source']+' '+t['source']
        s['erase_rects']=s.get('erase_rects',[s['bbox']])+t.get('erase_rects',[t['bbox']])
        s.update(bbox=list(r|pymupdf.Rect(t['bbox'])),source=text,segment=segment(text))
        result.remove(t)
    return result


def expand_text_operators(operations):
    """Expand shorthand, without MuPDF clean_contents rewriting text state.

    Per PDF specification, single quote is T* then Tj; double quote also sets
    word/character spacing. Preserve every other operator and its order.
    """
    result=[]
    for args,op in operations:
        if op==b"'":result.extend([([],b'T*'),(args,b'Tj')])
        elif op==b'"':result.extend([([args[0]],b'Tw'),([args[1]],b'Tc'),([],b'T*'),([args[2]],b'Tj')])
        else:result.append((args,op))
    return result


ZERO_SPACING=re.compile(rb'(^|[\s\]>)])[-+]?0*\.?0+\s+(Tc|Tw)\b')


def normalized_document(pdf):
    writer=PdfWriter(clone_from=str(pdf))
    for page in writer.pages:
        stream=page.get_contents()
        if stream is not None:
            stream.operations=expand_text_operators(stream.operations)
            page.replace_contents(stream)
    buffer=BytesIO();writer.write(buffer)
    doc=pymupdf.open(stream=buffer.getvalue(),filetype='pdf')
    # MuPDF's redaction rewrite drops "0 Tc"/"0 Tw" as defaults, so a
    # spacing set earlier on the page leaks into the next text (ACS712 p11:
    # the page number's -0.074 Tc shifted the whole footer form). A tiny
    # non-zero value is kept and draws the same.
    # ponytail: regex on raw streams; "...1 0 Tc" inside a malformed operand list would also match.
    contents={x for p in doc for x in p.get_contents()}
    for xref in range(1,doc.xref_length()):
        if not doc.xref_is_stream(xref):continue
        if xref not in contents and doc.xref_get_key(xref,'Subtype')[1]!='/Form':continue
        stream=doc.xref_stream(xref)
        fixed=ZERO_SPACING.sub(rb'\g<1>0.00001 \2',stream)
        if fixed!=stream:doc.update_stream(xref,fixed)
    return doc


def retained_position_errors(source,background,slots):
    selected=[pymupdf.Rect(s['bbox']) for s in slots]
    positions=defaultdict(list)
    # A zero-width combining overlay (the slash of ≠) may be re-attached to
    # another word after redaction; it is not a moved glyph.
    # A soft hyphen re-extracts as "-" after any redaction (ACS712 p10).
    def key(t):return ''.join(c for c in t if not unicodedata.combining(c)).replace('\xad','-')
    for w in background.get_text('words'):positions[key(w[4])].append(w[:4])
    def chars(page,rect):
        return [(c['c'],c['bbox']) for b in page.get_text('rawdict',clip=rect+(-1,-1,1,1))['blocks'] if b['type']==0
                for l in b['lines'] for s in l['spans'] for c in s['chars']
                if c['c'].strip() and rect.contains(((c['bbox'][0]+c['bbox'][2])/2,(c['bbox'][1]+c['bbox'][3])/2))]
    errors=[]
    for word in source.get_text('words'):
        if any(pymupdf.Rect(word[:4]).intersects(r) for r in selected):continue
        if any(max(abs(a-b) for a,b in zip(word[:4],other))<.5 for other in positions[key(word[4])]):continue
        # Text drawn twice at one spot (fill + stroke figure labels) is
        # re-grouped into one word after any redaction; judge by glyphs.
        rect=pymupdf.Rect(word[:4]);after=chars(background,rect);before=chars(source,rect)
        if before and all(any(key(c)==key(d) and max(abs(a-b) for a,b in zip(box,other))<.5 for d,other in after) for c,box in before):continue
        errors.append({'text':word[4],'bbox':list(word[:4])})
    return errors


def restore_rotated(page,source,slots):
    """Redraw retained rotated lines (the arXiv side stamp) that slot erasure
    hit: a narrow margin puts body glyphs inside the stamp's glyph boxes, so
    every redaction strip also removes stamp digits. The stamp is cleared and
    copied from the source, clipped to the side of it free of horizontal text."""
    lines=[l for b in source.get_text('dict')['blocks'] for l in b.get('lines',[])]
    flat=[pymupdf.Rect(l['bbox']) for l in lines if l['dir']==(1.0,0.0)]
    boxes=[pymupdf.Rect(s['bbox']) for s in slots]
    for l in lines:
        r=pymupdf.Rect(l['bbox'])
        if abs(l['dir'][0])>.01 or any(b.contains((r.tl+r.br)/2) for b in boxes) or not any(r.intersects(b) for b in boxes):continue
        clip=pymupdf.Rect(r)
        for f in flat:
            if f.intersects(r):
                if (f.x0+f.x1)/2>(r.x0+r.x1)/2:clip.x1=min(clip.x1,f.x0-.2)
                else:clip.x0=max(clip.x0,f.x1+.2)
        if clip.width<.5*r.width:continue
        page.add_redact_annot(clip,fill=None)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        page.show_pdf_page(clip,source.parent,source.number,clip=clip)
# ponytail: vertical lines only; a horizontal stamp over body text would need a y-clip.


def redacted_copy(doc,pi,slots,_single={}):
    # One-page extract cached per document page: insert_pdf of a large
    # document is the slow part, reopening the bytes is cheap.
    k=(id(doc),pi)
    if k not in _single:
        _single.clear()
        with pymupdf.open() as one:
            one.insert_pdf(doc,from_page=pi,to_page=pi);_single[k]=one.tobytes()
    tmp=pymupdf.open(stream=_single[k],filetype='pdf');page=tmp[0]
    for s in slots:
        for box in s.get('erase_rects',[s['bbox']]):
            page.add_redact_annot(erase_core(box,bool(s.get('rotation'))),fill=None)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    return tmp


def shifting_slots(doc,pi,source,slots):
    """Slots whose erasure moves retained words. Only failing pages pay for
    the per-slot search."""
    def broken(group):
        with redacted_copy(doc,pi,group) as tmp:return bool(retained_position_errors(source,tmp[0],group))
    if not slots or not broken(slots):return []
    # Shifts can need two slots together (both halves of one TJ run), so
    # grow an accepted set; bisect each batch so a clean page section costs
    # one redaction instead of one per slot.
    kept=[];bad=[]
    def take(group):
        if not broken(kept+group):kept.extend(group);return
        if len(group)==1:bad.append(group[0]);return
        take(group[:len(group)//2]);take(group[len(group)//2:])
    take(list(slots))
    return bad


def fraction_regions(page):
    """Find short horizontal rules with mathematical text above AND below.

    Uses PDF vector geometry, not OCR, guessed LaTeX or sample coordinates.
    Numeric-only table rows alone do not qualify as a numerator.
    """
    words=page.get_text('words');regions=[]
    for drawing in page.get_drawings():
        r=drawing['rect']
        if not (8<=r.width<=page.rect.width*.3 and r.height<=1.5):continue
        y=(r.y0+r.y1)/2
        nearby=[w for w in words if r.x0-2<=(w[0]+w[2])/2<=r.x1+2]
        above=[w for w in nearby if 0<y-(w[1]+w[3])/2<max(8,w[3]-w[1])*1.5]
        below=[w for w in nearby if 0<(w[1]+w[3])/2-y<max(8,w[3]-w[1])*1.5]
        if not above or not below:continue
        numerator=' '.join(w[4] for w in above);denominator=' '.join(w[4] for w in below)
        if not re.search(r'[A-Za-zΔθ]+\d|[+*/−=]|[A-Za-z]_?',numerator):continue
        if prose(numerator) or prose(denominator):continue
        box=pymupdf.Rect(r)
        for w in above+below:box |= pymupdf.Rect(w[:4])
        regions.append(box)
    return regions


def header_columns(page,cell,table):
    """Align spaced labels in a merged header with actual subcolumns below."""
    words=page.get_text('words',clip=cell)
    if len(words)<2 or any(not re.fullmatch(r'[A-Za-z]+',w[4]) for w in words):return []
    if max(w[1] for w in words)-min(w[1] for w in words)>2:return []
    if any(b[0]-a[2]<max(4,(a[3]-a[1])*.6) for a,b in zip(words,words[1:])):return []
    for row in table.rows:
        sub=[pymupdf.Rect(c) for c in row.cells if c and c[1]>=cell.y1-.5 and c[0]>=cell.x0-.5 and c[2]<=cell.x1+.5]
        if len(sub)!=len(words):continue
        sub.sort(key=lambda r:r.x0)
        if all(r.x0<=(w[0]+w[2])/2<=r.x1 for r,w in zip(sub,words)):
            return [{'source':w[4],'bbox':[r.x0+2,cell.y0+1,r.x1-2,cell.y1-1]} for r,w in zip(sub,words)]
    return []


def starts_bullet(line):
    """A row opening with a list bullet, also one kept as a dingbat island."""
    first=next((s for s in line['spans'] if s['text'].strip()),None)
    if first is None:return False
    return bool(re.match(r'[•▪◦■◆●]',first['text'].strip()) or first.get('dingbat'))


def continuation_runs(lines):
    """Group wrapped lines of one sentence; list items stay separate.

    A line continues the previous one when it is left-aligned with it, close
    below it, of the same size, and the previous line does not end a sentence
    (or the new line starts in lowercase). Bullets and "X:" labels start runs.
    """
    def size(l):return max((s['size'] for s in l['spans']),default=0)
    pseudo=pseudo_block(lines)
    runs=[]
    for line in lines:
        text=body_text([line]);r=pymupdf.Rect(line['bbox'])
        if runs:
            prev=runs[-1][-1];pr=pymupdf.Rect(prev['bbox']);pt=body_text([prev]);fs=max(size(prev),1)
            starts_item=(starts_bullet(line) or (pseudo and pseudo_code_line(line)) or
                re.match(r'^(?:[•▪◦\-–*]|\(?[A-Za-z0-9]{1,3}[.)]\s|[A-Z][A-Za-z]{0,10}:)',text))
            # Lines of a wrapped sentence overlap slightly (y0 < prev.y1)
            # in tight figure callouts; allow up to half a line of overlap.
            if (not starts_item and abs(r.x0-pr.x0)<=fs and -fs*.5<=r.y0-pr.y1<=fs*.8
                and abs(size(line)-fs)<=fs*.15
                and (not re.search(r'[.?!:;]$',pt) or text[:1].islower())):
                runs[-1].append(line);continue
        runs.append([line])
    return runs


def paragraph_parts(lines,limit=1200):
    """Split one native block into paragraphs.

    A new paragraph starts at a line that opens with a bold run (run-in
    heading) or is indented after a line ending a sentence. Parts still over
    `limit` characters are cut after a sentence-ending line, so no request
    is long enough for the model to stop early.
    """
    def first(l):return next((s for s in l['spans'] if s['text'].strip()),None)
    left=min(l['bbox'][0] for l in lines)
    pseudo=pseudo_block(lines)
    parts=[[lines[0]]]
    for prev,line in zip(lines,lines[1:]):
        f=first(line);pt=body_text([prev]);fs=max((s['size'] for s in line['spans']),default=10)
        # Only cut at a new visual line after a finished sentence: a bold
        # heading may wrap ("reason-" / "ing.") and its body may continue on
        # the same visual line as a separate PDF line object.
        new_row=line['bbox'][1]>=prev['bbox'][3]-fs*.3
        ended=re.search(r'[.?!:]$',pt) is not None
        bold_head=f is not None and styles.style(f)['bold']
        indent=line['bbox'][0]-left>fs*.8
        size=sum(len(body_text([l])) for l in parts[-1])
        long_cut=size>limit and re.search(r'[.?!]$',pt) is not None
        # A heading row ("3 Description") in a larger size than the body.
        ps=max((s['size'] for s in prev['spans']),default=fs)
        resized=new_row and abs(ps-fs)>min(ps,fs)*.15
        if resized or (new_row and starts_bullet(line)) or (new_row and pseudo and pseudo_code_line(line)) or (new_row and ended and (bold_head or indent or long_cut)):parts.append([line])
        else:parts[-1].append(line)
    return parts


def pseudo_block(lines):
    """An algorithm box keeps one part per row. Only a block with assignment
    arrows or several "Require:" rows counts: parentheses and "for"/"if"
    openers are common in prose and tables, where splitting rows would
    scatter ordinary paragraphs."""
    texts=[body_text([l]) for l in lines]
    if sum(pseudo_code_line(l) for l in lines)<3:return False
    # Math islands replace "m0 ← 0" by a private-use glyph, so the arrow is
    # often gone by now; rows that open with an island and end in a
    # parenthesised comment are the same step pattern.
    steps=sum(bool((re.match(r'\s*[\U000f0000-\U000fffff]',t) and re.search(r'\)\s*$',t)) or
                   re.match(r'\s*(?:end\s+(?:while|for|if)|return)\b',t)) for t in texts)
    return (any('←' in t for t in texts) or steps>=3 or
            sum(bool(re.match(r'\s*(?:Require|Ensure|Input|Output):',t)) for t in texts)>=2)


def pseudo_code_line(line):
    """A pseudo-code step: assignment arrow, a control word opening the line,
    or a step ending in a parenthesised comment. Merging such rows into one
    paragraph makes TeX reflow them and neighbouring steps overlap."""
    text=body_text([line]).strip()
    if not text:return False
    return bool(re.search(r'[←→]',text) or
        re.match(r'^(?:Require|while|end|for|if|return)\b',text,re.I) or
        re.search(r'\([^)]+\)\s*$',text))


def visual_rows(lines):
    """Join PDF line objects sharing one baseline into one visual row.

    Justified datasheets (e.g. TI) emit every word as its own line object;
    treated separately, each word becomes a slot and overlaps its row.
    """
    rows=[]
    for line in lines:
        if rows:
            prev=rows[-1];pr,r=prev['bbox'],line['bbox']
            h=min(pr[3]-pr[1],r[3]-r[1])
            same_dir=tuple(prev.get('dir',(1,0)))==tuple(line.get('dir',(1,0))) and abs(line.get('dir',(1,0))[1])<1e-3
            overlap=min(pr[3],r[3])-max(pr[1],r[1])
            if same_dir and h>0 and overlap>h*.6 and pr[2]-1<=r[0]<=pr[2]+h*1.5:
                # A zero-width space span keeps words apart without altering
                # the native spans (their bbox+text identify what to erase).
                gap={**line['spans'][0],'text':' ','bbox':(pr[2],r[1],pr[2],r[3])}
                rows[-1]={**prev,'spans':list(prev['spans'])+[gap]+line['spans'],
                          'bbox':(pr[0],min(pr[1],r[1]),max(pr[2],r[2]),max(pr[3],r[3]))}
                continue
        rows.append(line)
    return rows


def width_groups(lines):
    """Split a paragraph wrapped around a figure into its narrow and full-width
    line runs; one bounding box over both would cover the figure."""
    if len(lines)<4:return [lines]
    right=max(l['bbox'][2] for l in lines);width=right-min(l['bbox'][0] for l in lines)
    short=[l['bbox'][2]<right-width*.15 for l in lines]
    short[-1]=short[-2]  # a paragraph's last line is naturally short
    for i in range(1,len(short)-1):
        if short[i-1]==short[i+1]!=short[i]:short[i]=short[i-1]
    groups=[[lines[0]]]
    for flag,prev,line in zip(short[1:],short,lines[1:]):
        if flag!=prev:groups.append([line])
        else:groups[-1].append(line)
    return groups if len(groups)>1 and all(len(g)>=2 for g in groups) else [lines]


def tall_glyph_boxes(page,blocks):
    """A CMEX hat or radical span can report a box 3-4x its font size, which
    swallows the rows around it. Use the glyphs' ink box for such spans."""
    tall=[s for b in blocks if b['type']==0 for l in b['lines'] for s in l['spans']
          if s['bbox'][3]-s['bbox'][1]>1.6*s['size']]
    if not tall:return
    flags=pymupdf.TEXTFLAGS_DICT&~pymupdf.TEXT_PRESERVE_IMAGES|pymupdf.TEXT_ACCURATE_BBOXES
    ink={(round(s['origin'][0],1),round(s['origin'][1],1),s['text']):s['bbox']
         for b in page.get_text('dict',flags=flags)['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans']}
    for s in tall:
        box=ink.get((round(s['origin'][0],1),round(s['origin'][1],1),s['text']))
        # Keep at least a script-row height: hats are a 2pt sliver of ink.
        if box and box[3]>box[1]:
            s['bbox']=(s['bbox'][0],box[1],s['bbox'][2],max(box[3],box[1]+s['size']*.5))
    for b in blocks:
        for l in b.get('lines',[]):
            if any(s in tall for s in l['spans']):
                ys=[v for s in l['spans'] if s['text'].strip() for v in (s['bbox'][1],s['bbox'][3])] or [l['bbox'][1],l['bbox'][3]]
                l['bbox']=(l['bbox'][0],min(ys),l['bbox'][2],max(ys))


def ink_box(spans):
    """Union of a span list's box, shrunk past leading/trailing spaces by
    their estimated width (spans carry no per-char boxes in 'dict' mode)."""
    box=None
    for s in spans:
        t=s['text'];r=pymupdf.Rect(s['bbox'])
        if not t.strip():continue
        if len(t)>1:
            per=r.width/len(t)
            r.x0+=per*(len(t)-len(t.lstrip()))*.9;r.x1-=per*(len(t)-len(t.rstrip()))*.9
        box=r if box is None else box|r
    return box or pymupdf.Rect(spans[0]['bbox'])


def small_caps(blocks):
    """Small caps are emitted as a full-size capital plus smaller capitals
    ("H"+"ISTORY"). Restore the word ("History") and the capital size, so the
    translator sees words, not acronyms, and headings keep their size."""
    for b in blocks:
        if b['type']!=0:continue
        for line in b['lines']:
            spans=line['spans']
            for prev,s in zip(spans,spans[1:]):
                t=s['text']
                if (re.fullmatch(r'[A-Z][A-Z-]*',t.strip()) and t[:1].strip() and
                        re.search(r'[A-Z]$',prev['text']) and prev['font']==s['font'] and
                        .65<=s['size']/max(prev['size'],.1)<=.9):
                    s['text']=t.lower();s['size']=prev['size']


def merge_negation(blocks):
    """TeX \\not= draws a zero-width slash (CMSY, U+0338) and then the relation;
    extracted loose, the slash lands after the previous formula and the relation
    prints as a bare "=" (37297 p6: "Tk' != Tk"). Fuse the pair into the precomposed
    symbol (=, in, <=, ... -> !=, not-in, not-<=) inside the relation's span; the
    span's box covers both glyphs, so both are erased."""
    for b in blocks:
        if b['type']!=0:continue
        for line in b['lines']:
            spans=line['spans']
            for s in spans:
                if len(s['text'])>1 and '\u0338' in s['text']:s['text']=unicodedata.normalize('NFC',s['text'])
            i=0
            while i<len(spans):
                s=spans[i]
                if s['text'].strip() in ('\u0338','\u20d2'):
                    j=i+1
                    while j<len(spans) and not spans[j]['text'].strip():j+=1
                    if j<len(spans):
                        t=spans[j];rest=t['text'].lstrip();fused=unicodedata.normalize('NFC',rest[:1]+s['text'].strip())
                        sb,tb=s['bbox'],t['bbox']
                        if len(fused)==1 and 0<=tb[0]-sb[2]<=max(s['size'],t['size']) and min(sb[3],tb[3])>max(sb[1],tb[1]):
                            text=''.join(x['text'] for x in spans[i+1:j])+t['text'][:len(t['text'])-len(rest)]+fused+rest[1:]
                            spans[i:j+1]=[{**t,'text':text,'bbox':(sb[0],min(sb[1],tb[1]),tb[2],max(sb[3],tb[3]))}]
                i+=1


def math_islands(blocks):
    """Replace inline math (math-font spans with their sub/superscripts) by one
    private-use sentinel span each, in place. Returns island descriptions.

    The sentinel is protected from translation and rendered as a clip of the
    original vector glyphs, so formulas are never flattened into plain text.
    Sub/superscripts emitted as separate PDF line objects are attached to the
    island they overlap; formulas without surrounding prose stay untouched.
    """
    def math_like(s,prose_size):
        t=s['text'].strip()
        # CMEX encodes big delimiters as C0 codes ('\r' is a brace piece), which
        # strip() would treat as blank; they are glyphs of the formula.
        if MATH_FONT.match(s['font']) and any(unicodedata.category(c)=='Cc' and c!=' ' for c in s['text']):return 'strong'
        if not t or t in '•∙◦':return None
        if MATH_FONT.match(s['font']):return 'strong'
        if WEAK_MATH_FONT.match(s['font']) and (s['size']<prose_size*.85 or SYMBOLIC.fullmatch(t)):return 'weak'
        return None
    candidates=[]
    body=Counter()
    for b in blocks:
        for l in b.get('lines',[]):
            for s in l['spans']:body[round(s['size'],1)]+=len(s['text'].strip())
    body_size=body.most_common(1)[0][0] if body else 10
    for b in blocks:
        if b['type']!=0:continue
        for line in b['lines']:
            spans=line['spans']
            sizes=Counter()
            for s in spans:
                if s['text'].strip() and not MATH_FONT.match(s['font']) and not WEAK_MATH_FONT.match(s['font']):
                    sizes[round(s['size'],2)]+=len(s['text'])
            prose_size=sizes.most_common(1)[0][0] if sizes else max((s['size'] for s in spans),default=10)
            # A paper typeset in Computer Modern sets its prose in CMR10/CMBX10 too.
            # A row of body-size words is prose, not a detached formula row below:
            # as an island it merged into a neighbouring row's island and its
            # glyphs jumped to that line (37297 p6), so it stays plain text.
            # Only a row made of words: a formula row with an operator name
            # ("log", "lim") or a stray letter run is still a formula (DPO p6:
            # "r = beta log" became text and "log" was translated as "日志").
            # (judged over the whole visual row: a PDF may emit one line object per word)
            if not sizes:
                cy=(line['bbox'][1]+line['bbox'][3])/2
                words=[w for l in b['lines'] if abs((l['bbox'][1]+l['bbox'][3])/2-cy)<=4
                       for s in l['spans'] if WEAK_MATH_FONT.match(s['font']) and s['size']>=body_size*.85
                       for w in re.findall(r'[A-Za-z]{3,}',s['text']) if w.lower() not in MATH_OPERATORS and not w.isupper() and not re.search(r'[a-z][A-Z]',w)]
                if len(words)>=2:continue
            kinds=[math_like(s,prose_size) for s in spans]
            # Script-size math-roman digits split from their partner ("1" over
            # "2" in another PDF line) must stay a formula, not prose digits.
            for i,s in enumerate(spans):
                if kinds[i]=='weak' and s['size']<prose_size*.85 and re.fullmatch(r'\d+',s['text'].strip()):kinds[i]='strong'
            # A raised script-size marker opening a line in a text font ("1" before
            # "The Chinese University") would fuse with the next word ("1The") and
            # be drawn as a full-size digit; it is an island. Markers inside a word
            # ("I^2C") already protect as one token and are left alone.
            # Mid-line markers ("Ruan^1,2") stay out: they protect the whole name
            # as one token, whereas a bare name gets transliterated by the model.
            if len(spans)>1 and not kinds[0] and spans[0]['size']<prose_size*.85 and re.fullmatch(r'[\d,*†‡§¶∗]+',spans[0]['text']) \
                    and not kinds[1] and spans[1]['text'].strip() and spans[1]['origin'][1]-spans[0]['origin'][1]>=prose_size*.2:
                kinds[0]='strong'
            # A circled number "①" is a text digit overprinted by a large math
            # circle; the digit sits inside the circle's box, so the island
            # clip cannot erase it. Both are one island.
            for i,s in enumerate(spans):
                if kinds[i] or not 0<len(s['text'].strip())<=2:continue
                r=pymupdf.Rect(s['bbox'])
                for j in (i-1,i+1):
                    if 0<=j<len(spans) and kinds[j]=='strong' and r.get_area()>0 and \
                            (r&pymupdf.Rect(spans[j]['bbox'])).get_area()>.7*r.get_area():
                        kinds[i]='strong';break
            if not sizes:
                # A row of only math-roman glyphs (a detached subscript "V").
                kinds=[k or ('weak' if s['text'].strip() and WEAK_MATH_FONT.match(s['font']) else None) for s,k in zip(spans,kinds)]
            # Operator names in math roman glued to the formula ("Append" in
            # Append_K(...)) belong to it.
            grew=True
            while grew:
                grew=False
                for i,s in enumerate(spans):
                    if kinds[i] or not WEAK_MATH_FONT.match(s['font']) or not s['text'].strip():continue
                    for j in (i-1,i+1):
                        if not (0<=j<len(spans) and kinds[j]):continue
                        o=spans[j]
                        gap=o['bbox'][0]-s['bbox'][2] if j>i else s['bbox'][0]-o['bbox'][2]
                        # No space between them on either side of the joint.
                        tight=(s['text'][-1:].strip() and o['text'][:1].strip()) if j>i else (s['text'][:1].strip() and o['text'][-1:].strip())
                        if abs(gap)<.6 and tight and (kinds[j]=='strong' or MATH_FONT.match(o['font'])):
                            kinds[i]='weak';grew=True;break
            if not any(kinds):continue
            items=[];i=0
            while i<len(spans):
                if not kinds[i]:items.append(spans[i]);i+=1;continue
                j=i
                # Whitespace-only spans between two math spans belong to it.
                while j+1<len(spans) and (kinds[j+1] or (not spans[j+1]['text'].strip() and
                        j+2<len(spans) and kinds[j+2])):j+=1
                members=spans[i:j+1]
                island={'members':members,'line':line,'size':prose_size,
                        'strong':any(kinds[k]=='strong' for k in range(i,j+1))}
                # A leading space glyph overlaps the previous word; the clip
                # box comes from visible glyphs only.
                island['bbox']=ink_box(members)
                main=max(members,key=lambda m:m['size'])
                island['baseline']=main.get('origin',(0,island['bbox'].y1-main['size']*.22))[1]
                items.append(island);candidates.append(island);i=j+1
            line['_items']=items
    # A line made only of math (its own PDF line object: a sub/superscript or
    # the rest of a formula on the same row) is attached to a touching island.
    def orphan(island):
        # Trailing punctuation ("2n ,") does not make a row prose.
        return all('members' in x or not re.search(r'\w',x['text']) for x in island['line']['_items'])
    def lineno(island,other):
        # Script-size digits opening their PDF line: "13", "17 Function".
        first=island['members'][0]
        lead=next((s for s in island['line']['spans'] if s['text'].strip()),None)
        return lead is first and re.fullmatch(r'\d{1,3}',first['text'].strip() or '-') is not None and first['size']<other['size']*.85
    def near(a,b):
        ra,rb=a['bbox'],b['bbox']
        # An algorithm line number never joins what lies left of it across
        # a gap (37297 p4: body formula "P" at x=289 took "17" at x=293).
        left,right=(a,b) if ra.x0<=rb.x0 else (b,a)
        if left['line'] is not right['line'] and lineno(right,left) and right['bbox'].x0-left['bbox'].x1>2:return False
        if (ra+(-.5,-.5,.5,.5)).intersects(rb):return True
        h=min(ra.height,rb.height);overlap=min(ra.y1,rb.y1)-max(ra.y0,rb.y0)
        # Up to a math space around a binary operator ("x/m + y/n" in 7pt
        # script); any prose word in between is wider than this.
        return overlap>h*.5 and max(ra.x0,rb.x0)-min(ra.x1,rb.x1)<=min(a['size'],b['size'])*.9
    def bullet(island):
        glyphs=[m for m in island['members'] if m['text'].strip(' \uf020')]
        return bool(glyphs) and all(re.match(r'Wingdings|ZapfDingbats',m['font'],re.I) or
                                    (re.match(r'Symbol',m['font'],re.I) and m['text'].strip(' \uf020')=='\uf0b7') for m in glyphs)
    absorbed=set()
    def between(a,t):
        # Punctuation in a text font ("[Z,E,x]^p , [Z,E,x]^q": the comma) standing
        # between two islands about to merge. It would stay behind inside the
        # merged box, unerased (37178 p5). The island takes it over; a word there
        # means the islands are not one formula.
        l,r=(a['bbox'],t['bbox']) if a['bbox'].x0<=t['bbox'].x0 else (t['bbox'],a['bbox'])
        found=[]
        for line in (a['line'],t['line']):
            for x in line['_items']:
                if 'members' in x or id(x) in absorbed or not x['text'].strip():continue
                b=x['bbox']
                if l.x1-.5<=(b[0]+b[2])/2<=r.x0+.5 and max(l.y0,r.y0)<=(b[1]+b[3])/2<=min(l.y1,r.y1):
                    if re.search(r'\w',x['text']):return None
                    found.append(x)
        return found
    def take(island,other,gap):
        island['members']=island['members']+other['members']+gap;island['bbox']|=other['bbox'];island['strong']=island['strong'] or other['strong']
        for x in gap:island['bbox']|=pymupdf.Rect(x['bbox'])
        absorbed.update(id(x) for x in gap)
    alive=list(candidates)
    row_lines=[l for b in blocks if b['type']==0 for l in b['lines'] if any(s['text'].strip() for s in l['spans'])]
    changed=True
    while changed:
        changed=False
        for a in alive:
            # On a prose row only a weak sub/superscript may move to another row.
            if not orphan(a) and (a['strong'] or max(m['size'] for m in a['members'])>=a['size']*.85):continue
            for t in alive:
                if t is a or t['line'] is a['line'] or not near(a,t):continue
                # Attach orphans toward prose-line islands, never the reverse.
                if orphan(t) and not orphan(a):continue
                # Two lines on different rows that each carry a math-roman word (CMR10
                # "start -1. The" / "start -1 only.") are prose tails, not one formula;
                # merged, they come back as spans of one line, in merge order,
                # and the rows swap (37297 p7).
                if orphan(a) and orphan(t) and abs(a['baseline']-t['baseline'])>=.8*min(a['size'],t['size']) and all(any(WEAK_MATH_FONT.match(m['font']) and re.search(r'[A-Za-z]{3,}',m['text']) for m in x['members']) for x in (a,t)):continue
                # A sub/superscript centres within its prose row; an inline
                # fraction of the NEXT row only touches it and must stay there.
                # The row is measured by its text glyphs: a tall hat or
                # radical in the line would stretch it into the next row.
                if orphan(a) and not orphan(t):
                    cy=(a['bbox'].y0+a['bbox'].y1)/2
                    text=[x['bbox'] for x in t['line']['_items'] if 'members' not in x and re.search(r'\w',x['text'])]
                    if text and not min(b[1] for b in text)-a['size']*.3<=cy<=max(b[3] for b in text)+a['size']*.3:continue
                gap=between(a,t)
                if gap is None:continue
                take(t,a,gap)
                a['merged']=True;alive.remove(a);changed=True;break
            if changed:break
    # A numerator and denominator in different PDF lines, stacked over the
    # same column ("1" over "2"), are one fraction.
    changed=True
    while changed:
        changed=False
        for a in alive:
            for t in alive:
                if t is a or t['line'] is a['line']:continue
                ra,rt=a['bbox'],t['bbox']
                h=min(ra.height,rt.height);ov=min(ra.x1,rt.x1)-max(ra.x0,rt.x0)
                # Both script size, barely apart: rows of prose leave a
                # wider gap between inline formulas of consecutive lines.
                if ov<min(ra.width,rt.width)*.6 or not -.2<=rt.y0-ra.y1<=1 or ra.y0>rt.y0:continue
                if any(m['size']>=x['size']*.85 for x in (a,t) for m in x['members']):continue
                a['members']=a['members']+t['members'];a['bbox']|=rt;a['strong']=a['strong'] or t['strong']
                t['merged']=True;alive.remove(t);changed=True;break
            if changed:break
    # Producers also split one formula across PDF lines on the same row
    # (V = {I_t}^N | _{t=1}); join adjacent islands of different lines.
    changed=True
    while changed:
        changed=False
        for a in alive:
            for t in alive:
                if t is a or t['line'] is a['line'] or not near(a,t):continue
                ra,rt=a['bbox'],t['bbox']
                if rt.x0<ra.x0:continue  # merge left-to-right only
                if rt.x0-ra.x1>min(a['size'],t['size'])*.6:continue
                # Same row: baselines agree. A sub/superscript of a different
                # row was already attached above.
                if abs(a['baseline']-t['baseline'])>min(a['size'],t['size'])*.3:continue
                gap=between(a,t)
                if gap is None:continue
                take(a,t,gap)
                t['merged']=True;alive.remove(t);changed=True;break
            if changed:break
    # Formulas without prose on their row (display equations) stay original.
    # Exception: an inline fraction typeset as its own PDF lines but sitting
    # inside a prose row ("of size x/2n rather than"); the row rejoins it.
    def prose_row(a):
        cy=(a['bbox'].y0+a['bbox'].y1)/2
        # A list bullet in its own PDF line sits a tab stop (~1.5 em) before its
        # item text (AP2112: Symbol \uf0b7 12pt left of "Output Voltage").
        reach=a['size']*(3 if bullet(a) else 1)
        # A row that has islands of its own ("segment, u_f = clip") is still
        # prose; only rows made of math alone are not.
        return next((l for l in row_lines if l is not a['line'] and not ('_items' in l and all('members' in x or not re.search(r'\w',x['text']) for x in l['_items'])) and re.search(r'[A-Za-z]{3,}',''.join(s['text'] for s in l['spans']))
                   and l['bbox'][1]<=cy<=l['bbox'][3]
                   and (0<=a['bbox'].x0-l['bbox'][2]<=a['size'] or 0<=l['bbox'][0]-a['bbox'].x1<=reach)),None)
    for a in alive:
        row=prose_row(a) if orphan(a) and a['strong'] else None
        # Scale relative to the row's prose, not the fraction's script size.
        if row:
            prose_span=max((s for s in row['spans'] if s['text'].strip()),key=lambda s:s['size'])
            a['size']=prose_span['size'];a['baseline']=prose_span['origin'][1]
            # Keep the bullet's tab stop: the translation would start right after it.
            if bullet(a) and row['bbox'][0]>a['bbox'].x1:a['after']=(row['bbox'][0]-a['bbox'].x1)/a['size']
        elif orphan(a):a['strong']=False
    result=[]
    for b in blocks:
        if b['type']!=0:continue
        for line in b['lines']:
            items=line.pop('_items',None)
            if items is None:continue
            spans=[]
            for x in items:
                if 'members' not in x:
                    if id(x) not in absorbed:spans.append(x)
                    continue
                if x.get('merged'):continue
                if not x['strong']:
                    # Fragments merged from several PDF lines of one row come back left to right.
                    ms=x['members'];cy=[(m['bbox'][1]+m['bbox'][3])/2 for m in ms]
                    if len(ms)>1 and max(cy)-min(cy)<=max(m['size'] for m in ms)*.8:ms=sorted(ms,key=lambda m:m['bbox'][0])
                    spans.extend(ms);continue
                first,last=x['members'][0]['text'],x['members'][-1]['text']
                prose_items=[y for y in items if 'members' not in y and y['text'].strip()]
                before=[y for y in prose_items if items.index(y)<items.index(x)]
                neighbour=(before[-1] if before else prose_items[0] if prose_items else x['members'][0])
                lead=first[:len(first)-len(first.lstrip())];trail=last[len(last.rstrip()):]
                index=len(result)
                if lead:spans.append({**neighbour,'text':lead,'bbox':(x['bbox'].x0,x['bbox'].y0,x['bbox'].x0,x['bbox'].y1)})
                spans.append({**neighbour,'text':chr(ISLAND_BASE+index),'bbox':tuple(x['bbox']),'size':x['size'],
                              'members':[list(m['bbox']) for m in x['members']],
                              'dingbat':bullet(x)})
                if trail:spans.append({**neighbour,'text':trail,'bbox':(x['bbox'].x1,x['bbox'].y0,x['bbox'].x1,x['bbox'].y1)})
                result.append({'bbox':list(x['bbox']),'baseline':x['baseline'],'size':x['size'],
                               'members':[list(m['bbox']) for m in x['members']],
                               **({'after':round(x['after'],4)} if 'after' in x else {})})
                line['bbox']=tuple(pymupdf.Rect(line['bbox'])|x['bbox'])
            line['spans']=spans
            if spans and not any(s['text'].strip() for s in spans):line['spans']=[]
            elif spans:
                r=pymupdf.Rect(spans[0]['bbox'])
                for s in spans[1:]:r|=pymupdf.Rect(s['bbox'])
                line['bbox']=tuple(r)
        # Lines emptied by attaching their glyphs to an island disappear.
        b['lines']=[l for l in b['lines'] if l['spans']]
    return result


def stack_blocks(blocks):
    """Rejoin the one-line blocks MuPDF cuts out of loosely spaced text.

    1.5-spaced preprints get a new block per line, so every line became its
    own slot and sentences were translated in pieces. Consecutive blocks join
    when the next row has the same size, a constant loose baseline
    pitch (1.4 to 2.3 lines; MuPDF keeps tighter lines in one block) and the
    stack's left edge, and the row before it was full (the next row's first
    word would not have fitted on it). A short row (heading, last line of a
    paragraph) closes the stack, so paragraph_parts only splits at an indent."""
    def rows(b):
        if b['type']!=0:return None
        lines=[l for l in b['lines'] if any(s['text'].strip() for s in l['spans'])]
        if not lines or any(tuple(l.get('dir',(1,0)))!=(1,0) for l in lines):return None
        result=[]
        for r in visual_rows(lines):
            d=max((s for s in r['spans'] if s['text'].strip()),key=lambda s:len(s['text']))
            # Width of the row's first word: whether it would have fitted on
            # the row above decides if that row ended by wrapping.
            first=next(s for s in r['spans'] if s['text'].strip());text=first['text'].strip()
            word=(first['bbox'][2]-first['bbox'][0])*len(text.split()[0])/max(len(first['text']),1)
            result.append((r['bbox'][0],r['bbox'][2],d['origin'][1],d['size'],word,styles.style(d)['bold']))
        return result
    infos=[rows(b) for b in blocks]
    # Only a page that MuPDF cut into mostly one-line blocks: elsewhere the
    # blocks it makes are paragraphs and joining neighbours would re-cut them.
    wide=[i for i in infos if i and any(r[1]-r[0]>=15*r[3] for r in i)]
    single=[i for i in wide if len(i)==1]
    if len(single)<8 or len(single)*2<len(wide):return blocks
    stacks=[];cur=[]
    for i,info in enumerate(infos):
        if info is None:
            if cur:stacks.append(cur);cur=[]
            stacks.append([i]);continue
        if cur:
            prev=infos[cur[-1]][-1];fs=prev[3];pitch=info[0][2]-prev[2]
            joined=(abs(info[0][3]-fs)<=.15*fs and info[0][5]==prev[5] and 1.4*fs<=pitch<=2.3*fs and
                    abs(info[0][0]-left)<=1.5*fs and (ref is None or abs(pitch-ref)<=.2*fs))
            if joined:
                ref=pitch if len(info)==1 else info[-1][2]-info[-2][2]
                left=min(left,*(r[0] for r in info));cur.append(i);continue
            stacks.append(cur)
        cur=[i];left=min(r[0] for r in info)
        ref=info[-1][2]-info[-2][2] if len(info)>1 else None
    if cur:stacks.append(cur)
    result=[]
    for st in stacks:
        right=max((r[1] for i in st if infos[i] for r in infos[i]),default=0)
        # A heading or a short address block is the longest row of its own
        # small stack: only a margin that two rows reach (justified text
        # ends within a fraction of a point) is a text edge.
        if sum(r[1]>=right-.3*r[3] for i in st if infos[i] for r in infos[i])<2:
            result.extend([i] for i in st);continue
        piece=[st[0]]
        for a,b in zip(st,st[1:]):
            x0,x1,_,fs,_,_=infos[a][-1]
            if x1+.25*fs+infos[b][0][4]>=right-fs and x1-x0>=15*fs:piece.append(b)
            else:result.append(piece);piece=[b]
        result.append(piece)
    merged=[]
    for piece in result:
        bs=[blocks[i] for i in piece]
        if len(bs)>1:
            box=pymupdf.Rect(bs[0]['bbox'])
            for b in bs[1:]:box|=pymupdf.Rect(b['bbox'])
            bs=[{**bs[0],'lines':[l for b in bs for l in b['lines']],'bbox':tuple(box)}]
        merged.append(bs[0])
    return merged


def blocks_for_page(page,regions):
    tables=[];protected=[];slots=[];retained=[]
    page_blocks=page.get_text('dict')['blocks']
    tall_glyph_boxes(page,page_blocks)
    small_caps(page_blocks)
    merge_negation(page_blocks)
    page_spans=[s for b in page_blocks if b['type']==0 for l in b['lines'] for s in l['spans'] if s['text'].strip()]
    islands=math_islands(page_blocks)
    # Inline math makes PDF producers start a new block mid-sentence on the
    # same row; rejoin it (word-space gap only, so columns stay apart).
    joined=[]
    for b in page_blocks:
        if b['type']!=0 or not b['lines']:
            if b['type']!=0:joined.append(b)
            continue
        if joined and joined[-1]['type']==0:
            pr,r=joined[-1]['lines'][-1]['bbox'],b['lines'][0]['bbox']
            h=min(pr[3]-pr[1],r[3]-r[1])
            horizontal=all(tuple(l.get('dir',(1,0)))==(1,0) for l in joined[-1]['lines']+b['lines'])
            # A display formula (no words, far taller than the row) that starts
            # where the row ends is not that row's continuation.
            display=r[3]-r[1]>2*(pr[3]-pr[1]) and not prose(body_text([b['lines'][0]]))
            # A gap holding only math glyphs (a formula kept native between
            # "assumption," and ". We can") still continues the row.
            gap=pymupdf.Rect(pr[2],max(pr[1],r[1]),r[0],min(pr[3],r[3]))
            inside=[s for s in page_spans if s['text'].strip() and pymupdf.Rect(s['bbox']).intersects(gap)]
            bridged=pr[2]<r[0]<=pr[2]+h*12 and inside and all(MATH_FONT.match(s['font']) or WEAK_MATH_FONT.match(s['font']) for s in inside)
            if horizontal and h>0 and not display and min(pr[3],r[3])-max(pr[1],r[1])>h*.6 and pr[2]-h<=r[0]<=(r[0] if bridged else pr[2]+h*.8):
                # Fold the continuation into the row it continues, so a
                # paragraph broken by several formulas stays one paragraph.
                prev=joined[-1];row=prev['lines'][-1];first=b['lines'][0]
                merged={**row,'spans':row['spans']+[{**first['spans'][0],'text':' ','bbox':(pr[2],r[1],pr[2],r[3])}]+first['spans'],
                        'bbox':tuple(pymupdf.Rect(row['bbox'])|pymupdf.Rect(first['bbox']))}
                joined[-1]={**prev,'lines':prev['lines'][:-1]+[merged]+b['lines'][1:],
                            'bbox':tuple(pymupdf.Rect(prev['bbox'])|pymupdf.Rect(b['bbox']))}
                continue
        joined.append(b)
    page_blocks=stack_blocks(joined)
    table_regions=[pymupdf.Rect(r['bbox']) for r in regions if r['kind']=='table' and r['confidence']>=0.6]
    formula_regions=[pymupdf.Rect(r['bbox'])+(-2,-2,2,2) for r in regions if r['kind']=='isolate_formula' and r['confidence']>=0.3]
    fractions=fraction_regions(page)
    for region in table_regions:
        found=page.find_tables(clip=region).tables
        good=[t for t in found if t.row_count>=2 and t.col_count>=2 and (pymupdf.Rect(t.bbox)&region).get_area()/region.get_area()>.55]
        if not good:
            # Borderless parameter lists need no invented grid. Use their
            # native text lines, retaining numeric-only lines and all artwork.
            retained.append({'kind':'table_native_fallback','bbox':list(region),'reason':'无可靠网格，改用原生文字行；不跳过整表'})
        tables.extend(good)
    protected.extend(formula_regions)
    for rect in formula_regions: retained.append({'kind':'formula','bbox':list(rect),'reason':'公式保持原版矢量内容'})
    figure_regions=[pymupdf.Rect(r['bbox']) for r in regions if r['kind']=='figure' and r['confidence']>=0.6]
    protected.extend(figure_regions)

    def add(rect,lines,kind):
        # Inspect raw spans BEFORE whitespace normalization/prose filtering.
        # Type3 fonts without usable Unicode maps may encode glyphs as C0/C1
        # controls (including tabs/newlines); these are not readable prose.
        raw_spans=[s for line in lines for s in line['spans']]
        # Tab/CR/LF are layout whitespace (InDesign "▪\t" bullets, titles
        # with "\n"), not unmapped glyphs; real Type3 garbage uses other codes.
        if any(any(unicodedata.category(c)=='Cc' and c not in '\t\n\r' for c in s['text']) for s in raw_spans):
            retained.append({'kind':'unmapped_glyph','bbox':list(rect),
                'text':''.join(s['text'] for s in raw_spans),
                'reason':'原 PDF 字体映射含控制字符，保留原始图形，不发送乱码翻译'})
            return
        text=body_text(lines)
        if not text or not prose(text): return
        spans=[s for line in lines for s in line['spans'] if s['text'].strip()]
        if not spans: return
        # Sub-3pt text (detector labels "person:1.00" in a photo grid) is
        # unreadable at 100% zoom; erasing hundreds of overlapping labels
        # scrambles the page's extracted numbers. Keep it as is.
        if max(s['size'] for s in spans)<3:
            retained.append({'kind':'tiny_text','bbox':list(rect),'text':text,'reason':'字号小于 3pt，保留原文'});return
        # Dense symbol expressions are not converted into guessed LaTeX math.
        words=re.findall(r'[A-Za-z][a-z]{2,}',text)
        # A fraction already clipped as an island of this text is safe; only
        # one whose glyphs are still loose prose spans must stop the slot.
        own=[pymupdf.Rect(islands[ord(c)-ISLAND_BASE]['bbox']) for c in text if ISLAND_BASE<=ord(c)<ISLAND_BASE+len(islands)]
        # (Detection may add a word of the row above as "numerator".)
        fraction=any((rect&r).get_area()>.3*r.get_area() and not any((r&o).get_area()>.5*r.get_area() for o in own) for r in fractions)
        # formula_prose pieces are already cut to native word boxes; sending
        # them through this branch again recurses forever.
        if kind!='formula_prose' and (fraction or ('=' in text and len(words)<3)):
            # Translate prose embedded in a condition without flattening any
            # subscript, sign or equation. Native word boxes are safe cuts.
            runs=[]
            for word in page.get_text('words',clip=rect):
                if not re.fullmatch(r'[(),.*]*[A-Za-z][a-z]{2,}[(),.*]*',word[4]): continue
                wr=pymupdf.Rect(word[:4])
                if runs and abs(runs[-1].y0-wr.y0)<1 and 0<=wr.x0-runs[-1].x1<max(5,wr.height):runs[-1] |= wr
                else:runs.append(wr)
            for wr in runs:
                wl=[l for b in page.get_text('dict',clip=wr+(-.1,-.1,.1,.1))['blocks'] if b['type']==0 for l in b['lines']]
                if wl: add(wr,wl,'formula_prose')
            retained.append({'kind':'fraction' if fraction else 'formula','bbox':list(rect),'text':text,'reason':'公式保持原版；可分离的英文词另行翻译'});return
        directions={tuple(l.get('dir',(1,0))) for l in lines}
        rotation=90 if directions=={(0.0,-1.0)} else -90 if directions=={(0.0,1.0)} else 0
        if directions!={(1,0)} and not rotation:
            retained.append({'kind':'rotated_text','bbox':list(rect),'text':text,'reason':'旋转标注保持原样'});return
        if any(0xE000<=ord(c)<=0xF8FF or c=='\ufffd' for c in text):
            retained.append({'kind':'unmapped_glyph','bbox':list(rect),'text':text,'reason':'字体映射不确定'});return
        # Adjacent superscripts can overlap a prose span's glyph bounding box
        # even when their baselines differ. MuPDF redacts every intersecting
        # glyph, so such a slot is unsafe: retain it instead of deleting math.
        selected={(tuple(s['bbox']),s['text']) for s in spans}
        member_boxes=[tuple(m) for s in spans for m in s.get('members',[])]
        spans=[s for s in spans if not s.get('members')]+[{**s,'bbox':m} for s in spans for m in s.get('members',[])]
        for other in page_spans:
            if (tuple(other['bbox']),other['text']) in selected or tuple(other['bbox']) in member_boxes:continue
            if prose(other['text']) or not re.search(r'\d|[\U0001D400-\U0001D7FF]',other['text']):continue
            r=pymupdf.Rect(other['bbox'])
            # Same-size numbers on an adjacent line (table rows) are not
            # superscripts. Exception: in tiny text (chart labels, <6pt) any
            # touching number such as an axis tick would be redacted too.
            body=max(s['size'] for s in spans)
            if (not re.search(r'[\U0001D400-\U0001D7FF]',other['text']) and other['size']>=body*.85
                and body>=6):continue
            if any((r&erase_core(s['bbox'])).get_area()>.05 for s in spans):
                retained.append({'kind':'adjacent_math_collision','bbox':list(rect),'text':text,
                    'reason':'文字擦除范围与相邻数学字符相交，保留原文以免损坏公式'})
                return
        # The body size must not be inflated by a larger local caption.
        sizes=Counter()
        for span in spans:sizes[round(span['size'],2)]+=len(span['text'])
        fontsize=sizes.most_common(1)[0][0]
        fmt=styles.style(max(spans,key=lambda s:len(s['text'])))
        rich=styles.rich_segment(lines,segment(text),segment)
        bold=fmt['bold']
        rect=rect & page.rect
        used={ord(c)-ISLAND_BASE:islands[ord(c)-ISLAND_BASE] for c in text if ISLAND_BASE<=ord(c)<ISLAND_BASE+len(islands)}
        # Island sentinels are page-global; renumber per slot so the cache key
        # depends only on this text, not on how many formulas came earlier.
        local={chr(ISLAND_BASE+k):chr(ISLAND_BASE+n) for n,k in enumerate(sorted(used))}
        if local:
            text=text.translate({ord(a):b for a,b in local.items()})
            lines=[{**l,'spans':[{**s,'text':s['text'].translate({ord(a):b for a,b in local.items()})} for s in l['spans']]} for l in lines]
        rich=styles.rich_segment(lines,segment(text),segment)
        slots.append({'bbox':list(rect),'kind':kind,'font':round(fontsize,2),'bold':bold,'rotation':rotation,
                      'source':text,'segment':rich,'style':fmt,
                      'islands':[used[k] for k in sorted(used)],
                      'erase_rects':[list(s['bbox']) for s in spans]})

    for table in list(tables):
        try: cells=validated_table_cells(page,table)
        except ValueError as e:
            # Same as a borderless table: native text lines, not a skipped table.
            retained.append({'kind':'table_native_fallback','bbox':list(table.bbox),'reason':str(e)})
            tables.remove(table)
            continue
        for rect in cells:
            lines=[l for b in page.get_text('dict',clip=rect)['blocks'] if b['type']==0 for l in b['lines']]
            if not lines: continue
            # Interior padding keeps translated glyphs away from grid lines.
            before=len(slots)
            add(rect+(2,1,-2,-1),lines,'table_cell')
            if len(slots)>before and slots[-1]['kind']=='table_cell' and abs(rect.y0-table.bbox[1])<1:
                slots[-1]['table_header']=True
                columns=header_columns(page,rect,table)
                if columns:slots[-1]['columns']=columns

    table_boxes=[pymupdf.Rect(t.bbox) for t in tables]
    furniture=[pymupdf.Rect(r['bbox']) for r in regions if r['kind']=='abandon' and r['confidence']>=.5]
    for b in page_blocks:
        if b['type']!=0: continue
        # Formula lines inside a protected region stay out before rows merge:
        # merged into a prose row, their centre no longer falls inside it.
        def boxed(l):
            r=pymupdf.Rect(l['bbox']);return any((r.tl+r.br)/2 in p for p in protected) and not prose(body_text([l]))
        lines=visual_rows([l for l in b['lines'] if body_text([l]) and not boxed(l)])
        if not lines: continue
        rect=pymupdf.Rect(lines[0]['bbox'])
        for l in lines[1:]:rect |= pymupdf.Rect(l['bbox'])
        center=pymupdf.Point((rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2)
        footer=center.y>page.rect.height*.91 and re.search(r'https?://|www\.',body_text(lines))
        if footer or (any(center in r for r in furniture) and (center.y<page.rect.height*.085 or center.y>page.rect.height*.91)):
            if prose(body_text(lines)):
                retained.append({'kind':'page_furniture','bbox':list(rect),'text':body_text(lines),
                    'reason':'页眉页脚候选区保留原文；若实际为正文，需复核结构报告'})
            continue  # manufacturer/version/page furniture
        if any(rect.intersects(r) for r in protected+table_boxes):
            # A native PDF block may cover a whole page or several columns.
            # Restrict protection to actual lines inside the protected area.
            # Consecutive free lines are still one paragraph: per-line slots
            # break sentences and leave ragged half-empty rows.
            free=[]
            def flush():
                for part in (g for p in paragraph_parts(free) for g in width_groups(p)) if free else []:
                    r=pymupdf.Rect(part[0]['bbox'])
                    for l in part[1:]:r |= pymupdf.Rect(l['bbox'])
                    add(r,part,'paragraph' if len(part)>1 else 'label')
                free.clear()
            for line in lines:
                lr=pymupdf.Rect(line['bbox']);lc=(lr.tl+lr.br)/2
                if any(lc in r for r in table_boxes):flush();continue
                if any(lc in r for r in protected):
                    flush()
                    if prose(body_text([line])):
                        if any(lc in r for r in figure_regions):
                            retained.append({'kind':'figure_text','bbox':list(lr),
                                'text':body_text([line]),'reason':'图内文字保持原样'})
                        else:
                            retained.append({'kind':'protected_region_text','bbox':list(lr),
                                'text':body_text([line]),'reason':'此文字行与原版公式区域相交'})
                    continue
                if free and (lr.y0-free[-1]['bbox'][3]>lr.height*.8 or lr.x0>free[-1]['bbox'][2]):flush()
                free.append(line)
            flush()
            continue
        lengths=[len(''.join(s['text'] for s in l['spans']).strip()) for l in lines]
        leaders=any(re.search(r'-{5,}',s['text']) for l in lines for s in l['spans'])
        listlike=len(lines)>1 and (sum(lengths)/len(lengths)<42 or leaders)
        if listlike and not leaders:
            # Narrow boxes (figure callouts, Q/A panels) wrap one sentence over
            # many short lines; keep such runs together instead of per line.
            for run in continuation_runs(lines):
                r=pymupdf.Rect(run[0]['bbox'])
                for l in run[1:]:r |= pymupdf.Rect(l['bbox'])
                add(r,run,'paragraph' if len(run)>1 else 'label')
        elif listlike:
            for line in lines: add(pymupdf.Rect(line['bbox']),[line],'label')
        else:
            # A PDF block may hold a whole column with several run-in
            # headed paragraphs. Long requests make models stop early and
            # drop text, so split at paragraph starts (see paragraph_parts).
            for part in (g for p in paragraph_parts(lines) for g in width_groups(p)):
                r=pymupdf.Rect(part[0]['bbox'])
                for l in part[1:]:r |= pymupdf.Rect(l['bbox'])
                add(r,part,'paragraph' if len(part)>1 else 'label')
    # Duplicate/overlapping native blocks must not silently overwrite one another.
    final=[]
    for s in sorted(merge_continued_labels(slots),key=lambda s:(s['bbox'][1],s['bbox'][0])):
        r=pymupdf.Rect(s['bbox'])
        if any((r&pymupdf.Rect(x['bbox'])).get_area()>.4*min(r.get_area(),pymupdf.Rect(x['bbox']).get_area()) for x in final):
            retained.append({'kind':'overlapping_text','bbox':s['bbox'],'text':s['source'],'reason':'重叠文字框需复核'})
        else: final.append(s)
    return final,retained


REFERENCES_HEAD=re.compile(r'(?:\d+\.?|[IVX]+\.)?\s*(?:References|REFERENCES|R\s?EFERENCES|Bibliography|BIBLIOGRAPHY|Literature Cited|LITERATURE CITED)')
AFTER_REFERENCES=re.compile(r'\(?(?:Appendix|APPENDIX|Appendices|APPENDICES|Supplementary|SUPPLEMENTARY|Supplemental|SUPPLEMENTAL)\b|'
                            r'(?:NeurIPS Paper )?Checklist\b|CHECKLIST\b|[A-Z](?:\.\s*[A-Z]|\s+[A-Z]|$)|\d+\.?\s+[A-Z][a-z]')
CAPTION=re.compile(r'(?:Fig\.|Figure|FIGURE|Table|TABLE|Algorithm)\s*\d')


def reading_key(i,rect,width):
    """(page, column, y) reading order; a two-column page reads left first."""
    return [i,int(rect.x0>=width/2),(rect.y0+rect.y1)/2]


def references_ranges(document):
    """Reading-order spans [start, end) of reference lists: from a References /
    Bibliography heading to the next top-level heading (Appendix, "A." ...,
    Supplementary, Checklist) or the end. User 2026-10-29: references may stay
    English, appendices after them are translated. A heading is a whole line
    at least body size, bold or upper case; "references" inside a sentence and
    small drawing labels (PCA9685 package pages, 6.8pt) are not."""
    sizes=Counter();lines=[]
    for i,page in enumerate(document):
        for b in page.get_text('dict')['blocks']:
            for l in b.get('lines',[]):
                spans=[s for s in l['spans'] if s['text'].strip()]
                if not spans:continue
                for s in spans:sizes[round(s['size'],1)]+=len(s['text'].strip())
                lines.append((i,pymupdf.Rect(l['bbox']),''.join(s['text'] for s in l['spans']).strip(),max(s['size'] for s in spans),
                              any(styles.style(s)['bold'] for s in spans),page.rect.width))
    if not sizes:return []
    body=sizes.most_common(1)[0][0]
    lines.sort(key=lambda x:reading_key(x[0],x[1],x[5]))
    ranges=[];head=None
    for i,r,text,size,bold,w in lines:
        if head is None:
            if size>=body-1 and (bold or text.isupper() or size>body+.5) and REFERENCES_HEAD.fullmatch(text):
                head=size;ranges.append([[i,int(r.x0>=w/2),r.y1],[len(document),0,0]])
        # A lone capital is an initial ("Faye Y Wu") unless bold or larger.
        elif size>=head-.3 and (bold or size>body+.5 or text.isupper() and len(text)>1) and (AFTER_REFERENCES.match(text) or size>head+1):
            ranges[-1][1]=reading_key(i,r,w)[:2]+[r.y0];head=None
    # A table-of-contents line "References" is followed by headings, not by
    # entries: a real list has a year on most of its lines.
    def entries(a,b):
        inside=[t for i,r,t,*_,w in lines if a<reading_key(i,r,w)<b]
        return inside and sum(bool(re.search(r'\b(?:19|20)\d\d\b',t)) for t in inside)>=.05*len(inside)
    return [x for x in ranges if entries(*x)]


def in_references(ranges,i,rect,width):
    k=reading_key(i,rect,width)
    return any(a<k<b for a,b in ranges)


def prepare(pdf,out):
    layout_path=out/'layout.json'; sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    layout=json.loads(layout_path.read_text(encoding='utf8')) if layout_path.exists() else {}
    if layout.get('sha256')!=sha:
        inspect(pdf,layout_path)
        layout=json.loads(layout_path.read_text(encoding='utf8'))
    pages=[]
    with pymupdf.open(pdf) as document:
        refs=references_ranges(document)
        for i,page in enumerate(document):
            if len(page.get_text().strip())<20:
                # Blank/image-only pages (covers, scans) are passed through
                # untranslated instead of aborting the whole document.
                pages.append({'width':page.rect.width,'height':page.rect.height,'slots':[],
                    'retained':[{'kind':'no_text_layer','bbox':list(page.rect),'reason':'无文字层，原样保留；扫描件需 OCR'}]})
                print(f'[结构] {i+1}/{len(document)} 页：无文字层，原样保留',flush=True)
                continue
            slots,retained=blocks_for_page(page,layout['pages'][i]['regions'])
            for s in [s for s in slots if in_references(refs,i,pymupdf.Rect(s['bbox']),page.rect.width)]:
                slots.remove(s)
                retained.append({'kind':'references','bbox':s['bbox'],'text':s['source'],'reason':'参考文献保留原文'})
            pages.append({'width':page.rect.width,'height':page.rect.height,'slots':slots,'retained':retained})
            print(f'[结构] {i+1}/{len(document)} 页：{len(slots)} 个可排版文本框，{len(retained)} 个明确保留项',flush=True)
    if all(r['kind']=='no_text_layer' for p in pages for r in p['retained']) and not any(p['slots'] for p in pages):
        raise ValueError('整份 PDF 没有文字层（扫描件）；本入口尚未接入 OCR，不生成伪完整译文。')
    plan={'source_sha256':sha,'source':str(pdf),'pages':pages,'references':refs}
    if refs:print(f'[结构] 参考文献 {len(refs)} 处保留原文（第 '+'、'.join(f'{a[0]+1}–{min(b[0]+1,len(pages))}' for a,b in refs)+' 页）',flush=True)
    unmapped=sum(r['kind']=='unmapped_glyph' for p in pages for r in p['retained'])
    if unmapped:print(f'[文字检查] {unmapped} 个区域存在字体映射异常，保留原文；需 OCR 才能可靠翻译，不发送乱码消费额度',flush=True)
    latex.write_json(out/'structure.json',plan)
    return plan


def orientation_normalized(pdf,out):
    """Pages with /Rotate get an unrotated copy so text coordinates, redaction
    and the overlay all share one coordinate system. Unrotated input is used as is."""
    with pymupdf.open(pdf) as d:
        if not any(p.rotation for p in d):return pdf
        target=out/(pdf.stem+'.unrotated.pdf')
        if target.exists() and target.stat().st_mtime>=pdf.stat().st_mtime:return target
        for p in d:
            if p.rotation:p.remove_rotation()
        d.save(target,garbage=3,deflate=True)
    print('[结构] 已将旋转页面规范化为正向坐标（显示方向不变）',flush=True)
    return target


def glyph_code_names(doc):
    """Simple fonts without /ToUnicode whose /Differences name every glyph
    "G<decimal code>" (PageMaker + Distiller: G72 = 'H'). MuPDF extracts their
    text as control characters; return {font xref: {code: unicode}}."""
    fonts={}
    for x in range(1,doc.xref_length()):
        if doc.xref_get_key(x,'Type')!=('name','/Font') or doc.xref_get_key(x,'Subtype')[1] not in ('/Type1','/TrueType'):continue
        if doc.xref_get_key(x,'ToUnicode')[0]!='null' or re.search(r'Symbol|Wingding|Dingbat',doc.xref_get_key(x,'BaseFont')[1],re.I):continue
        enc=doc.xref_get_key(x,'Encoding')
        if enc[0]=='xref':enc=('dict',doc.xref_object(int(enc[1].split()[0])))
        m=re.search(r'/Differences\s*\[([^\]]*)\]',enc[1]) if enc[0]=='dict' else None
        if not m:continue
        tokens=re.findall(r'\d+|/[^\s/\[\]]+',m[1]);code=0;names={}
        for t in tokens:
            if t[0]!='/':code=int(t);continue
            names[code]=t[1:];code+=1
        if not names or not all(re.fullmatch(r'G\d{2,3}',n) and 32<=int(n[1:])<=255 for n in names.values()):continue
        fonts[x]={c:bytes([int(n[1:])]).decode('cp1252',errors='replace') for c,n in names.items()}
    return fonts


def glyph_code_mapped(pdf,out):
    """A copy whose G<code> fonts (see glyph_code_names) carry a /ToUnicode."""
    with pymupdf.open(pdf) as d:
        fonts=glyph_code_names(d)
        if not fonts:return pdf
        target=out/(pdf.stem+'.mapped.pdf')
        for x,table in fonts.items():
            rows=''.join(f'<{c:02X}> <{ord(u):04X}>\n' for c,u in sorted(table.items()) if u!='\ufffd')
            cmap=('/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /pdfzh def /CMapType 2 def\n'
                  '1 begincodespacerange <00> <FF> endcodespacerange\n'
                  f'{rows.count(chr(10))} beginbfchar\n{rows}endbfchar endcmap CMapName currentdict /CMap defineresource pop end end')
            s=d.get_new_xref();d.update_object(s,'<<>>');d.update_stream(s,cmap.encode())
            d.xref_set_key(x,'ToUnicode',f'{s} 0 R')
        d.save(target,garbage=3,deflate=True)
    print(f'[结构] {len(fonts)} 个字体缺少文字映射（字形名 G+编码），已按编码补齐',flush=True)
    return target


def check_typesetting_environment():
    """Fail before translation spend if this machine cannot typeset locally."""
    missing=[name for name in ('xelatex','latexmk','fc-match') if not shutil.which(name)]
    if missing: raise RuntimeError('缺少本地排版依赖：'+', '.join(missing)+'；请安装 TeX Live 并重新打开终端。')
    for font in ('Microsoft YaHei','Arial','Cambria Math','Segoe UI Symbol'):
        result=subprocess.run(['fc-match','-f','%{family}',font],
            capture_output=True,text=True,encoding='utf8',errors='replace',timeout=20)
        if result.returncode or font not in result.stdout:
            raise RuntimeError('未找到 '+font+' 字体；在翻译前停止，未消费额度。')


PREAMBLE=r'''\documentclass{article}
\usepackage{fontspec,xeCJK,graphicx,eso-pic,xcolor}
\usepackage[absolute,overlay]{textpos}
\usepackage[paperwidth=WIDTHbp,paperheight=HEIGHTbp,margin=0bp]{geometry}
\setmainfont{Arial}
\newfontfamily\pdfzhsymbol{Cambria Math}[AutoFakeSlant=0.2]
\setCJKmainfont{Microsoft YaHei}[AutoFakeSlant=0.2]
\pagestyle{empty}\setlength{\parindent}{0pt}
\textblockorigin{0bp}{0bp}
\newbox\slotbox
\newif\ifslotcenter
\def\slotlinespread{1.13}
\newdimen\slotminfont\slotminfont=5.5pt
\newdimen\slotfont\newdimen\slotbaseline\newdimen\slotheight
\newcommand{\pdfzhisland}[3]{\raisebox{-#3\slotfont}{\includegraphics[page=#1,height=\dimexpr#2\slotfont+#3\slotfont\relax]{islands.pdf}}}
\newcommand{\slot}[5]{%
\slotfont=#4pt\relax
\loop
  \slotbaseline=\slotlinespread\slotfont\relax
  \setbox\slotbox=\vbox{\hsize=#2bp\relax\fontsize{\the\slotfont}{\the\slotbaseline}\selectfont\raggedright\noindent#5\par}%
  \slotheight=\dimexpr\ht\slotbox+\dp\slotbox\relax
  \ifdim\slotheight>#3bp\relax
    \ifdim\slotfont>\slotminfont\relax
      \advance\slotfont by -0.25pt\relax
      \ifdim\slotfont<\slotminfont\relax\slotfont=\slotminfont\relax\fi
    \else\slotheight=0pt\relax\fi
  \else\slotheight=0pt\relax\fi
\ifdim\slotheight>0pt\relax\repeat
\ifdim\dimexpr\ht\slotbox+\dp\slotbox\relax>#3bp\relax
  \typeout{PDFZH-OVERFLOW:#1}%
\fi
\typeout{PDFZH-FONT:#1:\the\slotfont}%
\typeout{PDFZH-LEADING:#1:\the\slotbaseline}%
\ifslotcenter\vbox to #3bp{\vfil\box\slotbox\vfil}%
\else\box\slotbox\fi}
\begin{document}
'''


def column_translations(slot):
    columns=slot.get('columns',[])
    values=[v for v in re.split(r'[\s,，;；|]+',slot['translation'].strip()) if v]
    if len(values)==len(columns):return values
    terms={'min':'最小值','minimum':'最小值','typ':'典型值','typical':'典型值','max':'最大值','maximum':'最大值'}
    if columns and all(c['source'].lower() in terms for c in columns):
        return [terms[c['source'].lower()] for c in columns]
    return []


def lost_number_slots(pdf,rendered,page,boxes,tokens):
    """Slot boxes whose rendered words (by centre) hold fewer of `tokens` than the source words there."""
    def count(doc,box):
        found=Counter()
        for w in doc[page-1].get_text('words'):
            if box.contains(pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2)):found.update(t for t in NUMBERS.findall(w[4]) if t in tokens)
        return found
    with pymupdf.open(pdf) as source,pymupdf.open(rendered) as result:
        return [b for b in boxes if count(source,pymupdf.Rect(b))-count(result,pymupdf.Rect(b))]


def check_complete(pdf):
    """An interrupted download lacks the trailing %%EOF; only the tail is checked
    because valid files may carry whitespace or junk bytes after it."""
    with open(pdf,'rb') as f:
        f.seek(0,os.SEEK_END);f.seek(max(0,f.tell()-2048));tail=f.read()
    if b'%%EOF' not in tail:raise ValueError('PDF 文件不完整（可能下载中断），请重新下载：'+str(pdf))


def publish_copy(source,destination):
    """Atomic publication; an open Windows reader must not abort a good build."""
    with tempfile.TemporaryDirectory(prefix='.publish-',dir=destination.parent) as temp:
        staged=Path(temp)/'result.pdf';shutil.copy2(source,staged)
        try:os.replace(staged,destination)
        except PermissionError:
            alternate=destination.with_name(destination.stem+'-修订.pdf');number=2
            while alternate.exists():
                alternate=destination.with_name(destination.stem+f'-修订{number}.pdf');number+=1
            os.replace(staged,alternate)
            print('[输出] 原文件正在使用，保留原文件并另存修订版：'+str(alternate),flush=True)
            return alternate
    return destination


def fall_back(plan,page,bboxes,kind,reason):
    """Keep the original text of these slots (None: the whole page). Returns
    how many slots were restored, so callers never loop without progress."""
    info=plan['pages'][page];before=len(info['slots'])
    for s in [s for s in info['slots'] if bboxes is None or s['bbox'] in bboxes]:
        info['slots'].remove(s)
        info['retained'].append({'kind':kind,'bbox':s['bbox'],'text':s['source'],
            **({'translation':s['translation']} if 'translation' in s else {}),'reason':reason})
    return before-len(info['slots'])


def failing_tex_slots(project,valid):
    """Slots named by XeLaTeX's file:line errors (-file-line-error)."""
    log=project/'build'/'main.log'
    text=log.read_text(encoding='utf8',errors='replace') if log.exists() else ''
    lines=(project/'main.tex').read_text(encoding='utf8').splitlines()
    ids=set()
    for number in re.findall(r'main\.tex:(\d+):',text):
        for line in reversed(lines[:int(number)]):
            m=re.search(r'\\slot\{(\d+)\}',line)
            if m:ids.add(int(m[1]));break
    return [s for s in valid if s['id'] in ids]


def compile_overlay(pdf,out,plan,cache,*,allow_fallback=True,rounds=0):
    """Every failed check restores the original text of the slots (or, when
    no slot can be blamed, the page) responsible and rebuilds; a document is
    published even when a check still fails, flagged for review."""
    project=out/'project';project.mkdir(exist_ok=True)
    tex=[PREAMBLE.replace('WIDTH',str(plan['pages'][0]['width'])).replace('HEIGHT',str(plan['pages'][0]['height']))]
    total=0;valid=[]
    background=project/'background.pdf'
    collisions=[]
    with normalized_document(pdf) as doc,pymupdf.open(pdf) as original:
        position_errors=[]
        write_islands(original,plan,project/'islands.pdf')
        for pi,info in enumerate(plan['pages']):
            print(f'[排版准备] {pi+1}/{len(plan["pages"])} 页：保护原图、公式和保留文字',flush=True)
            page=doc[pi]
            for s in list(info['slots']):
                value=cache.get(latex.digest(s['segment']['text']))
                try:
                    if value is None:raise ValueError('未取得合格译文（网络、额度或多次未通过保护检查）')
                    s['translation']=translated_text(s['segment'],value)
                    if s['segment'].get('style_events'):
                        s['translation_runs']=styles.decoded_runs(s['segment'],value)
                    # Names, e-mails and model IDs come back unchanged; the
                    # original glyphs (font, kerning, size) beat a re-render.
                    if re.sub(r'\s+','',s['translation'])==re.sub(r'\s+','',s['source']):
                        info['slots'].remove(s)
                        info['retained'].append({'kind':'identical_translation','bbox':s['bbox'],'text':s['source'],
                            'reason':'译文与原文相同，保留原版字形'})
                        continue
                except ValueError as e:
                    # One unverifiable translation keeps its original English;
                    # it must not block the rest of the document.
                    info['slots'].remove(s)
                    info['retained'].append({'kind':'translation_check_failed','bbox':s['bbox'],
                        'text':s['source'],'reason':'译文未通过数字/样式校验，保留原文：'+str(e)[:160]})
                    print(f'[校验] 第 {pi+1} 页 1 个文本框译文未通过校验，保留原文',flush=True)
                    continue
                # Erase only text actually extracted for translation, not the
                # empty area of a union box that can contain a neighbouring
                # superscript or a formula on a different baseline.
            # MuPDF can shift later glyphs of a TJ run that changes font size
            # mid-run (small caps). Find such slots on scratch copies first and
            # keep their original text; never abort the whole document.
            for s in shifting_slots(doc,pi,original[pi],info['slots']):
                info['slots'].remove(s)
                info['retained'].append({'kind':'redaction_shift','bbox':s['bbox'],'text':s['source'],
                    'reason':'擦除会移动同一文字串中保留的字形，保留原文'})
            for s in info['slots']:
                for box in s.get('erase_rects',[s['bbox']]):
                    page.add_redact_annot(erase_core(box,bool(s.get('rotation'))),fill=None)
            if info['slots']:
                # Never erase images or lines: numeric cells, plots and wire
                # geometry retain their original PDF operators.
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,text=pymupdf.PDF_REDACT_TEXT_REMOVE)
                # Fraction bars are vector rules, not glyphs: an island moved
                # into the translation would leave its bar behind. Remove only
                # rules lying wholly inside a moved island.
                moved=[pymupdf.Rect(i['bbox'])+(-1,-.5,1,.5) for s in info['slots'] for i in s.get('islands',[])]
                if moved:
                    for r in moved:page.add_redact_annot(r,fill=None)
                    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                        graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,text=pymupdf.PDF_REDACT_TEXT_NONE)
            # A retained line can sit inside the larger layout box even when
            # none of its glyphs were erased. Do not print Chinese over it.
            # Short glyphs a union span box could not reach (LoRA's "rq = rv",
            # BatchNorm's "Var[x]") survive the line strips as small words; a
            # word-level strip removes them exactly. Words the plan already
            # keeps (overlapping_text) and island glyphs are never erased.
            keep=info.get('retained',[])
            for s in list(info['slots']):
                box=pymupdf.Rect(s['bbox'])
                hits=[w for w in page.get_text('words') if pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2) in box]
                if not hits:continue
                islands=[pymupdf.Rect(i['bbox']) for i in s.get('islands',[])]
                zones=[pymupdf.Rect(r['bbox']) for r in keep if r.get('bbox')]
                remaining=[w for w in hits if not any(i.contains(pymupdf.Rect(w[:4])) for i in islands)
                           and not any((z & pymupdf.Rect(w[:4])).get_area()>.5*pymupdf.Rect(w[:4]).get_area() for z in zones)]
                for w in remaining:
                    page.add_redact_annot(erase_core(w[:4]),fill=None)
                if remaining:
                    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,text=pymupdf.PDF_REDACT_TEXT_REMOVE)
                # Words of a plan-retained region (overlapping_text) do not
                # collide with the translation: the region keeps its own
                # English anyway, and the slot's box may share the strip.
                rest=[w for w in page.get_text('words') if pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2) in box]
                if any(not any((z & pymupdf.Rect(w[:4])).get_area()>.5*pymupdf.Rect(w[:4]).get_area() for z in zones) for w in rest):
                    collisions.append((pi,s))
                # ponytail: without this zones-exemption the whole paragraph
                # falls back to English whenever a kept region sits inside it.
            if info['slots']:restore_rotated(page,original[pi],info['slots'])
            errors=retained_position_errors(original[pi],page,info['slots'])
            if errors:position_errors.append({'page':pi+1,'changed_words':errors})
        latex.write_json(out/'background-qa.json',{'retained_position_errors':position_errors})
        if position_errors:
            restored=sum(fall_back(plan,e['page']-1,None,'position_fallback',
                '擦除后保留文字的位置发生变化，本页恢复原文') for e in position_errors)
            if restored:
                print(f'[排版] {len(position_errors)} 页擦除后保留文字移位，这些页恢复原文后重排',flush=True)
                return compile_overlay(pdf,out,plan,cache,allow_fallback=allow_fallback,rounds=rounds)
        drop_long_strings(doc)
        doc.save(background,garbage=4,deflate=True)
    if collisions:
        for pi,s in collisions:
            plan['pages'][pi]['slots'].remove(s)
            plan['pages'][pi]['retained'].append({'kind':'retained_text_collision','bbox':s['bbox'],
                'text':s['source'],'translation':s['translation'],'reason':'排版框内仍有保留文字，恢复原文以免重叠'})
        print(f'[排版] {len(collisions)} 个文本框与保留文字相交，恢复原文并记录',flush=True)
        return compile_overlay(pdf,out,plan,cache,allow_fallback=allow_fallback,rounds=rounds)
    for pi,info in enumerate(plan['pages']):
        # Per-page paper size: datasheets mix Letter, A4 and landscape pages.
        pw,ph=info['width'],info['height']
        tex.append(r'\paperwidth='+f'{pw:.3f}'+r'bp\paperheight='+f'{ph:.3f}'+r'bp\pdfpagewidth=\paperwidth\pdfpageheight=\paperheight')
        tex.append(r'\AddToShipoutPictureBG*{\AtPageLowerLeft{\includegraphics[page='+str(pi+1)+r',width=\paperwidth,height=\paperheight]{background.pdf}}}')
        rendered_slots=[]
        for parent in info['slots']:
            values=column_translations(parent) if parent.get('columns') else []
            if values:
                for column,value in zip(parent['columns'],values):
                    rendered_slots.append({**parent,**column,'translation':value,'align':'center','parent_bbox':parent['bbox']})
            else:rendered_slots.append(parent)
        for s in rendered_slots:
            total+=1;x0,y0,x1,y1=s['bbox'];w=x1-x0;h=y1-y0
            sw,sh=(h,w) if s.get('rotation') else (w,h)
            runs=s.get('translation_runs') or [{'text':s['translation'],'style':s.get('style',{'color':0,'bold':s['bold'],'italic':False})}]
            value=styles.to_tex(runs,island_escape(s))
            # Long dot leaders are layout, not prose: TeX supplies the leader.
            value=re.sub(r'-{5,}',r'\\dotfill{}',value)
            if s.get('align')=='center':value=r'\centering '+value
            spread=1.35 if s['kind']=='paragraph' else 1.2 if s['kind']=='table_cell' else 1.13
            # Small figure labels: start at the source size and allow shrinking
            # to 80% of it, instead of a fixed 5.5pt floor larger than the box.
            start=min(s['font'],18);floor=min(5.5,round(start*.8,2))
            content=r'\slot{'+str(total)+'}{'+f'{sw:.3f}'+'}{'+f'{sh:.3f}'+'}{'+str(max(floor,start))+'}{'+value+'}'
            content=(r'\def\slotlinespread{'+str(spread)+r'}\slotminfont='+str(floor)+r'pt\relax'+
                     (r'\slotcentertrue' if s.get('table_header') else r'\slotcenterfalse')+content)
            if s.get('rotation'):content=r'\rotatebox{'+str(s['rotation'])+'}{'+content+'}'
            tex.append(r'\begin{textblock*}{'+f'{w:.3f}bp'+'}('+f'{x0:.3f}bp,{y0:.3f}bp'+')'+content+r'\end{textblock*}')
            valid.append({'id':total,'page':pi+1,**s})
        tex.append(r'\null'+(r'\newpage' if pi<len(plan['pages'])-1 else ''))
    tex.append(r'\end{document}')
    (project/'main.tex').write_text('\n'.join(tex),encoding='utf8')
    print(f'[排版] 正在本地编译 {len(plan["pages"])} 页、{total} 个文本框（不调用 AI）…',flush=True)
    try:rendered,warnings,critical=latex.compile_project(project,'main.tex')
    except RuntimeError:
        broken=failing_tex_slots(project,valid)
        if not broken:raise
        for s in broken:
            fall_back(plan,s['page']-1,[s.get('parent_bbox',s['bbox'])],'tex_error','该文本框编译出错，保留原文')
        print(f'[排版] {len(broken)} 个文本框编译出错，保留原文后重新编译',flush=True)
        return compile_overlay(pdf,out,plan,cache,allow_fallback=allow_fallback,rounds=rounds)
    log=(project/'build'/'main.log').read_text(encoding='utf8',errors='replace')
    overflows=re.findall(r'PDFZH-OVERFLOW:(\d+)',log)
    # TeX reports horizontal overflow separately from our height measurement.
    # Associate it with the generated slot, so the existing safe fallback
    # handles long unbreakable identifiers too (without disabling QA).
    tex_lines=(project/'main.tex').read_text(encoding='utf8').splitlines()
    for number in re.findall(r'Overfull \\[hv]box[^\n]* at lines (\d+)--\d+',log):
        line=int(number)-1
        match=re.search(r'\\slot\{(\d+)\}',tex_lines[line]) if 0<=line<len(tex_lines) else None
        if match:overflows.append(match[1])
    overflows=sorted(set(overflows),key=int)
    fonts={int(i):float(f) for i,f in re.findall(r'PDFZH-FONT:(\d+):([\d.]+)pt',log)}
    leading={int(i):float(f) for i,f in re.findall(r'PDFZH-LEADING:(\d+):([\d.]+)pt',log)}
    if overflows and allow_fallback:
        # Do not squeeze unreadable text into small diagram labels, truncate it,
        # or request a paid rewrite. Restore the original region and disclose it.
        ids={int(i) for i in overflows}
        for s in valid:
            if s['id'] not in ids: continue
            info=plan['pages'][s['page']-1]
            restore_bbox=s.get('parent_bbox',s['bbox'])
            info['slots']=[x for x in info['slots'] if x['bbox']!=restore_bbox]
            info['retained'].append({'kind':'translation_overflow','bbox':restore_bbox,
                'text':s['source'],'translation':s['translation'],
                'reason':'译文在最小字号仍放不下，恢复原文并记录待复核；不额外调用 API'})
        print(f'[排版] {len(ids)} 个窄框无法安全容纳译文，保留原文并记录；正在本地重排',flush=True)
        return compile_overlay(pdf,out,plan,cache,allow_fallback=False,rounds=rounds)
    report={'status':'review_required','source':str(pdf),'source_sha256':plan['source_sha256'],
            'engine':'native PDF + local DocLayout + XeLaTeX','author_source_used':False,
            'translation_blocks':len(valid),'overflow_slots':overflows,'critical_warnings':critical,
            'warnings':warnings,'min_font':min(fonts.values(),default=0),
            'retained_regions':[{'page':i+1,**r} for i,p in enumerate(plan['pages']) for r in p['retained']],
            'slots':[{k:v for k,v in s.items() if k!='segment'}|{'render_font':fonts.get(s['id']),'render_leading':leading.get(s['id'])} for s in valid]}
    with pymupdf.open(rendered) as result,pymupdf.open(pdf) as source:
        report['page_count_correct']=len(result)==len(source)
        missing=[];extra=[];where={}
        for i,(a,b) in enumerate(zip(source,result)):
            expected=Counter(NUMBERS.findall(a.get_text()))
            # Segment validation already permits only source-matched month
            # conversions. Audit rendering against those validated values too.
            for s in valid:
                if s['page']==i+1:
                    expected.subtract(NUMBERS.findall(s['source']))
                    expected.update(NUMBERS.findall(s['translation']))
            delta=expected-Counter(NUMBERS.findall(b.get_text()))
            surplus=Counter(NUMBERS.findall(b.get_text()))-expected
            # A formula clip's "=64" next to a footnote mark ".7" reads as one
            # decimal in the source; Chinese "。" splits it. Segment numbers
            # were already checked strictly, so accept exactly that split.
            for token in [t for t in delta.elements() if '.' in t]:
                parts=Counter(token.split('.'))
                if not parts-surplus:delta[token]-=1;surplus-=parts
            delta=+delta
            if delta:
                missing.append({'page':i+1,'missing':dict(delta)})
                where[i+1]=[pymupdf.Rect(w[:4]) for w in a.get_text('words') if set(NUMBERS.findall(w[4]))&set(delta)]
            if surplus:extra.append({'page':i+1,'extra':dict(surplus)})
        report['missing_numeric_tokens']=missing
        report['extra_numeric_tokens']=extra
        report['placeholders_remaining']=sum(len(latex.TOKEN.findall(p.get_text())) for p in result)
        report['untranslated_text_candidates']=untranslated_candidates(result,plan)
        report['translation_completeness']='needs_review' if report['untranslated_text_candidates'] else 'no_untranslated_lines_detected'
        layout_path=out/'layout.json'
        layout=json.loads(layout_path.read_text(encoding='utf8'))['pages'] if layout_path.exists() else None
        report['body_coverage']=body_coverage(source,plan,valid,layout)
    report['automated_checks_passed']=not(overflows or critical or report['missing_numeric_tokens'] or report['extra_numeric_tokens'] or report['placeholders_remaining']) and report['page_count_correct']
    if not report['automated_checks_passed'] and rounds<3:
        # Numbers/placeholders are checked per page: restore the original
        # text of the failing pages only, the rest stays translated.
        with pymupdf.open(rendered) as result:
            pages={e['page'] for e in missing+extra}|{i+1 for i,p in enumerate(result) if latex.TOKEN.search(p.get_text())}
        latex.write_json(out/'page-check-qa.json',{'round':rounds,'missing':missing,'extra':extra})
        bad={e['page']:set(e['missing']) for e in missing}
        for e in extra:bad.setdefault(e['page'],set()).update(e['extra'])
        restored=0
        for p in sorted(pages):
            # First restore only the slots carrying a mismatched number (tiny
            # overlapping chart labels extract as scrambled digits); the
            # whole page only when no slot can be blamed or it fails again.
            blamed=[s['bbox'] for s in valid if s['page']==p and set(NUMBERS.findall(s['source']+' '+s['translation']))&bad.get(p,set())]
            # Single digits missing from one merged formula ("I3"+"1" -> "31")
            # match nearly every slot (37043 p7: 9 innocent body boxes). A
            # surplus token is specific: blame only its slots first.
            surplus={t for e in extra if e['page']==p for t in e['extra']}
            first=[s['bbox'] for s in valid if s['page']==p and set(NUMBERS.findall(s['source']+' '+s['translation']))&surplus]
            # Only-missing pages: a slot whose own area lost a number (36599 p8:
            # the "3D" subscripts of one formula island) beats "every slot with a 3".
            if not first:first=lost_number_slots(pdf,rendered,p,[s['bbox'] for s in valid if s['page']==p],bad.get(p,set()))
            if first and rounds==0:blamed=first
            else:
                # A slot box reaching into a table row erases numbers its text
                # does not carry (36312 p7): blame it by position too.
                blamed+=[s['bbox'] for s in valid if s['page']==p and s['bbox'] not in blamed
                         and any(r.intersects(pymupdf.Rect(s['bbox'])) for r in where.get(p,[]))]
            if blamed and rounds<2:
                restored+=fall_back(plan,p-1,blamed,'number_check_fallback','该文本框数字检查未通过，恢复原文')
            else:
                restored+=fall_back(plan,p-1,None,'page_check_fallback','本页数字/占位符检查未通过，整页恢复原文')
        if restored:
            print(f'[质检] 第 {",".join(map(str,sorted(pages)))} 页检查未通过，恢复原文后重排；其余页保持译文',flush=True)
            return compile_overlay(pdf,out,plan,cache,allow_fallback=allow_fallback,rounds=rounds+1)
    report['status']='review_required' if report['automated_checks_passed'] else 'checks_failed_published'
    report['fallback_pages']=sorted({i+1 for i,p in enumerate(plan['pages']) for r in p['retained']
                                     if r['kind'] in ('page_check_fallback','position_fallback')})
    report['retained_by_kind']=dict(Counter(r['kind'] for r in report['retained_regions']))
    report['garbled_text']=garbled_text(report['retained_regions'],report['slots'])
    if report['garbled_text']['warning']:
        # rc stays 0 (a PDF is delivered), but never as a finished translation.
        report.update(translation_completeness='garbled_font',status='garbled_text_published',automated_checks_passed=False)
    latex.write_json(out/'qa.json',report)
    if not report['automated_checks_passed']:
        print('[质检] 仍有检查项未通过，照常输出成品并标记需复核；详情见 qa.json',flush=True)
    stem=re.sub(r'(\.unrotated|\.mapped)+$','',pdf.stem)
    zh=publish_copy(rendered,out/(stem+'-中文-无源码试验.pdf'))
    dual=out/(stem+'-中英-无源码试验.pdf')
    with pymupdf.open(pdf) as src,pymupdf.open(zh) as cn,pymupdf.open() as output:
        for i,page in enumerate(src):
            w,h=page.rect.width,page.rect.height
            p=output.new_page(width=2*w,height=h)
            p.show_pdf_page(pymupdf.Rect(0,0,w,h),src,i)
            p.show_pdf_page(pymupdf.Rect(w,0,2*w,h),cn,i)
        with tempfile.TemporaryDirectory(prefix='.dual-',dir=out) as temp:
            staged=Path(temp)/'dual.pdf';output.save(staged,garbage=4,deflate=True)
            dual=publish_copy(staged,dual)
    report['output_files']={'chinese':str(zh),'bilingual':str(dual)}
    latex.write_json(out/'qa.json',report)
    print('[输出] '+str(dual),flush=True)
    print('[质检] 数字、页数、占位符和文本框检查通过；保留项分类：'+str(report['retained_by_kind']),flush=True)
    print(f'[覆盖率检查] {len(report["untranslated_text_candidates"])} 行疑似未翻译英文（专名可能误报），详见 qa.json；排版通过不等于全文翻译完成。',flush=True)
    bc=report['body_coverage']
    print(f'[正文覆盖率] {bc["coverage"]:.1%}（正文字号 {bc["body_font"]}pt，{len(bc["untranslated_body"])} 行未译）',flush=True)
    g=report['garbled_text']
    if g['warning']:
        print(f'[警告] {g["share"]:.0%} 的文字字体编码异常，无法提取，这些文字没有翻译、保留英文原样；'
              '正文覆盖率不统计这部分，不代表全文已译。需要 OCR 或换一份 PDF。',flush=True)
    else:print('[说明] 自动检查通过，但报告中的保留项仍需人工复核；不会宣称全文无误。',flush=True)
    return report


def translate_plan(out,plan,tracking,cfg,offline):
    """Translate what can be translated. A segment the local checks reject,
    or one without an accepted translation (network, quota, repeated model
    errors), keeps its original text instead of stopping the document.
    --offline still stops on missing cache: it promises no API call."""
    segments=[s['segment'] for page in plan['pages'] for s in page['slots']]
    try:return latex.build_translation([(Path('native'),'',segments)],out/'text-cache.json',tracking,cfg,offline)
    except latex.SourceValidationError as e:
        if not e.ids:raise
        for info in plan['pages']:
            for s in [s for s in info['slots'] if latex.digest(s['segment']['text']) in e.ids]:
                info['slots'].remove(s)
                info['retained'].append({'kind':'source_check_failed','bbox':s['bbox'],'text':s['source'],
                    'reason':'原文保护结构检查未通过，保留原文'})
        print(f'[检查] {len(e.ids)} 个原文片段未通过本地保护检查，保留原文；其余照常翻译',flush=True)
        return translate_plan(out,plan,tracking,cfg,offline)
    except latex.TranslationIncompleteError as e:
        if not (out/'text-cache.json').exists():raise
        cache=json.loads((out/'text-cache.json').read_text(encoding='utf8'))
        missing={latex.digest(s['text']) for s in segments}-set(cache)
        errors=out/'translation-errors.json'
        failed={x.get('id') for x in json.loads(errors.read_text(encoding='utf8'))} if errors.exists() else set()
        # Offline, only text the model already failed on may stay English;
        # never-requested text means the cache is incomplete.
        # A styled paragraph the model keeps mis-marking falls back to its
        # plain segment: losing bold/italic beats keeping English. Offline
        # only when that plain translation is already cached.
        # A bad key or no network fails every request: retrying plain text
        # is pointless, and the run must not report success.
        api_down=any(k in str(e) for k in ('密钥无效','无法连接翻译服务'))
        if api_down:plan['api_failure']=str(e)
        swap=[s for info in plan['pages'] for s in info['slots']
              if not api_down and (plain:=s['segment'].get('plain_segment')) and latex.digest(s['segment']['text']) in missing
              and (not offline or latex.digest(plain['text']) in cache)]
        if offline and missing-failed-{latex.digest(s['segment']['text']) for s in swap}:raise
        for s in swap:s['segment']=s['segment']['plain_segment']
        swapped=len(swap)
        if swapped:
            print(f'[翻译] {swapped} 个带样式段落未通过样式检查，改按纯文本重译（不保留粗斜体）',flush=True)
            return translate_plan(out,plan,tracking,cfg,offline)
        print(f'[翻译] {len(missing)} 个片段未取得合格译文（{str(e)[:80]}），保留原文；重新运行只补这些片段',flush=True)
        usage_path=out/'last-translation-usage.json'
        return cache,json.loads(usage_path.read_text(encoding='utf8')) if usage_path.exists() else {}


def main(argv=None):
    p=argparse.ArgumentParser(description='无作者源码的 PDF 翻译排版工具；只支持带文字层 PDF，数字/图形本地保护。')
    p.add_argument('pdf',type=Path)
    p.add_argument('--outdir',type=Path)
    p.add_argument('--analyze-only',action='store_true',help='仅本地识别结构，绝不调用翻译 API')
    p.add_argument('--offline',action='store_true',help='仅用译文缓存；缺少则停止')
    p.add_argument('--pages',help='只处理指定页，如 1-3 或 1,4,6（先试几页省额度）')
    a=p.parse_args(argv);pdf=a.pdf.resolve()
    if not pdf.is_file(): raise ValueError('PDF 不存在')
    check_complete(pdf)
    out=(a.outdir or HERE/'output'/(pdf.stem+'-nosource')).resolve();out.mkdir(parents=True,exist_ok=True)
    if a.pages:
        # Work on an extracted copy; every later stage then sees only these pages.
        wanted=sorted({n for part in a.pages.split(',') for n in
            (range(int(part.split('-')[0]),int(part.split('-')[1])+1) if '-' in part else [int(part)])})
        subset=out/f'{pdf.stem}.p{a.pages.replace(",","_")}.pdf'
        with pymupdf.open(pdf) as d:
            if not wanted or wanted[0]<1 or wanted[-1]>len(d):raise ValueError(f'页码超出范围 1-{len(d)}')
            d.select([n-1 for n in wanted]);d.save(subset,garbage=3,deflate=True)
        pdf=subset
    print('[启动] 无源码 PDF 工具；'+('仅分析，不翻译' if a.analyze_only else '离线缓存重排，禁止翻译 API' if a.offline else '仅待翻译文本调用 DeepSeek Chat'),flush=True)
    print('[目录] '+str(out),flush=True)
    if not a.analyze_only: check_typesetting_environment()
    pdf=orientation_normalized(pdf,out)
    pdf=glyph_code_mapped(pdf,out)
    plan=prepare(pdf,out)
    if a.analyze_only:
        print('[完成] 仅分析结构，无翻译调用：'+str(out/'structure.json'),flush=True);return
    reuse_previous_report(out,plan)
    raw=json.loads((HERE/'config.json').read_text(encoding='utf8')) if (HERE/'config.json').exists() else {}
    from pdfzh_user import api_key
    # Any OpenAI-compatible endpoint (free/cheap models) via PDFZH_MODEL,
    # PDFZH_BASE_URL and PDFZH_API_KEY; DeepSeek Chat stays the default.
    custom=os.environ.get('PDFZH_BASE_URL')
    cfg={'model':os.environ.get('PDFZH_MODEL','deepseek-chat'),'url':custom or 'https://api.deepseek.com',
         'key':'' if a.offline else os.environ.get('PDFZH_API_KEY') or ('' if custom else
             os.environ.get('DEEPSEEK_API_KEY') or raw.get('api_keys',{}).get('deepseek','') or api_key())}
    tracking=HERE/'.home-next'/'work'/pdf.stem/pdf.stem/'translate_tracking.json'
    cache,usage=translate_plan(out,plan,tracking,cfg,a.offline)
    try:report=compile_overlay(pdf,out,plan,cache)
    except Exception as e:
        # Last resort: still hand the user a document (the original), with the
        # cause recorded; the translation cache is kept for a later rerun.
        stem=re.sub(r'(\.unrotated|\.mapped)+$','',pdf.stem)
        copy=publish_copy(pdf,out/(stem+'-排版失败-原文.pdf'))
        latex.write_json(out/'qa.json',{'status':'layout_failed','automated_checks_passed':False,
            'error':str(e)[:500],'source_sha256':plan['source_sha256'],'output_files':{'chinese':str(copy),'bilingual':str(copy)}})
        print(f'[错误] 排版失败（{str(e)[:120]}）；已输出原文副本 {copy}，译文缓存已保留',flush=True)
        sys.exit(5)
    report['this_run_translation_usage']=usage
    if plan.get('api_failure'):report['translation_failure']=plan['api_failure']
    latex.write_json(out/'qa.json',report)
    if plan.get('api_failure'):
        print(f'[翻译未完成] {plan["api_failure"]} 本次输出中未译部分保留原文：{report["output_files"]["bilingual"]}',file=sys.stderr,flush=True)
        sys.exit(3)


if __name__=='__main__':
    for s in [sys.stdout,sys.stderr]:
        if hasattr(s,'reconfigure'):s.reconfigure(encoding='utf8',errors='replace')
    try:main()
    except latex.SourceValidationError as e:
        print('[本地结构错误] '+str(e),file=sys.stderr,flush=True);sys.exit(4)
    except latex.TranslationIncompleteError as e:
        print('[翻译未完成] '+str(e),file=sys.stderr,flush=True);sys.exit(3)
    except Exception as e:print('[错误] '+str(e),file=sys.stderr,flush=True);sys.exit(2)
