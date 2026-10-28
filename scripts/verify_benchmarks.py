"""Explicit read-only smoke check of the three public official total-return feeds."""
import json
from pathlib import Path
from backend.instruments import BENCHMARKS
from backend.market import benchmark_quotes
results=[]
for instrument in BENCHMARKS:
    result=benchmark_quotes(instrument,'2026-09-30','2026-10-25')
    rows=result['rows']
    assert rows and all(row['kind']=='total_return' for row in rows)
    assert not any(row['date'] in ('2026-10-01','2026-10-04','2026-10-10','2026-10-11') for row in rows)
    item={'code':instrument['code'],'name':instrument['name'],'count':len(rows),
          'first':rows[0],'last':rows[-1],'notes':result['notes']}
    results.append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
path=Path('work/revision-v02/benchmark-live.json');path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps(results,ensure_ascii=False,indent=2))
