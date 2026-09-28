#!/usr/bin/env python3
"""Select election-market event families from the cached Gamma candidate corpus."""
from __future__ import annotations
import csv, json, re
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SRC=ROOT/'gamma_events.json'
OUT=ROOT/'selected_gamma_events.json'
AUDIT=ROOT/'selected_event_audit.csv'
US_STATES={
'Alabama','Alaska','Arizona','Arkansas','California','Colorado','Connecticut','Delaware','Florida','Georgia','Hawaii','Idaho','Illinois','Indiana','Iowa','Kansas','Kentucky','Louisiana','Maine','Maryland','Massachusetts','Michigan','Minnesota','Mississippi','Missouri','Montana','Nebraska','Nevada','New Hampshire','New Jersey','New Mexico','New York','North Carolina','North Dakota','Ohio','Oklahoma','Oregon','Pennsylvania','Rhode Island','South Carolina','South Dakota','Tennessee','Texas','Utah','Vermont','Virginia','Washington','West Virginia','Wisconsin','Wyoming','District of Columbia','Washington DC'
}


def mdate(v):
    return str(v or '')[:10]

def dates(event):
    return [mdate(m.get('endDate')) for m in event.get('markets',[]) if mdate(m.get('endDate'))]

def start_dates(event):
    return [mdate(m.get('startDate')) for m in event.get('markets',[]) if mdate(m.get('startDate'))]

def has_date(ds, starts):
    return any(any(d.startswith(s) for s in starts) for d in ds)

def joined(event):
    ms=event.get('markets') or []
    return ' '.join([str(event.get('title') or ''),str(event.get('slug') or ''),*[
        str(m.get('question') or '')+' '+str(m.get('slug') or '') for m in ms
    ]]).lower()

def family(event):
    text=joined(event); ds=dates(event); title=(str(event.get('title') or '')+' '+str(event.get('slug') or '')).lower()
    election_words=any(k in text for k in ('election','parliament','senate','president','governor','house of delegates','runoff'))
    if 'colombia' in text and election_words and has_date(ds,['2026-']): return 'COL_2026'
    if 'peru' in text and election_words and has_date(ds,['2026-']) and 'pardoned' not in text: return 'PER_2026'
    if any(k in text for k in ('hungary','hungarian','fidesz','tisza')) and election_words and has_date(ds,['2026-04']): return 'HUN_2026'
    if ('virginia' in text or 'new jersey' in text) and any(k in text for k in ('governor','house of delegates','election')) and has_date(ds,['2025-11']): return 'USA_STATES_2025'
    # US general-election markets share the verified 2024-11-02 through 2024-11-08 settlement window.
    if has_date(ds,['2024-11-02','2024-11-03','2024-11-04','2024-11-05','2024-11-06','2024-11-07','2024-11-08']):
        state_hit=any(s.lower() in text for s in US_STATES)
        query_hint=' '.join(map(str,event.get('_search_queries',[]))).lower()
        us_hit=state_hit or any(k in text for k in ('2024 us','united states','u.s. election','usa presidential','balance of power')) or ('2024 us' in query_hint and any(k in text for k in ('presidential','popular vote','turnout','electoral college','senate','house','election')))
        direct=any(k in text for k in ('election','senate','house','president','electoral college','popular vote','turnout','third party','states will move','closest state','last state'))
        if us_hit and direct and not any(k in text for k in ('primary','nominee','caucus')): return 'US_2024'
    # Canadian federal markets settle on or around 2025-04-28; retain linked province seat families.
    canada_area=any(k in text for k in ('canada','canadian','ontario','quebec','british columbia','alberta','manitoba','saskatchewan','nova scotia','new brunswick','newfoundland','prince edward island'))
    canada_link=any(k in text for k in ('election','government','seats','seat count','vote share','popular vote','turnout','margin of victory'))
    if canada_area and canada_link and (has_date(ds,['2025-04','2025-10']) or has_date(start_dates(event),['2025-04'])): return 'CAN_2025'
    if ('germany' in text or 'german' in text or 'bundestag' in text) and (election_words or 'fraktion' in text) and has_date(ds,['2025-02','2025-03']): return 'GER_2025'
    if ('australia' in text or 'australian' in text) and any(k in text for k in ('election','prime minister','parliament','electorate')) and has_date(ds,['2025-05']): return 'AUS_2025'
    if ('poland' in text or 'polish' in text) and 'election' in text and has_date(ds,['2025-05','2025-06']): return 'POL_2025'
    if ('bolivia' in text or 'bolivian' in text) and 'election' in text and has_date(ds,['2025-08','2025-10']): return 'BOL_2025'
    if ('ecuador' in text or 'ecuadorian' in text) and 'election' in text and has_date(ds,['2025-04']): return 'ECU_2025'
    if ('chile' in text or 'chilean' in text) and any(k in text for k in ('election','runoff')) and has_date(ds,['2025-11','2025-12','2026-03']): return 'CHL_2025'
    if ('japan' in text or 'japanese' in text) and ('election' in text or 'councillors' in text) and has_date(ds,['2025-07']): return 'JPN_2025'
    if ('norway' in text or 'norwegian' in text) and 'election' in text and has_date(ds,['2025-09']): return 'NOR_2025'
    if ('netherlands' in text or 'dutch' in text) and 'election' in text and has_date(ds,['2025-10','2026-07']): return 'NLD_2025'
    if ('czech' in text) and 'election' in text and has_date(ds,['2025-09','2025-10']): return 'CZE_2025'
    return None


def main():
    data=json.loads(SRC.read_text())
    selected=[]; audits=[]
    for event in data['events']:
        key=family(event)
        if not key: continue
        markets=event.get('markets') or []
        selected.append({'research_family':key,'event':event})
        ds=dates(event)
        audits.append({'research_family':key,'event_id':str(event.get('id') or ''),'event_name':event.get('title') or event.get('name') or '', 'event_slug':event.get('slug') or '', 'market_count':len(markets),'market_ids':'|'.join(str(m.get('id') or '') for m in markets),'market_end_dates':'|'.join(sorted(set(ds))),'search_queries':'|'.join(event.get('_search_queries') or [])})
    # Keep a clear, deterministic order without collapsing market IDs or equal question text.
    selected.sort(key=lambda x:(x['research_family'],str(x['event'].get('id') or '')))
    tmp=OUT.with_suffix('.json.tmp'); tmp.write_text(json.dumps({'source':str(SRC.name),'candidate_event_count':len(data['events']),'selected_event_count':len(selected),'events':selected},ensure_ascii=False,indent=2)+'\n'); tmp.replace(OUT)
    with AUDIT.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['research_family','event_id','event_name','event_slug','market_count','market_ids','market_end_dates','search_queries']); w.writeheader(); w.writerows(audits)
    counts={}
    for row in audits: counts[row['research_family']]=counts.get(row['research_family'],0)+1
    print('candidate_events',len(data['events']))
    print('selected_events',len(selected),'selected_markets',sum(len(x['event'].get('markets') or []) for x in selected))
    for k,n in sorted(counts.items()): print(k,n)
    print('audit',AUDIT.name,'manifest',OUT.name)

if __name__=='__main__': main()
