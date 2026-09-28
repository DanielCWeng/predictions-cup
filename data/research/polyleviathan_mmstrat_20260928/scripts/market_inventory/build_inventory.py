#!/usr/bin/env python3
"""Merge PostgreSQL market identities with cached Gamma identities."""
from __future__ import annotations
import csv, json, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('/home/ubuntu/polymarketwhale/campaigns/sisterreq_20260925')
BASE_GAMMA=json.loads((ROOT/'selected_gamma_events.json').read_text())
RAW_GAMMA=json.loads((ROOT/'gamma_events.json').read_text())
PG_SELECTION=json.loads((ROOT/'pg_supplemental_event_selections.json').read_text())
PG_ROWS=[json.loads(line) for line in (ROOT/'pg_inventory_markets.jsonl').open()]
TOKEN_MAP={}
for line in (ROOT/'pg_token_identity.jsonl').open():
    r=json.loads(line); TOKEN_MAP.setdefault(str(r.get('token_id') or ''),[]).append(r)
TOKEN_MAP_BY_CID=defaultdict(list)
for line in (ROOT/'pg_tokens_by_condition.jsonl').open():
    r=json.loads(line)
    TOKEN_MAP_BY_CID[str(r.get('condition_id') or '').lower()].append(r)
CANDIDATE_DESCRIPTIONS={}
for line in (ROOT/'pg_candidate_markets.jsonl').open():
    r=json.loads(line); cid=str(r.get('condition_id') or '').lower()
    if cid and r.get('description'): CANDIDATE_DESCRIPTIONS.setdefault(cid,r['description'])

CSV_OUT=ROOT/'polyleviathan_election_market_inventory.csv'
JSON_OUT=ROOT/'polyleviathan_election_family_manifest.json'
SUMMARY_OUT=ROOT/'inventory_build_summary.json'

FAMILY_META={
 'COL_2026':('Colombia','Colombia 2026 General Elections'),
 'PER_2026':('Peru','Peru 2026 General Elections'),
 'HUN_2026':('Hungary','Hungary 2026 Parliamentary Election'),
 'US_2024':('United States','United States 2024 General Election'),
 'USA_STATES_2025':('United States','United States 2025 State Elections'),
 'CAN_2025':('Canada','Canada 2025 Federal Election'),
 'GER_2025':('Germany','Germany 2025 Federal Election'),
 'AUS_2025':('Australia','Australia 2025 Federal Election'),
 'POL_2025':('Poland','Poland 2025 Presidential Election'),
 'BOL_2025':('Bolivia','Bolivia 2025 Presidential Election'),
 'ECU_2025':('Ecuador','Ecuador 2025 Presidential Election'),
 'CHL_2025':('Chile','Chile 2025 Presidential Election'),
 'JPN_2025':('Japan','Japan 2025 House of Councillors Election'),
 'NOR_2025':('Norway','Norway 2025 Parliamentary Election'),
 'NLD_2025':('Netherlands','Netherlands 2025 Parliamentary Election'),
 'CZE_2025':('Czech Republic','Czech Republic 2025 Parliamentary Election'),
 'JPN_2026':('Japan','Japan 2026 Snap General Election'),
 'SVN_2026':('Slovenia','Slovenia 2026 Parliamentary Election'),
 'LBN_2026':('Lebanon','Lebanon 2026 Parliamentary Election'),
 'AMBIGUOUS_2025':(None,'Unresolved 2025 Election Market'),
}

def parse_json_array(value):
    if value is None: return []
    if isinstance(value,list): return value
    if isinstance(value,str):
        try:
            parsed=json.loads(value)
            return parsed if isinstance(parsed,list) else []
        except json.JSONDecodeError: return []
    return []

def norm(value):
    return re.sub(r'\s+',' ',str(value or '')).strip().casefold()

