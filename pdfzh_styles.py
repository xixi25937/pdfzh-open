"""Local PDF style extraction and safe placeholder-based rich text.

Only text goes to the translator. Font/color instructions stay on this machine.
Plain legacy translations are not guessed into new semantic style ranges.
"""
import re
from collections import Counter


def style(span):
    font=span.get('font','').lower();flags=span.get('flags',0)
    return {'color':int(span.get('color',0)) & 0xffffff,
            'bold':bool(flags & 16 or 'bold' in font),
            'italic':bool(flags & 2 or 'italic' in font or 'oblique' in font)}


def source_runs(lines):
    runs=[]
    left=min(l['bbox'][0] for l in lines);right=max(l['bbox'][2] for l in lines)
    sizes=sorted(s.get('size',10) for l in lines for s in l['spans'])
    typical=sizes[len(sizes)//2]
    for index,line in enumerate(lines):
        spans=line['spans']
        first=next((s for s in spans if s['text'].strip()),None)
        # A bold field label at the start of a physical line is a paragraph,
        # while ordinary wrapped PDF lines must remain free to reflow.
        detached=bool(index and first and line['bbox'][0]>left+(right-left)*.45 and
                      first.get('size',typical)>typical*1.15)
        paragraph=bool(detached or (index and first and style(first)['bold'] and
                       re.search(r'[:：]\s*$',first['text'])))
        if runs:
            if paragraph:runs.append({'text':'\n','break':True})
            elif runs[-1]['text'].endswith('-') and first and first['text'][:1].islower():
                runs[-1]['text']=runs[-1]['text'][:-1]
            else:runs[-1]['text']+=' '
        for k,span in enumerate(spans):
            text=span['text'];fmt=style(span)
            after=spans[k+1]['text'] if k+1<len(spans) else ''
            if detached:
                fmt['align']='right'
                fmt['scale']=round(span.get('size',typical)/typical,3)
            if not text:continue
            # A number split across fonts ("50" bold + ".0%" regular) must stay
            # one token, or the translator can insert words inside it.
            joins_number=bool(runs and not runs[-1].get('break') and
                re.search(r'\d[.,]?$',runs[-1]['text']) and (re.match(r'[.,]?\d',text) or
                    (re.fullmatch(r'[.,]',text) and after[:1].isdigit())))
            if runs and not runs[-1].get('break') and (runs[-1]['style']==fmt or joins_number):
                runs[-1]['text']+=text
            else:runs.append({'text':text,'style':fmt})
    for run in runs:
        if not run.get('break'):run['text']=re.sub(r'\s+',' ',run['text'])
    return runs


def rich_segment(lines,plain,make_segment):
    runs=source_runs(lines)
    styles={tuple(sorted(r['style'].items())) for r in runs if not r.get('break') and r['text'].strip()}
    if len(styles)<2 and not any(r.get('break') for r in runs):return plain
    # Fail closed on unusual extraction normalization instead of dropping text.
    joined=re.sub(r'\s+',' ',''.join(r['text'] for r in runs)).strip()
    if joined!=plain['original']:return plain
    protected=[];events={};parts=[]
    def marker(raw,event=None):
        index=len(protected);protected.append(raw)
        if event is not None:events[str(index)]=event
        return f'@@P{index}@@'
    for run in runs:
        if run.get('break'):
            parts.append(marker('\n',{'kind':'break'}));continue
        if not run['text'].strip():
            parts.append(run['text']);continue
        parts.append(marker('{',{'kind':'open','style':run['style']}))
        # A bold "Q"/"A" whose colon starts the next run is a dialogue prefix.
        sub=make_segment(run['text'])
        nxt=runs[runs.index(run)+1]['text'] if runs.index(run)+1<len(runs) else ''
        if run is runs[0] and run['text'].strip() in ('Q','A') and nxt.startswith(':'):
            sub={'text':run['text'],'protected':[]}
        offset=len(protected)
        parts.append(re.sub(r'@@P(\d+)@@',lambda m:f'@@P{offset+int(m[1])}@@',sub['text']))
        protected.extend(sub['protected'])
        parts.append(marker('}',{'kind':'close'}))
    return {**plain,'text':''.join(parts),'protected':protected,'style_events':events,
            'plain_segment':plain}


def decoded_runs(segment,value):
    events=segment.get('style_events',{})
    # A PDF can put the decimal point in its own font span. Recover only a
    # source-proven decimal between the same immutable numeric placeholders,
    # including the intervening style markers; never repair arbitrary prose.
    plain=re.sub(r'@@P(\d+)@@',lambda m:'' if m[1] in events else m[0],segment['text'])
    for match in re.finditer(r'@@P(\d+)@@\.@@P(\d+)@@',plain):
        left,right=match.groups()
        if not (segment['protected'][int(left)][-1:].isdigit() and segment['protected'][int(right)][:1].isdigit()):continue
        a=f'@@P{left}@@';b=f'@@P{right}@@'
        start=segment['text'].index(a);end=segment['text'].index(b)+len(b)
        bridge=segment['text'][start:end]
        pattern=re.escape(bridge).replace(r'\.',r'\s*[.。．]\s*')
        value=re.sub(pattern,lambda m:bridge,value)
    default={'color':0,'bold':False,'italic':False};current=default;result=[]
    expected=[m for m in re.findall(r'@@P(\d+)@@',segment['text']) if m in events]
    actual=[m for m in re.findall(r'@@P(\d+)@@',value) if m in events]
    if expected!=actual:raise ValueError('样式或分段标记顺序改变')
    # Numeric/formula placeholders must not move into a differently styled
    # phrase, even when the global token count remains correct.
    def owners(text):
        owner=None;result={}
        for key in re.findall(r'@@P(\d+)@@',text):
            event=events.get(key)
            if event and event['kind']=='open':owner=event['style']
            elif event and event['kind']=='close':owner=None
            elif not event:result[key]=owner
        return result
    if owners(segment['text'])!=owners(value):raise ValueError('保护内容移出了对应样式片段')
    # PDF producers may split one identically styled sentence into hundreds of
    # word/space spans. Chinese word order cannot align to each of those spans.
    # Require content per actual style group, not per extraction artifact.
    ranges=phrase_ranges(segment)
    for group in translation_groups(segment).values():
        values=[]
        for key in group['members']:
            start,end=ranges[key]
            opening=segment['text'][:start].rfind('@@P')
            close=re.match(r'@@P\d+@@',segment['text'][end:]).group()
            opener=segment['text'][opening:start]
            a=value.index(opener)+len(opener);b=value.index(close,a)
            values.append(value[a:b])
        if group['text'].strip() and not ''.join(values).strip():
            raise ValueError('样式片段的译文为空')
    def emit(text):
        if not text:return
        if result and result[-1]['style']==current:result[-1]['text']+=text
        else:result.append({'text':text,'style':dict(current)})
    for part in re.split(r'(@@P\d+@@)',value):
        match=re.fullmatch(r'@@P(\d+)@@',part)
        if not match:emit(part);continue
        key=match[1];event=events.get(key)
        if not event:emit(segment['protected'][int(key)])
        elif event['kind']=='open':current=event['style']
        elif event['kind']=='close':current=default
        else:emit('\n')
    return result


def to_tex(runs,escape):
    parts=[]
    for run in runs:
        fmt=run['style'];value=escape(run['text']).replace('\n',r'\par ')
        options=r'\color[HTML]{'+format(fmt['color'],'06X')+'}'
        if 'scale' in fmt:options+=r'\fontsize{'+str(fmt['scale'])+r'\slotfont}{\slotbaseline}\selectfont '
        options+=r'\bfseries ' if fmt['bold'] else r'\mdseries '
        options+=r'\itshape ' if fmt['italic'] else r'\upshape '
        if fmt.get('align')=='right':options+=r'\hfill{}'
        # A leading space would only terminate the control word above
        # ("\upshape  2" prints "2"), gluing "2022." to the next number.
        lead=' ' if value[:1]==' ' else ''
        parts.append(lead+'{'+options+value.lstrip(' ')+'}')
    return ''.join(parts)


def phrase_ranges(segment):
    """Expose text ranges, never delegate style marker construction to the AI."""
    result={};opening=None
    for match in re.finditer(r'@@P(\d+)@@',segment['text']):
        event=segment['style_events'].get(match[1])
        if event and event['kind']=='open':opening=(match[1],match.end())
        elif event and event['kind']=='close':
            key,start=opening;result[key]=(start,match.start())
    return result


def translation_groups(segment):
    """Group adjacent equal styles; keep equations and explicit breaks intact.

    The segment/cache identity stays unchanged. Only the wire representation
    is compacted, so paid translations survive changes to this protocol.
    """
    groups={};previous_end=None;previous_key=None
    for key,(start,end) in phrase_ranges(segment).items():
        opener=segment['text'].rfind('@@P',0,start)
        closer=re.match(r'@@P\d+@@',segment['text'][end:])
        fmt=segment['style_events'][key]['style']
        gap=segment['text'][previous_end:opener] if previous_end is not None else None
        piece=segment['text'][start:end]
        if previous_key is not None and gap is not None and not gap.strip() and (
                groups[previous_key]['style']==fmt or not piece.strip()):
            group=groups[previous_key]
            group['text']+=gap+segment['text'][start:end]
            group['members'].append(key)
        else:
            groups[key]={'text':segment['text'][start:end],'members':[key],'style':fmt}
            previous_key=key
        previous_end=end+closer.end()
    return groups


def local_phrase(text):
    """Whitespace, punctuation and immutable tokens have no translation work."""
    visible=re.sub(r'@@P\d+@@','',text)
    return not re.search(r'[^\W\d_]',visible,re.UNICODE)


def request_phrases(segment):
    return {k:g['text'] for k,g in translation_groups(segment).items() if not local_phrase(g['text'])}


def assemble_phrases(segment,translations):
    # Some JSON-mode responses echo the input's harmless ordering metadata.
    # Accept it only when it exactly matches; never ignore unknown phrase ids.
    order=None
    if isinstance(translations,dict) and 'context_order' in translations:
        order=translations['context_order']
        translations={k:v for k,v in translations.items() if k!='context_order'}
    if isinstance(translations,dict) and isinstance(translations.get('phrases'),dict):
        if set(translations)!={'phrases'}:raise ValueError('样式回复包含未知字段')
        translations=translations['phrases']
    ranges=phrase_ranges(segment)
    groups=translation_groups(segment);requested=request_phrases(segment)
    if not isinstance(translations,dict):
        raise ValueError('样式片段编号不完整')
    # Accept the compact protocol, or a complete legacy response. Never guess
    # the correspondence of renumbered / missing non-whitespace phrases.
    compact=set(translations)==set(requested)
    if compact:
        if order is not None and order!=list(requested):
            raise ValueError('样式片段顺序元数据不正确')
        for value in translations.values():
            if not isinstance(value,str) or not value.strip():
                raise ValueError('样式片段译文为空')
        # A locally preserved citation can divide two phrases of the same
        # style. Chinese may place the clause (and its acronym) after that
        # citation. Require exact token counts across requested phrases; the
        # final decoded_runs ownership check still forbids crossing styles or
        # moving a standalone equation into styled prose.
        expected=Counter(re.findall(r'@@P\d+@@',''.join(requested.values())))
        actual=Counter(re.findall(r'@@P\d+@@',''.join(translations.values())))
        if expected!=actual:
            raise ValueError('样式片段内的保护内容缺少或重复；缺少：'+
                             ', '.join((expected-actual).elements())+'；多出：'+
                             ', '.join((actual-expected).elements()))
        expanded={}
        for key,group in groups.items():
            value=group['text'] if key not in requested else translations[key]
            if not isinstance(value,str) or (group['text'].strip() and not value.strip()):
                raise ValueError('样式片段译文为空')
            expanded.update({member:'' for member in group['members']})
            expanded[group['members'][0]]=value
        translations=expanded
    else:
        if order is not None and order!=list(ranges):
            raise ValueError('样式片段顺序元数据不正确')
        required={k for k,(a,b) in ranges.items() if segment['text'][a:b].strip()}
        if not required.issubset(translations) or not set(translations).issubset(ranges):
            raise ValueError('样式片段编号不完整')
        translations=dict(translations)
        for key,(a,b) in ranges.items():
            raw=segment['text'][a:b]
            candidate=translations.get(key)
            if not raw.strip() and (candidate is None or isinstance(candidate,str) and not candidate.strip()):
                translations[key]=raw
            elif not isinstance(translations.get(key),str) or not translations[key].strip():
                raise ValueError('样式片段译文为空')
    value=segment['text']
    for key,(start,end) in reversed(list(ranges.items())):
        translated=translations[key]
        # Style markers are local-only. The final segment validator checks
        # numeric token counts and style ownership; identical-style prose may
        # reorder naturally around an inline mathematical expression.
        if any(key in segment['style_events'] for key in re.findall(r'@@P(\d+)@@',translated)):
            raise ValueError('译文不得注入样式标记')
        value=value[:start]+translated+value[end:]
    return value
