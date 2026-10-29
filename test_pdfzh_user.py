import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import pdfzh_user as user
import pdfzh_cli as cli
import build_release as release
from contextlib import redirect_stdout
from io import StringIO


class UserTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_key_is_encrypted_and_settings_are_user_local(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'LOCALAPPDATA':''}):
            with patch.object(user,'settings_path',return_value=Path(folder)/'settings.json'):
                user.set_key('test-not-a-real-api-key')
                data=(Path(folder)/'settings.json').read_text()
                self.assertNotIn('test-not-a-real-api-key',data)
                with patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}):self.assertEqual(user.api_key(),'test-not-a-real-api-key')
                with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'environment-wins'}):self.assertEqual(user.api_key(),'environment-wins')

    def test_same_name_different_content_has_independent_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);pdf=root/'manual.pdf';pdf.write_bytes(b'first')
            first=user.job_directory(pdf);self.assertEqual(first,user.job_directory(pdf))
            self.assertEqual(first.parent,root/'翻译结果')
            pdf.write_bytes(b'second');self.assertNotEqual(first,user.job_directory(pdf))

    def test_offline_does_not_require_or_forward_key(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf=Path(folder)/'demo.pdf';pdf.write_bytes(b'fake%%EOF')
            with patch.object(user,'api_key',side_effect=AssertionError('no key access')), \
                 patch.object(cli.subprocess,'run',return_value=subprocess.CompletedProcess([],2)) as run:
                self.assertEqual(cli.main([str(pdf),'--offline','--no-open']),2)
                args=run.call_args.args[0]
                self.assertIn('--offline',args)
                self.assertNotIn('api_key',' '.join(args))

    def test_success_records_friendly_output_and_does_not_open_when_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);pdf=root/'demo.pdf';pdf.write_bytes(b'input%%EOF');out=user.job_directory(pdf);out.mkdir(parents=True)
            result=out/'dual.pdf';result.write_bytes(b'result')
            (out/'reflow-qa.json').write_text(json.dumps({'automated_checks_passed':True,'output_files':{'bilingual':str(result)}}))
            with patch.object(user,'api_key',return_value='not-a-real-key'),patch.object(user,'load_settings',return_value={}), \
                 patch.object(user,'save_settings') as save,patch.object(cli,'reveal') as reveal, \
                 patch.object(cli.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as run:
                self.assertEqual(cli.main([str(pdf),'--no-open','--mode','reflow']),0)
                friendly=Path(save.call_args.args[0]['last_result'])
                self.assertEqual(friendly.read_bytes(),b'result');reveal.assert_not_called()
                self.assertNotIn('not-a-real-key',' '.join(run.call_args.args[0]))
                self.assertEqual(run.call_args.kwargs['env']['DEEPSEEK_API_KEY'],'not-a-real-key')

    def test_default_mode_is_original_with_tex_and_reflow_without(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf=Path(folder)/'demo.pdf';pdf.write_bytes(b'fake%%EOF');output=StringIO()
            for tex,engine in ((True,'pdfzh_nosource.py'),(False,'pdfzh_reflow.py')):
                with patch.object(cli.shutil,'which',return_value='tex' if tex else None), \
                     patch.object(cli.subprocess,'run',return_value=subprocess.CompletedProcess([],2)) as run, \
                     redirect_stdout(output):
                    cli.main([str(pdf),'--offline','--no-open'])
                self.assertEqual(Path(run.call_args.args[0][1]).name,engine)
            self.assertIn('未找到 TeX Live',output.getvalue())
            self.assertEqual(output.getvalue().count('未找到 TeX Live'),1)

    def test_doctor_hint_matches_tex_availability(self):
        for tex,expected in ((True,'将使用默认的 original 模式'),(False,'自动改用 reflow 模式')):
            output=StringIO()
            with patch.object(cli.shutil,'which',return_value='tex' if tex else None), \
                 patch.object(user,'api_key',return_value=None),redirect_stdout(output):
                cli.doctor()
            self.assertIn(expected,output.getvalue().splitlines()[-1])
            self.assertNotIn('仅用于',output.getvalue())

    def test_missing_translation_does_not_recommend_offline_as_a_fix(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf=Path(folder)/'demo.pdf';pdf.write_bytes(b'fake%%EOF');output=StringIO()
            with patch.object(cli.subprocess,'run',return_value=subprocess.CompletedProcess([],3)),redirect_stdout(output):
                self.assertEqual(cli.main([str(pdf),'--offline','--no-open']),3)
            self.assertIn('--offline 不能补齐',output.getvalue())
            self.assertIn('去掉 --offline 重新运行',output.getvalue())
            self.assertNotIn('零翻译费用重排',output.getvalue())

    def test_truncated_download_stops_before_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf=Path(folder)/'demo.pdf';pdf.write_bytes(b'%PDF-1.7\n1 0 obj')
            with patch.object(cli.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'下载中断'):cli.main([str(pdf),'--offline','--no-open'])
                run.assert_not_called()

    def test_release_whitelist_and_secret_scan(self):
        for forbidden in ['config.json','sitecustomize.py','pdfzh_next.py','output','reference']:
            self.assertNotIn(forbidden,release.FILES)
        with self.assertRaises(ValueError):release.audit_text('bad','sk-'+'x'*24)
        release.audit_text('ok','DEEPSEEK_API_KEY')


if __name__=='__main__':unittest.main()
