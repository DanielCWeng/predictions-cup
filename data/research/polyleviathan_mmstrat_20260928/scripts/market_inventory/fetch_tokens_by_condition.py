#!/usr/bin/env python3
"""Resolve missing market token IDs through the indexed-to-market custody map."""
from __future__ import annotations
import json,os
from pathlib import Path
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
ROOT=Path('/home/ubuntu/polymarketwhale')
CAMPAIGN=ROOT/'campaigns/sisterreq_20260925'
IN=CAMPAIGN/'pg_inventory_markets.jsonl'
OUT=CAMPAIGN/'pg_tokens_by_condition.jsonl'
load_dotenv(ROOT/'Sonar/.env',override=False)
cids=sorted({str(json.loads(line).get('condition_id') or '') for line in IN.open()} - {''})
conn=psycopg2.connect(host=os.getenv('PG_HOST','localhost'),port=os.getenv('PG_PORT','5432'),dbname=os.getenv('PG_DATABASE','polymarket'),user=os.getenv('PG_USER','polyuser'),password=os.getenv('PG_PASSWORD'),connect_timeout=8)
conn.set_session(readonly=True,autocommit=False)
try:
 with conn.cursor() as c:
  c.execute("SET LOCAL statement_timeout='120s'")
  c.execute('SET LOCAL max_parallel_workers_per_gather=1')
 sql="""SELECT t.token_id,t.condition_id,t.token_side
 FROM public.custody_ref_token t
 JOIN unnest(%s::text[]) AS wanted(condition_id) ON wanted.condition_id=t.condition_id"""
 total=0;by_cid={}
 with conn.cursor(name='custody_tokens_by_selected_cid',cursor_factory=RealDictCursor) as cur, OUT.open('w') as out:
  cur.itersize=1000;cur.execute(sql,(cids,))
  for r in cur:
   item={k:(str(v) if v is not None else None) for k,v in r.items()}
   out.write(json.dumps(item,separators=(',',':'))+'\n')
   by_cid.setdefault(item['condition_id'],0);by_cid[item['condition_id']]+=1;total+=1
 print(json.dumps({'selected_condition_ids':len(cids),'mapped_tokens':total,'conditions_with_mapping':len(by_cid),'conditions_without_mapping':len(cids)-len(by_cid),'multiple_token_conditions':sum(v>2 for v in by_cid.values()),'output':OUT.name,'readonly':True}))
 conn.rollback()
finally:conn.close()
