"""Offline descriptive D/R winner NO/NO 20-share structural floor screen."""
import collections, datetime as dt, glob, json, math, os
from pathlib import Path
import pyarrow.parquet as pq

ROOT='/home/ec2-user/predictions-cup'
mapping=json.load(open(ROOT+'/data/mappings/sig_polymarket_2026.json'))
groups=collections.defaultdict(dict)
for p in mapping['records']:
 if p.get('mapping_class')!='EXACT':continue
 title=p['sig_market_title'];market=str(p['sig_market_id']);exchange=str(p['sig_exchange_id'])
 if title.startswith('Will the Democratic Party win the '):group=title[len('Will the Democratic Party win the '):];party='D'
 elif title.startswith('Will the Republican Party win the '):group=title[len('Will the Republican Party win the '):];party='R'
 else:continue
 groups[group][party]=(exchange,market)
pairs={race:g for race,g in groups.items() if 'D' in g and 'R' in g}
index={eid:(race,party,market) for race,g in pairs.items() for party,(eid,market) in g.items()}
print('CANDIDATE_EXACT_SAME_SEAT_TITLE_PAIRS',len(pairs),'exchanges',len(index),flush=True)
for window,s,e in [('new_universe','20261016T1530','20261016T1537'),('later','20261016T1545','20261016T1552')]:
 states={};last_rev={};counts=collections.Counter();seen=set();results=collections.defaultdict(list)
 files=sorted(glob.glob(ROOT+'/data/sig_research/raw_events/2026/10/16/15/*.parquet'))
 for path in files:
  n=os.path.basename(path)[:13]
  if not(s<=n<e):continue
  batch=pq.read_table(path,columns=['local_receive_at','topic','connection_epoch','revision','previous_revision','validation_error','raw_json'])
  for r in batch.to_pylist():
   if r['validation_error'] or not r['local_receive_at']:continue
   when=r['local_receive_at'].timestamp()
   topic=r['topic'];epoch=r['connection_epoch'];revs=(topic,epoch)
   prev=last_rev.get(revs);gap=prev is not None and prev!=r['previous_revision'] and prev!=r['revision']
   last_rev[revs]=r['revision']
   try:o=json.loads(r['raw_json'])
   except(ValueError,TypeError):continue
   for b in o.get('books',[]):
    ex=str(b.get('exchangeId'))
    if ex not in index:continue
    race,party,market=index[ex]
    if not topic.endswith(':market:'+market):counts['wrong_topic']+=1;continue
    if gap:counts['gap']+=1;continue
    bids=b.get('bids');asks=b.get('asks')
    if not bids or not asks:continue
    try:
     vb=[(float(x['price']),float(x['quantity'])) for x in bids]
     va=[(float(x['price']),float(x['quantity'])) for x in asks]
     if vb!=sorted(vb,reverse=True) or va!=sorted(va) or vb[0][0]>=va[0][0]:continue
     if any(not(.005<=px<=.995 and qty>=1 and qty==int(qty)) for px,qty in vb+va):continue
     native=dt.datetime.fromisoformat(b['asOf']['at'].replace('Z','+00:00')).timestamp()
     if when-native>5 or native>when+.5:continue
    except(KeyError,ValueError,TypeError):continue
    states[ex]=(when,native,vb,va)
    other=pairs[race]['R' if party=='D' else 'D'][0]
    counterpart=states.get(other)
    if not counterpart or when-counterpart[0]>2 or when-counterpart[1]>5:continue
    bin_key=(race,int(when//10))
    if bin_key in seen:continue
    seen.add(bin_key)
    statesD=states[pairs[race]['D'][0]];statesR=states[pairs[race]['R'][0]]
    def no_buy_cost(book,qty):
     remaining=qty;cash=0.0
     for price,size in book[2]:
      fill=min(remaining,math.floor(size*.5))
      cash+=fill*(1-price);remaining-=fill
      if remaining<=0:return cash
     return None
    costD=no_buy_cost(statesD,2500);costR=no_buy_cost(statesR,2500)
    if costD is None or costR is None:counts['insufficient_20lot']+=1;continue
    edge=(2500-costD-costR)/2500
    results[race].append(edge)
    counts['paired_20lot']+=1
  del batch
 edges=[v for vals in results.values() for v in vals]
 eligible=[(race,vals) for race,vals in results.items() if max(vals)>=.01]
 print('WINDOW',window,'counts',dict(counts),'qualified_races',len(results),'valid_samples',len(edges),'positive_gt0',sum(x>0 for x in edges),'edge_gt1c',sum(x>=.01 for x in edges),'max_edge_c',round(max(edges)*100,3) if edges else None,'race_gt1c',len(eligible))
 for race,vals in sorted(eligible,key=lambda p:max(p[1]),reverse=True)[:10]:
  print('STRUCTURAL_CANDIDATE',window,race,'n',len(vals),'max_raw_edge_c',round(max(vals)*100,3),'min_raw_edge_c',round(min(vals)*100,3),'count_ge_1c',sum(v>=.01 for v in vals))
 print('LIMITATION: exact settlement rules, source completeness, venue-specific NO execution semantics, fees, fill and leg risk still unverified.',flush=True)
