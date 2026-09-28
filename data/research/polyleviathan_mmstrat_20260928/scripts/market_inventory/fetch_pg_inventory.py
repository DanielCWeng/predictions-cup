#!/usr/bin/env python3
"""Fetch whole selected event groups and token identity checks from PostgreSQL."""
from __future__ import annotations
import json, os
from decimal import Decimal
from pathlib import Path
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

ROOT=Path('/home/ubuntu/polymarketwhale')
CAMPAIGN=ROOT/'campaigns/sisterreq_20260925'
GAMMA=json.loads((CAMPAIGN/'selected_gamma_events.json').read_text())
PG_SELECTION=json.loads((CAMPAIGN/'pg_supplemental_event_selections.json').read_text())
RAW_GAMMA=json.loads((CAMPAIGN/'gamma_events.json').read_text())
PG_OUT=CAMPAIGN/'pg_inventory_markets.jsonl'
TOKEN_OUT=CAMPAIGN/'pg_token_identity.jsonl'
CATEGORIES=['Politics','Elections','Global Elections','World Elections','U.S. Politics','United States','USA Election','US Election','u.s. 2024 elections','Canada','Poland','Trump Presidency','Trump','Primaries','nomination','Inauguration','General','Geopolitics','UK','Mexico','Iran','Russia','China','Israel','Middle East','Ukraine','Palestine Protests','North Korea','United Nations']

load_dotenv(ROOT/'Sonar/.env',override=False)

def json_default(value):
    if isinstance(value,Decimal): return int(value) if value==value.to_integral_value() else str(value)
    if hasattr(value,'isoformat'): return value.isoformat()
    raise TypeError(type(value).__name__)

def get_cids_and_tokens():
    cids=set(); tokens=set(); event_ids=set()
    for selected in GAMMA['events']:
        ev=selected['event']
        if ev.get('id') is not None: event_ids.add(str(ev['id']))
        for m in ev.get('markets') or []:
            cid=m.get('conditionId') or m.get('condition_id')
            if cid: cids.add(str(cid))
            raw=m.get('clobTokenIds') or m.get('clob_token_ids') or []
            if isinstance(raw,str):
                try: raw=json.loads(raw)
                except json.JSONDecodeError: raw=[]
            for token in raw or []:
                if token is not None and str(token): tokens.add(str(token))
    selected_ids=[]
    for fam,ids in PG_SELECTION['events_by_family'].items():
        selected_ids.extend(str(x) for x in ids)
    for ids in PG_SELECTION['ambiguous_events'].values(): selected_ids.extend(str(x) for x in ids)
    event_ids.update(selected_ids)
    selected_event_ids=set(event_ids)
    for ev in RAW_GAMMA.get('events',[]):
        if str(ev.get('id') or '') not in selected_event_ids: continue
        for m in ev.get('markets') or []:
            cid=m.get('conditionId') or m.get('condition_id')
            if cid: cids.add(str(cid))
            raw=m.get('clobTokenIds') or m.get('clob_token_ids') or []
            if isinstance(raw,str):
                try: raw=json.loads(raw)
                except json.JSONDecodeError: raw=[]
            for token in raw or []:
                if token is not None and str(token): tokens.add(str(token))
    candidate_path=CAMPAIGN/'pg_candidate_markets.jsonl'
    selected_ids=set(selected_ids)
    for line in candidate_path.open():
        r=json.loads(line)
        if str(r.get('parent_event_id') or '') in selected_ids:
            if r.get('condition_id'): cids.add(str(r['condition_id']))
            for key in ('yes_token_id','no_token_id'):
                if r.get(key): tokens.add(str(r[key]))
    return sorted(cids),sorted(event_ids),sorted(tokens)

cids,event_ids,tokens=get_cids_and_tokens()
conn=psycopg2.connect(host=os.getenv('PG_HOST','localhost'),port=os.getenv('PG_PORT','5432'),dbname=os.getenv('PG_DATABASE','polymarket'),user=os.getenv('PG_USER','polyuser'),password=os.getenv('PG_PASSWORD'),connect_timeout=8)
conn.set_session(readonly=True,autocommit=False)
try:
    with conn.cursor() as c: c.execute("SET LOCAL statement_timeout='120s'")
    sql="""SELECT m.condition_id,m.parent_event_id,m.question,m.slug,m.parent_event_slug,
 m.start_date_ts,m.end_date_ts,m.resolved_at_ts,m.is_resolved,m.winner_outcome,
 m.outcomes_json,m.yes_token_id,m.no_token_id,m.market_type,m.category,
 ms.total_volume AS metadata_total_volume,ms.liquidity AS metadata_liquidity,
 mt.tags_json AS metadata_tags_json
 FROM public.markets m
 LEFT JOIN public.market_summary ms ON ms.condition_id=m.condition_id
 LEFT JOIN public.market_tags mt ON mt.condition_id=m.condition_id
 WHERE m.condition_id=ANY(%s) OR (m.category=ANY(%s) AND m.parent_event_id=ANY(%s))"""
    with conn.cursor(name='selected_election_groups',cursor_factory=RealDictCursor) as cur, PG_OUT.open('w') as out:
        cur.itersize=500
        cur.execute(sql,(cids,CATEGORIES,event_ids))
        rows=0
        for r in cur:
            out.write(json.dumps(dict(r),ensure_ascii=False,default=json_default,separators=(',',':'))+'\n')
            rows+=1
    with conn.cursor(name='selected_custody_token_map',cursor_factory=RealDictCursor) as cur, TOKEN_OUT.open('w') as out:
        cur.itersize=1000
        cur.execute("SELECT token_id,condition_id,token_side FROM public.custody_ref_token WHERE token_id=ANY(%s)",(tokens,))
        token_rows=0
        for r in cur:
            item={k:(str(v) if v is not None else None) for k,v in r.items()}
            out.write(json.dumps(item,separators=(',',':'))+'\n')
            token_rows+=1
    print(json.dumps({'pg_market_rows':rows,'input_gamma_cids':len(cids),'selected_parent_event_ids':len(event_ids),'token_ids_checked':len(tokens),'custody_ref_token_matches':token_rows,'pg_output':PG_OUT.name,'token_output':TOKEN_OUT.name,'readonly':True}))
    conn.rollback()
finally:
    conn.close()
