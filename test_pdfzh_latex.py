import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
import pdfzh_latex as app
import pdfzh_styles as styles
import pdfzh_nosource as native


def fragmented(parts):
    """Synthetic PDF word/space spans, including whitespace in another font."""
    source='';text='';protected=[];events={}
    for word,color in parts:
        i=len(protected);protected.append('{');events[str(i)]={'kind':'open','style':{'color':color,'bold':False,'italic':False}}
        text+=f'@@P{i}@@'+word
        j=len(protected);protected.append('}');events[str(j)]={'kind':'close'}
        text+=f'@@P{j}@@';source+=word
    return {'text':text,'original':source,'protected':protected,'style_events':events,'leading':'','trailing':''}


class LatexTests(unittest.TestCase):
    def test_pdf_literal_braces_are_not_tex_formatting(self):
        for source in ['Parameters {width=16, depth=2} remain.', 'Set {', 'Closing } continues.']:
            s=native.segment(source)
            self.assertEqual(native.translated_text(s,s['text']),source)
            # Old cached plans predate the explicit token type.
            del s['protection_format']
            self.assertEqual(native.translated_text(s,s['text']),source)
        s={'text':'Value @@P0@@width@@P1@@ remains.', 'protected':['{','} '],
           'original':'Value {width}  remains.', 'leading':'','trailing':''}
        self.assertEqual(native.translated_text(s,s['text']),s['original'])

    def test_same_style_clause_can_move_acronym_around_colored_citation(self):
        s=fragmented([('Trained via @@P6@@ actions from Equation (',0),('@@P7@@',0xff0000),('), using data.',0)])
        s['protected'].extend(['FAST','1'])
        value=app.normalize_translation(s,{'0':'通过公式（','4':'）中的 @@P6@@ 动作框架，使用数据训练。'})
        runs=styles.decoded_runs(s,value)
        self.assertEqual(''.join(r['text'] for r in runs),'通过公式（1）中的 FAST 动作框架，使用数据训练。')
        self.assertEqual([r['text'] for r in runs if r['style']['color']==0xff0000],['1'])
        for bad in [{'0':'通过公式（','4':'）使用数据训练。'},
                    {'0':'通过公式（@@P6@@','4':'）@@P6@@ 使用数据训练。'},
                    {'0':'通过公式（@@P7@@','4':'）@@P6@@ 使用数据训练。'}]:
            with self.assertRaises(ValueError):app.normalize_translation(s,bad)

    def test_source_preflight_stops_all_requests_for_local_structure_error(self):
        good=app.extract_segments('A valid paragraph.')[0]
        broken={'text':'Invalid @@P0@@group','protected':[r'\textbf{'],
                'leading':'','trailing':'','protection_format':'tex'}
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'api_batch') as api:
            root=Path(folder)
            with self.assertRaises(app.SourceValidationError):
                app.build_translation([(Path('x'),'', [good,broken])],root/'cache.json',None,{'key':'fake'},False)
            api.assert_not_called()
            errors=json.loads((root/'source-validation-errors.json').read_text())
            self.assertEqual([e['id'] for e in errors],[app.digest(broken['text'])])

    def test_whitespace_and_same_style_words_are_one_request_phrase(self):
        s=fragmented([('Read',0),(' ',0xffffff),('the',0),(' ',0),('instructions.',0)])
        self.assertEqual(styles.request_phrases(s),{'0':'Read the instructions.'})
        value=app.normalize_translation(s,{'0':'请阅读说明。'})
        self.assertEqual(native.translated_text(s,value).strip(),'请阅读说明。')
        self.assertEqual([r['style']['color'] for r in styles.decoded_runs(s,value) if r['text'].strip()],[0])

    def test_legacy_whitespace_response_recovers_without_weakening_missing_prose(self):
        s=fragmented([('Safety',0),(' ',0),('instructions',0)])
        for whitespace in ('',' ',None):
            value=app.normalize_translation(s,{'0':'安全','2':whitespace,'4':'说明'})
            self.assertIn('安全',native.translated_text(s,value))
        self.assertIn('安全',native.translated_text(s,app.normalize_translation(s,{'0':'安全','4':'说明'})))
        with self.assertRaises(ValueError):app.normalize_translation(s,{'0':'安全','2':' '})
        with self.assertRaises(ValueError):app.normalize_translation(s,{'0':'安全','2':' ','4':''})

    def test_true_style_boundaries_and_wrong_metadata_are_rejected(self):
        s=fragmented([('Warning',0xcc0000),(' ',0),('Read instructions',0)])
        self.assertEqual(list(styles.request_phrases(s)),['0','4'])
        value=app.normalize_translation(s,{'0':'警告','4':'阅读说明'})
        runs=styles.decoded_runs(s,value)
        self.assertEqual(runs[0]['style']['color'],0xcc0000)
        self.assertEqual(runs[-1]['style']['color'],0)
        for reply in [{'0':'警告'},{'0':'警告','4':'阅读说明','99':'多余'},
                      {'phrases':{'0':'警告','4':'阅读说明'},'context_order':['4','0']}]:
            with self.assertRaises(ValueError):app.normalize_translation(s,reply)

    def test_immutable_only_groups_are_restored_locally_without_api(self):
        s=fragmented([('(',0),(')',0)])
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'api_batch') as api:
            cache,_=app.build_translation([(Path('x'),'', [s])],Path(folder)/'cache.json',None,{},True)
            self.assertEqual(native.translated_text(s,cache[app.digest(s['text'])]),'()')
            api.assert_not_called()

    def test_compact_phrases_preserve_math_and_real_style_ownership(self):
        s=fragmented([('Use @@P6@@',0),(' safely.',0),('Avoid @@P7@@.',0xcc0000)])
        s['protected'].extend(['x','y'])
        good={'0':'安全使用 @@P6@@。','4':'避免 @@P7@@。'}
        self.assertEqual(styles.request_phrases(s),{'0':'Use @@P6@@ safely.','4':'Avoid @@P7@@.'})
        value=app.normalize_translation(s,good)
        self.assertEqual([(r['text'],r['style']['color']) for r in styles.decoded_runs(s,value)],
                         [('安全使用 x。',0),('避免 y。',0xcc0000)])
        for bad in [{'0':'安全使用。','4':'避免 @@P6@@ @@P7@@。'},
                    {'0':'安全使用 @@P6@@ @@P6@@。','4':'避免 @@P7@@。'},
                    {'0':'安全使用 @@P0@@ @@P6@@。','4':'避免 @@P7@@。'}]:
            with self.assertRaises(ValueError):app.normalize_translation(s,bad)

    def test_standalone_formula_and_paragraph_break_are_group_boundaries(self):
        for is_break in (False,True):
            s=fragmented([('Before',0),('After',0)])
            s['protected'].append('\n' if is_break else 'x')
            s['text']=s['text'].replace('@@P1@@','@@P1@@@@P4@@')
            if is_break:s['style_events']['4']={'kind':'break'}
            self.assertEqual(styles.request_phrases(s),{'0':'Before','2':'After'})
            value=app.normalize_translation(s,{'0':'之前','2':'之后'})
            self.assertIn('@@P4@@',value)

    def test_saved_whitespace_failure_is_recovered_offline(self):
        s=fragmented([('Safety',0),(' ',0),('instructions',0)]);key=app.digest(s['text'])
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'api_batch') as api:
            root=Path(folder)
            app.write_json(root/'translation-errors.json',[{'id':key,'response':{'0':'安全','2':' ','4':'说明'}}])
            cache,_=app.build_translation([(Path('x'),'', [s])],root/'cache.json',None,{},True)
            self.assertIn('安全',native.translated_text(s,cache[key]));api.assert_not_called()

    def test_actual_api_payload_does_not_request_whitespace_or_per_word_styles(self):
        from io import BytesIO
        s=fragmented([('Read',0),(' ',0),('instructions',0)]);key=app.digest(s['text'])
        response={'choices':[{'message':{'content':json.dumps({key:{'0':'阅读说明'}})},'finish_reason':'stop'}],'usage':{}}
        with patch.object(app.urllib.request,'urlopen',return_value=BytesIO(json.dumps(response).encode())) as api:
            values,_=app.api_batch([(key,s)],{'model':'test','url':'https://example.invalid','key':'fake'})
            payload=json.loads(api.call_args.args[0].data)
            message=json.loads(payload['messages'][1]['content'])
            self.assertEqual(message[key],{'phrases':{'0':'Read instructions'},'context_order':['0']})
            self.assertIn('阅读说明',app.normalize_translation(s,values[key]))

    def test_retry_receives_missing_token_feedback(self):
        s=app.extract_segments('Value $x$ remains.')[0];key=app.digest(s['text']);calls=[]
        def api(batch,cfg):
            calls.append(cfg)
            return {key:'值' if len(calls)==1 else '值 @@P0@@ 保持不变。'},{'total_tokens':2}
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'api_batch',side_effect=api):
            cache,usage=app.build_translation([(Path('x'),'', [s])],Path(directory)/'cache.json',None,{'key':'fake','model':'test'},False)
            self.assertIn('@@P0@@',calls[1]['retry_feedback'][key])
            self.assertEqual(len(cache),1)
            self.assertEqual(usage['total_tokens'],4)

    def test_saved_raw_json_recovered_offline_without_api(self):
        s=app.extract_segments('Hello.')[0];key=app.digest(s['text'])
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'api_batch') as api:
            root=Path(directory)
            app.write_json(root/'translation-errors.json',[{'id':key,'raw_response':json.dumps({key:'你好'})}])
            cache,_=app.build_translation([(Path('x'),'', [s])],root/'cache.json',None,{},True)
            self.assertEqual(cache[key],'你好');api.assert_not_called()

    def test_response_decoder_accepts_fence_but_not_truncated_json(self):
        self.assertEqual(app.decode_response('```json\n{"id":"译文"}\n```'),{'id':'译文'})
        for raw in ['{"id":','[]','prefix {"id":"text"}']:
            with self.assertRaises(ValueError):app.decode_response(raw)

    def test_batch_parse_failure_splits_and_counts_all_paid_responses(self):
        segments=app.extract_segments('First paragraph.\n\nSecond paragraph.')
        self.assertEqual(len(segments),2)
        def api(batch,config):
            if len(batch)>1:raise app.TranslationResponseError('invalid JSON','{"partial":',{'total_tokens':10})
            return {batch[0][0]:'译文'}, {'total_tokens':2}
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'api_batch',side_effect=api) as mocked:
            cache,usage=app.build_translation([(Path('x'),'',segments)],Path(directory)/'cache.json',None,{'key':'fake','model':'test'},False)
            self.assertEqual(len(cache),2)
            self.assertEqual(usage['total_tokens'],14)
            self.assertEqual(mocked.call_count,3)

    def test_batch_exception_records_every_missing_segment(self):
        segments=app.extract_segments('First paragraph.\n\nSecond paragraph.')
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'api_batch',side_effect=TimeoutError),patch.object(app.time,'sleep'):
            root=Path(directory)
            with self.assertRaisesRegex(RuntimeError,'无法连接翻译服务'):
                app.build_translation([(Path('x'),'',segments)],root/'cache.json',None,{'key':'fake','model':'test'},False)
            errors=json.loads((root/'translation-errors.json').read_text(encoding='utf8'))
            self.assertEqual({e['id'] for e in errors},{app.digest(s['text']) for s in segments})

    def test_http_401_fails_fast_with_actionable_message(self):
        import urllib.error
        segments=app.extract_segments('First paragraph.\n\nSecond paragraph.')
        err=urllib.error.HTTPError('https://example.invalid',401,'Unauthorized',{},None)
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'api_batch',side_effect=err) as api,patch.object(app.time,'sleep'):
            root=Path(directory)
            with self.assertRaisesRegex(app.TranslationIncompleteError,'密钥无效.*configure'):
                app.build_translation([(Path('x'),'',segments)],root/'cache.json',None,{'key':'fake','model':'test'},False)
            self.assertEqual(api.call_count,1)
            errors=json.loads((root/'translation-errors.json').read_text(encoding='utf8'))
            self.assertTrue(all('401' in e['reason'] for e in errors))

    def test_connection_failure_message_mentions_network(self):
        import urllib.error
        segments=app.extract_segments('First paragraph.\n\nSecond paragraph.')
        err=urllib.error.URLError('connection refused')
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'api_batch',side_effect=err) as api,patch.object(app.time,'sleep'):
            root=Path(directory)
            with self.assertRaisesRegex(app.TranslationIncompleteError,'无法连接翻译服务'):
                app.build_translation([(Path('x'),'',segments)],root/'cache.json',None,{'key':'fake','model':'test'},False)
            self.assertEqual(api.call_count,3)

    def test_formula_citation_and_styles_roundtrip(self):
        text = r'''\documentclass{article}
\begin{document}
\title{Test title}
\author{Original Name}
Let $a\sqrt{t/(1-t)}$ denote noise~\cite{key}. \hl{Gray} is highlighted.

\begin{equation}E=mc^2\end{equation}
\bibliographystyle{ACM-Reference-Format}
\end{document}'''
        segments=app.extract_segments(text)
        self.assertEqual(len(segments),2)
        rebuilt=text
        for s in reversed(segments):
            rebuilt=rebuilt[:s['start']]+app.restore(s,s['text'])+rebuilt[s['end']:]
        self.assertEqual(rebuilt,text)
        message=' '.join(s['text'] for s in segments)
        self.assertNotIn('sqrt',message)
        self.assertNotIn('Original Name',message)
        self.assertNotIn('ACM-Reference',message)
        self.assertNotIn('E=mc',message)

    def test_rejects_changed_tokens_and_tex_injection(self):
        s=app.extract_segments('Noise $x$ and $y$ is stable.')[0]
        for value in ['噪声 @@P0@@', '噪声 @@P0@@ @@P0@@', r'@@P0@@ @@P1@@ \input{evil}']:
            with self.assertRaises(ValueError): app.restore(s,value)
        self.assertIn('$y$',app.restore(s,'@@P1@@ 和 @@P0@@'))

    def test_table_structure_is_not_translation_input(self):
        text=r'''\begin{table}\caption{A caption.}
\begin{tabular}{lc}\toprule
\textbf{Model} & Score \\
\cmidrule(lr){1-2}
Foo & 1.25 \\
\bottomrule\end{tabular}\end{table}'''
        segments=app.extract_segments(text)
        text_inputs=' '.join(s['text'] for s in segments)
        self.assertNotIn('(lr)',text_inputs)
        self.assertNotIn('1.25',text_inputs)
        self.assertNotIn('lc',text_inputs)
        rebuilt=text
        for s in reversed(segments): rebuilt=rebuilt[:s['start']]+app.restore(s,s['text'])+rebuilt[s['end']:]
        self.assertEqual(text,rebuilt)

    def test_offline_never_calls_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app,'api_batch') as api:
                with self.assertRaises(app.TranslationIncompleteError):
                    app.build_translation([(Path('x.tex'),'Hello.',app.extract_segments('Hello.'))],
                        Path(directory)/'cache.json',None,{'key':'not-needed'},True)
                api.assert_not_called()

    def test_formatting_pairs_cannot_cross(self):
        s=app.extract_segments(r'\textbf{First} and \emph{second}.')[0]
        with self.assertRaises(ValueError):
            app.restore(s,'@@P0@@甲@@P2@@乙@@P1@@@@P3@@')


if __name__=='__main__': unittest.main()
