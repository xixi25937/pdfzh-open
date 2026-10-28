"""Unit test for tools/audit.py: builds a synthetic side-by-side PDF in memory."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit

import pymupdf

W, H = 300, 500


def make_pdfs(src_path, out_path):
    src = pymupdf.open()
    out = pymupdf.open()
    for i in range(2):
        sp = src.new_page(width=W, height=H)
        op = out.new_page(width=2 * W, height=H)
        # left half of out = source page content (same layout as src)
        if i == 0:
            lines = [('This is a normal English sentence about robotics.', 60),
                     ('Another line of prose for the body text here.', 90)]
            cn = [('这是一句正常的中文翻译，内容关于机器人。', 60),
                  ('另一行中文正文，排版正常没有问题。', 90)]
        else:
            lines = [('The quick brown fox jumps over the lazy dog near the river bank.', 60),
                     ('Section 7. Conclusion and future work for the reader.', 90),
                     ('The experiment details are discussed in the following section.', 120),
                     ('This line sets the reference size for body text.', 150)]
            cn = [('The quick brown fox jumps over the lazy dog.', 60),      # untranslated
                  ('1The 香港中文大学是作者的单位。', 90),                        # glued english
                  ('实验结果见第X节的讨论部分。', 120),                            # stray letter X
                  ('这行字号太小看不清内容了。', 150)]                             # tiny (5pt)
        for text, y in lines:
            sp.insert_text((20, y), text, fontsize=10)
            op.insert_text((20, y), text, fontsize=10)
        for text, y in cn:
            size = 5 if '太小' in text else 10
            op.insert_text((W + 20, y), text, fontsize=size, fontname='china-s')
        if i == 1:
            op.insert_text((W + 20, 200), '重叠文字甲', fontsize=10, fontname='china-s')
            op.insert_text((W + 22, 201), '重叠文字乙', fontsize=10, fontname='china-s')
            # whole-line overlap: two distinct lines sharing >30% height on
            # the right half only (merged pseudo-code rows reflowed by TeX).
            op.insert_text((W + 20, 250), '第一行算法步骤内容。', fontsize=10, fontname='china-s')
            op.insert_text((W + 20, 252), '第二行算法步骤内容。', fontsize=10, fontname='china-s')
    src.save(src_path)
    out.save(out_path)


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.mkdtemp(prefix='audit_test_')
        cls.src = Path(cls.tmp) / 'src.pdf'
        cls.out = Path(cls.tmp) / 'out.pdf'
        make_pdfs(cls.src, cls.out)
        cls.findings = audit.audit(str(cls.src), str(cls.out))

    def kinds_on_page(self, page):
        return sorted(f['kind'] for f in self.findings if f['page'] == page)

    def test_clean_page_has_no_findings(self):
        self.assertEqual([], [f for f in self.findings if f['page'] == 1])

    def test_untranslated_caught(self):
        hits = [f for f in self.findings if f['page'] == 2 and f['kind'] == 'untranslated']
        self.assertTrue(hits, 'untranslated english prose not caught')
        self.assertTrue(any('quick brown fox' in f['text'] for f in hits))

    def test_leftover_glued_word(self):
        hits = [f for f in self.findings if f['page'] == 2 and f['kind'] == 'leftover']
        self.assertTrue(any('The' in f['text'] for f in hits), f'glued english word not in: {hits}')

    def test_leftover_stray_letter(self):
        hits = [f for f in self.findings if f['page'] == 2 and f['kind'] == 'leftover']
        self.assertTrue(any('第X节' in f['text'] for f in hits), f'stray letter not in: {hits}')

    def test_tiny_caught(self):
        hits = [f for f in self.findings if f['page'] == 2 and f['kind'] == 'tiny']
        self.assertTrue(hits, 'tiny chinese line not caught')
        self.assertTrue(any('太小' in f['text'] for f in hits))

    def test_overlap_caught(self):
        hits = [f for f in self.findings if f['page'] == 2 and f['kind'] == 'overlap']
        self.assertTrue(hits, 'overlapping words not caught')

    def test_line_stacked_on_line_caught(self):
        hits = [f for f in self.findings if f['page'] == 2 and f['kind'] == 'overlap'
                and 'stacked' in (f['detail'] or '')]
        self.assertTrue(hits, 'whole-line overlap not caught')


if __name__ == '__main__':
    unittest.main()
