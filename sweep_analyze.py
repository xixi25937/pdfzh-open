# -*- coding: utf-8 -*-
"""Zero-cost generalization sweep: structure analysis only, no translation API.

Usage: python sweep_analyze.py <pdf>...   (writes .test/sweep/<stem>/)
Prints one summary line per PDF: pages, slots, retained kinds, or the error.
"""
import collections, json, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = HERE / '.venv-next' / 'Scripts' / 'python.exe'

for arg in sys.argv[1:]:
    pdf = Path(arg).resolve()
    out = HERE / '.test' / 'sweep' / pdf.stem
    out.mkdir(parents=True, exist_ok=True)
    t = time.time()
    r = subprocess.run([str(PY), '-X', 'utf8', '-W', 'ignore', str(HERE / 'pdfzh_nosource.py'), str(pdf),
                        '--outdir', str(out), '--analyze-only'],
                       capture_output=True, text=True, encoding='utf8', errors='replace', timeout=3600)
    dt = time.time() - t
    s = out / 'structure.json'
    if r.returncode or not s.exists():
        err = (r.stderr.strip().splitlines() or ['?'])[-1]
        print(f'FAIL {pdf.name} ({dt:.0f}s): {err[:200]}', flush=True)
        continue
    plan = json.loads(s.read_text(encoding='utf8'))
    slots = sum(len(p['slots']) for p in plan['pages'])
    kinds = collections.Counter(r['kind'] for p in plan['pages'] for r in p['retained'])
    chars = sum(len(x['source']) for p in plan['pages'] for x in p['slots'])
    print(f'OK   {pdf.name}: {len(plan["pages"])}p slots={slots} chars={chars} ({dt:.0f}s) retained={dict(kinds)}', flush=True)
