import json,sys,re
# usage: qa.py <outdir>  -> failed QA keys and number mismatches with their slots
out=sys.argv[1]
q=json.load(open(out+r'\qa.json',encoding='utf8'))
print('false:',[k for k,v in q.items() if v is False])
print({k:q.get(k) for k in ('missing_numeric_tokens','extra_numeric_tokens','overflow_slots','critical_warnings')})
N=re.compile(r'(?<!\d)\d+(?:\.\d+)?')
bad={m['page'] for m in q.get('missing_numeric_tokens',[])+q.get('extra_numeric_tokens',[])}
for s in q['slots']:
    if s['page'] in bad and sorted(N.findall(s['source']))!=sorted(N.findall(s['translation'])):
        print(s['page'],[round(v) for v in s['bbox']],repr(s['source'][:200]),'\n =>',repr(s['translation'][:200]))
