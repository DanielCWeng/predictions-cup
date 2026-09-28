import json, time
from pathlib import Path
import stage
stage._init(); toks = sorted(stage.tokens())
names = json.load(open("/home/ubuntu/polymarketwhale/campaigns/sisterreq_20260925/oci_trade_object_inventory.json"))["canonical_daily_objects"]
days = sorted([Path(n).stem for n in names if "2023-01-14" <= Path(n).stem < "2025-12-01"], reverse=True)
t0 = time.time()
for i, d in enumerate(days, 1):
    r = stage.day(d, toks)
    print(f"pre {i}/{len(days)} {d} fills={r['fills']} legs={r['legs']} {r.get('custody')} {r.get('secs')}s el={int(time.time()-t0)}s", flush=True)
print("PREERA DONE", flush=True)
