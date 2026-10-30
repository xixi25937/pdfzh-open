"""usage: worklog.py ROUND "what happened -> result" [--agent NAME]
Adds one line "- HH:MM text" at the top of the "### 第 ROUND 轮" section of 工作日志.md
(newest first; the section is created above the newest one when missing) and appends
the same line to <temp>/opencode/<ROUND>_progress.txt, which tools/watchdog.py watches.
Example: python tools/worklog.py k27 "README rewritten -> 98 lines"
"""
import argparse
import tempfile
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('round')
p.add_argument('text')
p.add_argument('--agent', default='')
a = p.parse_args()

log = Path(__file__).resolve().parent.parent / '工作日志.md'
raw = log.read_bytes().decode('utf8')
nl = '\r\n' if '\r\n' in raw else '\n'
lines = raw.split(nl)
entry = f'- {time.strftime("%H:%M")} ' + (f'[{a.agent}] ' if a.agent else '') + a.text
mark = f'第 {a.round} 轮'
heads = [i for i, l in enumerate(lines) if l.startswith('### ')]
at = next((i for i in heads if mark in lines[i]), None)
if at is None:
    at = heads[0] if heads else len(lines)
    lines[at:at] = [f'### {mark}', '']
lines.insert(at + 2 if lines[at + 1:at + 2] == [''] else at + 1, entry)
log.write_bytes(nl.join(lines).encode('utf8'))

progress = Path(tempfile.gettempdir()) / 'opencode' / f'{a.round}_progress.txt'
progress.parent.mkdir(parents=True, exist_ok=True)
with progress.open('a', encoding='utf8') as f:
    f.write(entry + '\n')
print(entry)