def iso_epoch(value):
    if value is None or value=='': return None
    try:
        return datetime.fromtimestamp(int(float(value)),timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    except (ValueError,TypeError,OverflowError,OSError): return None

def iso_any(value):
    if value is None or value=='': return None
    if isinstance(value,(int,float)): return iso_epoch(value)
    text=str(value).strip()
    try:
        dt=datetime.fromisoformat(text.replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    except ValueError: return text

def truth(value):
    if value is None: return None
    if isinstance(value,bool): return value
    if isinstance(value,(int,float)): return bool(value)
    if isinstance(value,str):
        if value.casefold() in ('true','1','yes'): return True
        if value.casefold() in ('false','0','no'): return False
    return None

def market_family(text):
    t=str(text or '').casefold()
    if 'turnout' in t: return 'TURNOUT'
    if 'margin' in t: return 'MARGIN'
    if any(x in t for x in ('advance to','advances to','qualify for','qualifies for','runoff pair','advance from')): return 'RUNOFF_QUALIFICATION_OR_PAIR'
    if any(x in t for x in ('second round','2nd round','runoff')): return 'PRESIDENTIAL_RUNOFF'
    if any(x in t for x in ('first round','1st round')): return 'PRESIDENTIAL_FIRST_ROUND'
    if any(x in t for x in ('balance of power','sweep','trifecta','presidency +','presidency and')): return 'JOINT_ELECTION_OUTCOME'
    if any(x in t for x in ('coalition','next government','government be formed','prime minister','chancellor','cabinet')): return 'GOVERNMENT_FORMATION'
    if any(x in t for x in ('senate','senator')): return 'SENATE_OR_UPPER_CHAMBER'
    if any(x in t for x in ('chamber of deputies','chamber of representatives','house of delegates','house of representatives','house seat','congressional district','house election')): return 'HOUSE_OR_LOWER_CHAMBER'
    if any(x in t for x in ('seats','seat count','most seats','second most seats','third most seats','majority of seats')): return 'SEAT_COUNT_OR_RANK'
    if any(x in t for x in ('popular vote','vote share','votes','% of vote','percent of vote','most votes')): return 'VOTE_SHARE_OR_POPULAR_VOTE'
    if any(x in t for x in ('governor','governor election')): return 'GOVERNOR_RACE'
    if any(x in t for x in ('riding','constituency','electorate','district race','district election')): return 'CONSTITUENCY_OR_DISTRICT_RACE'
    if any(x in t for x in ('president','presidential')): return 'PRESIDENTIAL_RESULT'
    if any(x in t for x in ('election winner','who will win','which party wins')): return 'ELECTION_WINNER'
    return 'OTHER_ELECTION_PROPOSITION'

def exact_election_dates(text, question):
    months={m.lower():i for i,m in enumerate(('January','February','March','April','May','June','July','August','September','October','November','December'),1)}
    date_re=re.compile(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b',re.I)
    found=[]
    for m in date_re.finditer(text or ''):
        around=(text[max(0,m.start()-100):m.end()+50] or '').casefold()
        # Keep a date only when nearby text identifies an election, vote, or election round.
        if not re.search(r'(election|vote|voting|polling|runoff|round|polls)',around): continue
        key=f"{int(m.group(3)):04d}-{months[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
        if key not in found: found.append(key)
    if not found: return None
    q=(question or '').casefold()
    if any(x in q for x in ('second round','2nd round','runoff')) and len(found)>1:
        return found[-1]
    if any(x in q for x in ('first round','1st round')) and len(found)>1:
        return found[0]
    return '|'.join(found)

def safe_ids(values):
    return [str(v) for v in values if v is not None and str(v).strip()]

# Establish selected event IDs and research-family membership.
family_by_event={}
event_by_id={}
for selected in BASE_GAMMA['events']:
    ev=selected['event']; eid=str(ev.get('id') or '')
    if eid:
        family_by_event[eid]=selected['research_family']; event_by_id[eid]=ev
for family,ids in PG_SELECTION['events_by_family'].items():
    for eid in ids:
        eid=str(eid)
        if eid in family_by_event and family_by_event[eid]!=family:
            raise RuntimeError(f'event {eid} has conflicting research families')
        family_by_event[eid]=family
for _,ids in PG_SELECTION['ambiguous_events'].items():
    for eid in ids:
        eid=str(eid); family_by_event[eid]='AMBIGUOUS_2025'

raw_events={}
for ev in RAW_GAMMA.get('events',[]):
    eid=str(ev.get('id') or '')
    if eid and eid in family_by_event:
        raw_events.setdefault(eid,ev)
        if eid in event_by_id: raw_events[eid]=event_by_id[eid]
        else: event_by_id[eid]=ev

pg_by_cid={}
for r in PG_ROWS:
    cid=str(r.get('condition_id') or '').lower()
    if cid:
        if cid in pg_by_cid: raise RuntimeError(f'duplicate PostgreSQL condition_id {cid}')
        pg_by_cid[cid]=r
pg_by_parent=defaultdict(list)
for r in PG_ROWS:
    eid=str(r.get('parent_event_id') or '')
    if eid: pg_by_parent[eid].append(r)

gamma_market_refs=[]
gamma_cids=set()
for eid,ev in raw_events.items():
    for gm in ev.get('markets') or []:
        cid=str(gm.get('conditionId') or gm.get('condition_id') or '').strip()
        if cid: gamma_cids.add(cid.lower())
        gamma_market_refs.append((eid,ev,gm))

# Preserve each Gamma market ID, including duplicate questions and repeated CIDs.
rows=[]; matched_pg_cids=set()
for eid,ev,gm in gamma_market_refs:
    cid=str(gm.get('conditionId') or gm.get('condition_id') or '').strip()
    pg=pg_by_cid.get(cid.lower()) if cid else None
    if pg: matched_pg_cids.add(str(pg.get('condition_id') or '').lower())
    rows.append({'family':family_by_event[eid],'event':ev,'gamma':gm,'pg':pg,'identity_source':'both' if pg else 'gamma_only'})

# Keep selected PostgreSQL instruments that Gamma does not identify by market ID.
for pg in PG_ROWS:
    cid=str(pg.get('condition_id') or '').lower()
    if cid and cid in matched_pg_cids: continue
    eid=str(pg.get('parent_event_id') or '')
    family=family_by_event.get(eid)
    if not family:
        raise RuntimeError(f'PostgreSQL market {cid} belongs to an unselected parent event {eid!r}')
    rows.append({'family':family,'event':event_by_id.get(eid),'gamma':None,'pg':pg,'identity_source':'pg_only'})

# Attach exact token orientation checks and all source disagreements.
clean=[]
for source_row in rows:
    family=source_row['family']; ev=source_row['event'] or {}; gm=source_row['gamma'] or {}; pg=source_row['pg'] or {}
    notes=[]
    eid_gamma=str(ev.get('id') or '')
    eid_pg=str(pg.get('parent_event_id') or '')
    if eid_gamma and eid_pg and eid_gamma!=eid_pg: notes.append(f'event_id_conflict_gamma={eid_gamma}_pg={eid_pg}')
    eid=eid_pg or eid_gamma or None
    if not eid: notes.append('event_id_missing_from_both_sources')
    event_name=str(ev.get('title') or ev.get('name') or '').strip() or None
    event_slug=str(ev.get('slug') or '').strip() or None
    pg_event_slug=str(pg.get('parent_event_slug') or '').strip() or None
    if event_slug and pg_event_slug and norm(event_slug)!=norm(pg_event_slug):
        notes.append('event_slug_pg_gamma_conflict')
    event_slug=pg_event_slug or event_slug

    q_pg=str(pg.get('question') or '').strip() or None
    q_gamma=str(gm.get('question') or '').strip() or None
    if q_pg and q_gamma and norm(q_pg)!=norm(q_gamma): notes.append('question_pg_gamma_conflict')
    question=q_pg or q_gamma
    if not question: notes.append('question_missing_from_both_sources')
    slug_pg=str(pg.get('slug') or '').strip() or None
    slug_gamma=str(gm.get('slug') or '').strip() or None
    if slug_pg and slug_gamma and norm(slug_pg)!=norm(slug_gamma): notes.append('market_slug_pg_gamma_conflict')
    market_slug=slug_pg or slug_gamma

    cid_pg=str(pg.get('condition_id') or '').strip() or None
    cid_gamma=str(gm.get('conditionId') or gm.get('condition_id') or '').strip() or None
    if cid_pg and cid_gamma and cid_pg.lower()!=cid_gamma.lower(): notes.append('condition_id_pg_gamma_conflict')
    cid=cid_pg or cid_gamma
    if not cid: notes.append('condition_id_missing_from_both_sources')
    mid=str(gm.get('id') or '').strip() or None
    negrisk=str(gm.get('negRiskMarketID') or gm.get('negRiskMarketId') or '').strip() or None
    if negrisk in ('0','null','None'): negrisk=None
    if truth(gm.get('negRisk')) is True and not negrisk: notes.append('Gamma_marks_NegRisk_but_market_id_is_missing')

    outcomes_pg=parse_json_array(pg.get('outcomes_json'))
    outcomes_gamma=parse_json_array(gm.get('outcomes'))
    if outcomes_pg and outcomes_gamma and [norm(x) for x in outcomes_pg]!=[norm(x) for x in outcomes_gamma]:
        notes.append('outcome_labels_pg_gamma_conflict')
    outcomes=outcomes_gamma or outcomes_pg
    g_tokens=safe_ids(parse_json_array(gm.get('clobTokenIds') or gm.get('clob_token_ids')))
    p_yes=str(pg.get('yes_token_id') or '').strip() or None
    p_no=str(pg.get('no_token_id') or '').strip() or None
    p_tokens=[x for x in (p_yes,p_no) if x]
    condition_tokens=TOKEN_MAP_BY_CID.get((cid or '').lower(),[])
    side_to_tokens=defaultdict(set)
    for mapped_token in condition_tokens:
        side=str(mapped_token.get('token_side') or '').upper()
        token=str(mapped_token.get('token_id') or '')
        if token and side in ('YES','NO'): side_to_tokens[side].add(token)
    if p_yes and p_no and p_yes==p_no: notes.append('PostgreSQL_yes_no_token_ids_are_equal')
    if len(g_tokens)>=2 and p_yes and p_no and (g_tokens[0]!=p_yes or g_tokens[1]!=p_no):
        notes.append('Gamma_token_order_differs_from_PostgreSQL_yes_no_fields')

    token_outcome={}
    if g_tokens and outcomes and len(g_tokens)==len(outcomes):
        for token,label in zip(g_tokens,outcomes): token_outcome[token]=str(label)
    elif g_tokens and len(g_tokens)==2 and not outcomes:
        token_outcome[g_tokens[0]]='Yes'; token_outcome[g_tokens[1]]='No'
    yes=p_yes; no=p_no
    if not yes and not no:
        for token,label in token_outcome.items():
            if norm(label)=='yes': yes=token
            elif norm(label)=='no': no=token
    # Exact condition-linked custody mappings can restore missing binary sides.
    # Infer a side only when custody_ref_token has one unique token for that side.
    if not yes and len(side_to_tokens['YES'])==1:
        yes=next(iter(side_to_tokens['YES']))
    if not no and len(side_to_tokens['NO'])==1:
        no=next(iter(side_to_tokens['NO']))
    if len(side_to_tokens['YES'])>1:
        notes.append('multiple_custody_ref_token_YES_ids; no_side_inference')
    if len(side_to_tokens['NO'])>1:
        notes.append('multiple_custody_ref_token_NO_ids; no_side_inference')
    for token, expected_side in ((yes,'YES'),(no,'NO')):
        mapped_sides={str(x.get('token_side') or '').upper() for x in condition_tokens if str(x.get('token_id') or '')==token and x.get('token_side')}
        if mapped_sides and expected_side not in mapped_sides:
            notes.append(f'condition_token_side_conflict_{expected_side}_is_{"_or_".join(sorted(mapped_sides))}')
    if yes: token_outcome.setdefault(yes,'Yes')
    if no: token_outcome.setdefault(no,'No')
    all_tokens=[]
    for token in [*p_tokens,*g_tokens,*[str(x.get('token_id') or '') for x in condition_tokens]]:
        if not token: continue
        if token not in all_tokens: all_tokens.append(token)
    source_tokens=set(p_tokens)|set(g_tokens)
    custody_additions=[str(x.get('token_id') or '') for x in condition_tokens if str(x.get('token_id') or '') and str(x.get('token_id') or '') not in source_tokens]
    if custody_additions:
        notes.append(f'custody_ref_token_adds_{len(set(custody_additions))}_condition_linked_token_ids_without_Gamma_or_markets_token_identity')
    unlabelled_custody_tokens={str(x.get('token_id') or '') for x in condition_tokens if not x.get('token_side') and str(x.get('token_id') or '')}
    if unlabelled_custody_tokens:
        notes.append(f'{len(unlabelled_custody_tokens)}_condition_linked_tokens_have_no_custody_side')
    other_tokens=[t for t in all_tokens if t not in (yes,no)]
    if not all_tokens: notes.append('outcome_token_ids_missing_from_both_sources')
    elif not yes or not no:
        notes.append('YES_NO_orientation_not_verified; token IDs remain in outcome token list')

    orientation_ok=True
    for token,expected_side in ((yes,'YES'),(no,'NO')):
        if not token: continue
        mapped=TOKEN_MAP.get(token,[])
        if mapped:
            for ref in mapped:
                ref_cid=str(ref.get('condition_id') or '')
                side=str(ref.get('token_side') or '').upper()
                if cid and ref_cid.lower()!=cid.lower():
                    notes.append(f'custody_ref_token_condition_conflict_{expected_side}')
                    orientation_ok=False
                if side and side!=expected_side:
                    notes.append(f'custody_ref_token_side_conflict_{expected_side}_is_{side}')
                    orientation_ok=False
        elif pg:
            notes.append(f'custody_ref_token_missing_{expected_side}')
            orientation_ok=False
    if len({t for t in all_tokens})<len(all_tokens): notes.append('duplicate_token_id_within_market')

    p_start=iso_epoch(pg.get('start_date_ts')); g_start=iso_any(gm.get('startDate'))
    p_end=iso_epoch(pg.get('end_date_ts')); g_end=iso_any(gm.get('endDate'))
    if p_start and g_start and p_start!=g_start: notes.append(f'market_start_pg={p_start}_gamma={g_start}')
    if p_end and g_end and p_end!=g_end: notes.append(f'market_end_pg={p_end}_gamma={g_end}')
    start=p_start or g_start; end=p_end or g_end
    resolved=truth(pg.get('is_resolved'))
    if resolved is None: resolved=truth(gm.get('resolved'))
    active=truth(gm.get('active'))
    closed=truth(gm.get('closed'))
    if resolved is True: status='resolved'
    elif resolved is False and active is True: status='active'
    elif resolved is False and closed is True: status='closed_unresolved'
    elif resolved is False: status='unresolved'
    elif active is True: status='active'
    elif closed is True: status='closed_status_unknown'
    else: status='unknown'
    resolution_time=iso_epoch(pg.get('resolved_at_ts'))
    resolution_time_source='markets.resolved_at_ts' if resolution_time else None
    if not resolution_time:
        resolution_time=iso_any(gm.get('closedTime') or gm.get('closed_time'))
        if resolution_time: resolution_time_source='Gamma.closedTime'

    family_country,election_name=FAMILY_META.get(family,(None,family))
    if family=='AMBIGUOUS_2025':
        notes.append('ambiguous_country: PG parent slug identifies Norway; question identifies Australia')
        family_country=None
    country=family_country
    if family_country and (family_country=='United States'):
        country='United States'
    market_text=' '.join([str(ev.get('title') or ''),str(event_slug or ''),str(question or ''),str(market_slug or '')])
    m_family=market_family(market_text)
    description=' '.join([str(ev.get('description') or ''),str(gm.get('description') or ''),str(CANDIDATE_DESCRIPTIONS.get((cid or '').lower(),'') or '')])
    election_date=exact_election_dates(description,question or '')
    if not election_date: notes.append('election_date_not_explicit_in_available_Gamma_or_PostgreSQL_descriptions')
    if not event_name: notes.append('Polymarket_event_name_missing_from_Gamma')
    if not mid: notes.append('numeric_market_id_missing_from_Gamma')

    confidence='high'
    if any('conflict' in n for n in notes): confidence='low'
    elif family=='AMBIGUOUS_2025': confidence='low'
    elif not cid or not all_tokens or (not yes and not no): confidence='low'
    elif source_row['identity_source']!='both' or not orientation_ok: confidence='medium'
    if source_row['identity_source']=='gamma_only' and (not cid or not mid): confidence='low'

    token_records=[]
    for token in all_tokens:
        mapped=TOKEN_MAP.get(token,[])
        sides={str(x.get('token_side') or '').upper() for x in mapped if x.get('token_side')}
        token_side='YES' if token==yes else 'NO' if token==no else None
        if len(sides)==1:
            custody_side=next(iter(sides))
            if token_side and custody_side!=token_side: notes.append(f'custody_side_mismatch_{token_side}_token_is_{custody_side}')
            token_side=custody_side
        labels=token_outcome.get(token)
        token_records.append({'token_id':token,'outcome_label':labels,'token_side':token_side,'fill_available':None,'first_fill_timestamp':None,'last_fill_timestamp':None,'fill_count':None,'traded_notional_usd':None,'distinct_fill_days':None,'fill_source':None})

    source_row_out={
      'research_family':family,'country':country,'election_name':election_name,'election_date':election_date,
      'market_family':m_family,'event_name':event_name,'event_id':eid,'event_slug':event_slug,
      'market_id':mid,'market_slug':market_slug,'condition_id':cid,'negrisk_market_id':negrisk,
      'question':question,'outcome_labels':json.dumps([str(x) for x in outcomes],ensure_ascii=False) if outcomes else None,
      'yes_token_id':yes,'no_token_id':no,'other_token_ids':json.dumps(other_tokens,ensure_ascii=False) if other_tokens else None,
      'market_start':start,'market_end':end,'resolution_time':resolution_time,'resolution_time_source':resolution_time_source,
      'is_resolved':resolved,'market_status':status,'winner_outcome':pg.get('winner_outcome') or gm.get('outcome'),
      'fill_available':None,'first_fill_timestamp':None,'last_fill_timestamp':None,'fill_count':None,
      'traded_notional_usd':None,'distinct_fill_days':None,'fill_source':None,
      'identity_source':source_row['identity_source'],'identity_confidence':confidence,
      'token_fill_stats_json':json.dumps(token_records,ensure_ascii=False,separators=(',',':')),
      'notes':'; '.join(dict.fromkeys(notes)),
    }
    source_row['_outcome_tokens']=token_records
    source_row['_flat']=source_row_out
    source_row['_textnorm']=norm(question)
    clean.append(source_row)

# Mark, but never collapse, repeated question text across distinct market IDs.
by_question=defaultdict(list)
for x in clean:
    if x['_textnorm']: by_question[x['_textnorm']].append(x)
for group in by_question.values():
    id_pairs={(x['_flat']['market_id'],x['_flat']['condition_id']) for x in group}
    distinct={pair for pair in id_pairs if pair!=(None,None)}
    if len(distinct)>1:
        ids=','.join(f'{m or "unknown_market_id"}:{c or "unknown_cid"}' for m,c in sorted(distinct,key=lambda x:(x[0] or '',x[1] or '')))
        for x in group:
            current=x['_flat']['notes'] or ''
            marker=f'duplicate_question_preserved_across_ids={ids}'
            x['_flat']['notes']='; '.join(y for y in [current,marker] if y)

# Sort reproducibly by family, event ID, then exact market ID/CID.
clean.sort(key=lambda x:(x['_flat']['research_family'],x['_flat']['event_id'] or '',x['_flat']['market_id'] or '',x['_flat']['condition_id'] or ''))
fields=['research_family','country','election_name','election_date','market_family','event_name','event_id','event_slug','market_id','market_slug','condition_id','negrisk_market_id','question','outcome_labels','yes_token_id','no_token_id','other_token_ids','market_start','market_end','resolution_time','resolution_time_source','is_resolved','market_status','winner_outcome','fill_available','first_fill_timestamp','last_fill_timestamp','fill_count','traded_notional_usd','distinct_fill_days','fill_source','identity_source','identity_confidence','token_fill_stats_json','notes']
with CSV_OUT.open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); writer.writeheader()
    for x in clean: writer.writerow(x['_flat'])

# Hierarchical manifest repeats the exact row inventory and keeps per-token fill slots.
elections={}
for x in clean:
    flat=x['_flat']; family=flat['research_family']
    election=elections.setdefault(family,{
      'research_family':family,'country':flat['country'],'election_name':flat['election_name'],
      'events':{},'market_families':{},
    })
    event_key=flat['event_id'] or f"unresolved:{flat['event_slug'] or flat['condition_id'] or flat['market_id']}"
    event=election['events'].setdefault(event_key,{
      'event_id':flat['event_id'],'event_name':flat['event_name'],'event_slug':flat['event_slug'],
      'identity_source':flat['identity_source'],'markets':[]
    })
    market={k:flat[k] for k in fields if k not in ('token_fill_stats_json',)}
    market['outcome_tokens']=x['_outcome_tokens']
    market_family=election['market_families'].setdefault(flat['market_family'],{'market_count':0,'markets':[]})
    market_family['market_count']+=1; market_family['markets'].append(market)
    event['markets'].append({'market_id':flat['market_id'],'condition_id':flat['condition_id'],'question':flat['question']})

# Event counts are based on exact non-null event IDs, not names or questions.
for family,election in elections.items():
    election['event_count']=len({x['_flat']['event_id'] for x in clean if x['_flat']['research_family']==family and x['_flat']['event_id']})
    election['market_count']=sum(v['market_count'] for v in election['market_families'].values())
    election['token_id_count']=len({t['token_id'] for x in clean if x['_flat']['research_family']==family for t in x['_outcome_tokens']})
    election['identity_source_rows']=dict(Counter(x['_flat']['identity_source'] for x in clean if x['_flat']['research_family']==family))

summary={'status':'identity_complete_fill_pending','market_rows':len(clean),'event_count':len({x['_flat']['event_id'] for x in clean if x['_flat']['event_id']}),'unique_token_ids':len({t['token_id'] for x in clean for t in x['_outcome_tokens']}),'identity_source_rows':dict(Counter(x['_flat']['identity_source'] for x in clean)),'identity_confidence_rows':dict(Counter(x['_flat']['identity_confidence'] for x in clean)),'gamma_market_id_missing_rows':sum(not x['_flat']['market_id'] for x in clean),'condition_id_missing_rows':sum(not x['_flat']['condition_id'] for x in clean),'token_id_missing_rows':sum(not x['_outcome_tokens'] for x in clean),'families':{k:{'events':v['event_count'],'markets':v['market_count'],'token_ids':v['token_id_count'],'identity_source_rows':v['identity_source_rows']} for k,v in elections.items()}}
manifest={'schema_version':1,'source_notes':{'postgresql':'markets fields; custody_ref_token token_id,condition_id,token_side','gamma':'event and market IDs, names/slugs, NegRisk IDs, outcomes, token order, dates','fill_metrics':'null until the canonical filtered fill scan completes'},'elections':elections,'summary':summary}
JSON_OUT.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
SUMMARY_OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
print('csv',CSV_OUT.name,'manifest',JSON_OUT.name)
