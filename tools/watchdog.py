"""usage: watchdog.py PROGRESS_FILE [--stale MIN] [--max HOURS]
Blocks until PROGRESS_FILE has not been written for MIN minutes (default 20), or MAX hours
(default 6) have passed, then prints why and exits. Run it in the background next to a
long sub-agent job: its exit is the wake-up signal to check on (and resume) that job."""
import argparse
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('path')
p.add_argument('--also', action='append', default=[], help='extra directory; any file written there counts as activity')
p.add_argument('--stale', type=float, default=20)
p.add_argument('--max', type=float, default=6)
a = p.parse_args()
f, start = Path(a.path), time.time()


def last_write():
    times = [f.stat().st_mtime] if f.exists() else [start]
    for d in a.also:
        times += [x.stat().st_mtime for x in Path(d).rglob('*') if x.is_file()]
    return max(times)


while True:
    now = time.time()
    age = (now - last_write()) / 60
    if age >= a.stale:
        print(f'STALE {f.name}: no update for {age:.0f} min')
        sys.exit(0)
    if now - start >= a.max * 3600:
        print(f'TIMEOUT {a.max} h; {f.name} last update {age:.0f} min ago')
        sys.exit(0)
    time.sleep(60)
