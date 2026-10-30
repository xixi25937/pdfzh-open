"""usage: baseline.py save|compare [file] : coverage + rc-like summary from every regress.log"""
import sys, re, json, os, pathlib
T = pathlib.Path(r'C:\Users\Lenovo\Desktop\具身智能\测试新论文')
HERE = pathlib.Path(r'C:\Users\Lenovo\Desktop\具身智能\pdfzh')
dirs = [T / 'out-miner', T / 'out-ropt', T / 'out', HERE / '.test' / 'iter' / '22536'] + sorted((T / 'train').glob('*'))
def read():
    res = {}
    for d in dirs:
        f = d / 'regress.log'
        if not f.exists() or not (d / 'text-cache.json').exists():
            continue
        t = f.read_text(encoding='utf8', errors='replace')
        m = re.search(r'\[正文覆盖率\] ([\d.]+)%', t)
        res[d.name] = float(m[1]) if m else None
    return res
mode = sys.argv[1]
path = pathlib.Path(os.environ['TEMP']) / 'opencode' / (sys.argv[2] if len(sys.argv) > 2 else 'cov_base.json')
if mode == 'save':
    r = read(); path.write_text(json.dumps(r, ensure_ascii=False), encoding='utf8'); print(len(r), 'saved', path)
else:
    old = json.loads(path.read_text(encoding='utf8')); new = read()
    same = up = down = 0
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a is None or b is None:
            print('NA', k, a, b); continue
        if abs(a - b) < 0.05: same += 1
        elif b > a: up += 1; print('UP  ', k, a, '->', b)
        else: down += 1; print('DOWN', k, a, '->', b)
    print('same', same, 'up', up, 'down', down)
