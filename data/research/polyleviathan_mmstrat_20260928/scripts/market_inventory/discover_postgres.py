#!/usr/bin/env python3
"""Read-only election market search over PostgreSQL's market dictionary."""
from __future__ import annotations
import json, os, time
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path('/home/ubuntu/polymarketwhale')
OUT = ROOT / 'campaigns/sisterreq_20260925/pg_candidate_markets.jsonl'
load_dotenv(ROOT / 'Sonar/.env', override=False)

US_STATES = [
 'Alabama','Alaska','Arizona','Arkansas','California','Colorado','Connecticut','Delaware','Florida','Georgia','Hawaii','Idaho','Illinois','Indiana','Iowa','Kansas','Kentucky','Louisiana','Maine','Maryland','Massachusetts','Michigan','Minnesota','Mississippi','Missouri','Montana','Nebraska','Nevada','New Hampshire','New Jersey','New Mexico','New York','North Carolina','North Dakota','Ohio','Oklahoma','Oregon','Pennsylvania','Rhode Island','South Carolina','South Dakota','Tennessee','Texas','Utah','Vermont','Virginia','Washington','West Virginia','Wisconsin','Wyoming','District of Columbia'
]
CAN_PROVINCES = ['Ontario','Quebec','British Columbia','Alberta','Manitoba','Saskatchewan','Nova Scotia','New Brunswick','Newfoundland','Prince Edward Island']
GEO = [
 'Colombia','Colombian','Peru','Peruvian','Hungary','Hungarian','Fidesz','TISZA',
 'United States','U.S. election','US election','American president','Electoral College',
 'Canada','Canadian',*CAN_PROVINCES,*US_STATES,
 'Germany','German','Bundestag','CDU','AfD','SPD','Die Linke','BSW',
 'Australia','Australian','Poland','Polish','Bolivia','Bolivian','Ecuador','Ecuadorian',
 'Chile','Chilean','Japan','Japanese','House of Councillors','Norway','Norwegian',
 'Netherlands','Dutch','Czech','Czech Republic'
]
ELECT = [
 'election','president','presidency','parliament','senate','house of representatives',
 'house of delegates','governor','councillor','council election','turnout','runoff',
 'popular vote','vote share','most seats','seat count','seats','seat','government',
 'coalition','majority','minority','balance of power','electoral college','candidate',
 'constituency','riding','electorate','party control','chamber control','trifecta',
 'sweep','margin of victory','party','congress','chancellor','prime minister','cabinet'
]
POLITICAL_CATEGORIES = [
 'Politics','Elections','Global Elections','World Elections','U.S. Politics',
 'United States','USA Election','US Election','u.s. 2024 elections','Canada','Poland',
 'Trump Presidency','Trump','Primaries','nomination','Inauguration','General',
 'Geopolitics','UK','Mexico','Iran','Russia','China','Israel','Middle East','Ukraine',
 'Palestine Protests','North Korea','United Nations'
]
GEO_PATTERNS = ['%' + s + '%' for s in GEO]
ELECT_PATTERNS = ['%' + s + '%' for s in ELECT]
FIELDS = ('question','slug','parent_event_slug','description')
SELECT = """m.condition_id,m.parent_event_id,m.question,m.slug,m.parent_event_slug,m.description,
 m.start_date_ts,m.end_date_ts,m.resolved_at_ts,m.is_resolved,m.winner_outcome,
 m.outcomes_json,m.yes_token_id,m.no_token_id,m.market_type,m.category"""

conn = psycopg2.connect(
 host=os.getenv('PG_HOST','localhost'), port=os.getenv('PG_PORT','5432'),
 dbname=os.getenv('PG_DATABASE','polymarket'), user=os.getenv('PG_USER','polyuser'),
 password=os.getenv('PG_PASSWORD'), connect_timeout=8,
)
conn.set_session(readonly=True, autocommit=False)
try:
    with conn.cursor() as control:
        control.execute("SET LOCAL statement_timeout='120s'")
        control.execute("SET LOCAL idle_in_transaction_session_timeout='120s'")
    clauses = []
    params = []
    for patterns in (GEO_PATTERNS, ELECT_PATTERNS):
        clauses.append('(' + ' OR '.join(f"m.{field} ILIKE ANY(%s)" for field in FIELDS) + ')')
        params.extend([patterns] * len(FIELDS))
    sql = f"SELECT {SELECT} FROM public.markets m WHERE m.category = ANY(%s) AND {' AND '.join(clauses)}"
    params.insert(0, POLITICAL_CATEGORIES)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    started = time.monotonic()
    with conn.cursor(name='election_market_search', cursor_factory=RealDictCursor) as cur, OUT.open('w') as out:
        cur.itersize = 1000
        cur.execute(sql, params)
        for row in cur:
            normalized = {}
            for key, value in row.items():
                if hasattr(value, 'isoformat'):
                    value = value.isoformat()
                elif value is not None and key.endswith('_ts'):
                    try: value = int(value)
                    except (ValueError, TypeError, OverflowError): value = str(value)
                elif isinstance(value, Decimal):
                    value = int(value) if value == value.to_integral_value() else str(value)
                elif key in ('yes_token_id','no_token_id','condition_id','parent_event_id') and value is not None:
                    value = str(value)
                normalized[key] = value
            out.write(json.dumps(normalized, ensure_ascii=False, separators=(',',':')) + '\n')
            count += 1
    print(json.dumps({'rows':count,'elapsed_seconds':round(time.monotonic()-started,2),'output':OUT.name,'fields_searched':list(FIELDS),'indexed_category_scope':POLITICAL_CATEGORIES,'readonly':True,'statement_timeout_seconds':120}))
    conn.rollback()
finally:
    conn.close()
