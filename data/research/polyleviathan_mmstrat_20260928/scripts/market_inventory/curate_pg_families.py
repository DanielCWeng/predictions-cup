#!/usr/bin/env python3
"""Curate PG-discovered whole election event families for the inventory."""
from __future__ import annotations
import collections, datetime, json, re
from pathlib import Path

ROOT=Path('/home/ubuntu/polymarketwhale/campaigns/sisterreq_20260925')
PG=ROOT/'pg_candidate_markets.jsonl'
GAMMA=ROOT/'selected_gamma_events.json'
OUT=ROOT/'pg_supplemental_event_selections.json'

MANUAL={
 'COL_2026':['34578','246364','34581'],
 'PER_2026':['213277','574717','385619','574464'],
 'HUN_2026':['326476','291049','34038'],
 'CAN_2025':['21218','21312','16125','21245','21307','21309','21182','20203','23258','23259','23260','16432','23261','23255','23256','21304','21305','23629','23628','23627','21598','21597','23701'],
 'GER_2025':['15625','18566','17240','18578','15627','18577','14835','15586','15628','17386','15619','19013','17241','17239'],
 'AUS_2025':['23633','23634','23683','23335','23336'],
 'POL_2025':['24885'],
 'BOL_2025':['24212','34048','34049','32587'],
 'ECU_2025':['17537'],
 'CHL_2025':['23947','84057','73598','73593','73613','73599','73826','73825','35722'],
 'NOR_2025':['25330','38706','38549','38877','38544'],
 'NLD_2025':['62364','25933','53217','59404','41438'],
 'CZE_2025':['39629','39624','39639','39635','39625','39411','39633','39410','39631','39620','39634','33869'],
 'USA_STATES_2025':['54987','54935','54928','43173','43172','48328','48078','48092'],
 'JPN_2026':['175261','177618','177629','177648','198837','179179','198847','179199','179185','179165','198781','177522','177716','177589','177468','177721'],
 'SVN_2026':['107420','263185'],
 'LBN_2026':['147848'],
}
AMBIGUOUS={
 'country_conflict_2025':['23682']
}

rows=[json.loads(line) for line in PG.open()]
by=collections.defaultdict(list)
for r in rows:
    if r.get('parent_event_id'):
        by[str(r['parent_event_id'])].append(r)
selected_gamma=json.loads(GAMMA.read_text())
base_by={str(x['event'].get('id')):x['research_family'] for x in selected_gamma['events']}

# Additional 2024 US result and race events from the market dictionary search.
# The category search is time-bounded here, then each event title is screened.
US_GEO=re.compile(r'(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|united states|electoral college)',re.I)
US_CORE=re.compile(r'(president|presidency|senate|house|governor|election|electoral|popular.vote|turnout|margin|vote|seat|control|tipping.point|swing.state|third.party|trifecta|balance.of.power|district|inaugurat|called|state.wide|record.turnout)',re.I)
US_EXCLUDE=re.compile(r'(mortgage|rally|speech|say.during|mention|tariff|court|prison|charged|appoint|visit|vp.debate|nominee|primary|caucus|debate.mentions|ballot.measure|abortion|election.interference|hunter.biden|daniel.penny|impeach|resign|endorse|congestion.pricing|unrealized|normalize.relations|mystery.drones|nate.silver|faithless.elector|campaign|georgia.parliament|japanese|saskatchewan|new.brunswick|canada|germany|israel|palestine|saudi.arabia|netherlands)',re.I)
def month(ts):
    if not ts:return ''
    try:return datetime.datetime.fromtimestamp(int(ts),datetime.timezone.utc).strftime('%Y-%m')
    except (ValueError,TypeError,OverflowError):return ''

auto_us=[]
for eid,rs in by.items():
    if eid in base_by: continue
    slug=' '.join(str(r.get('parent_event_slug') or '') for r in rs)
    title=' '.join(str(r.get('question') or '') for r in rs[:8])
    text=slug+' '+title
    dates=[month(r.get('end_date_ts') or r.get('start_date_ts')) for r in rs]
    if not any(d in {'2024-11','2024-12'} for d in dates): continue
    if not US_GEO.search(text) or not US_CORE.search(text) or US_EXCLUDE.search(slug): continue
    auto_us.append(eid)
MANUAL['US_2024']=sorted(auto_us, key=lambda x:(int(x) if x.isdigit() else x))

# Add useful linked families outside the requested list where PG shows several
# seat-rank outcomes for the same national election.
for fam, pattern, lo, hi in [
 ('JPN_2026',r'(japan|japanese)', '2026-01','2026-04'),
 ('SVN_2026',r'(slovenia|slovenian)', '2026-01','2026-06'),
 ('LBN_2026',r'(lebanon|lebanese)', '2026-04','2026-08'),
]:
    rx=re.compile(pattern,re.I)
    for eid,rs in by.items():
        if eid in base_by or eid in MANUAL.get(fam,[]): continue
        slug=' '.join(str(r.get('parent_event_slug') or '') for r in rs)
        questions=' '.join(str(r.get('question') or '') for r in rs[:8])
        event_text=slug+' '+questions
        if not rx.search(event_text): continue
        if not re.search(r'(election|parliament|seats|seat-count|prime-minister|government|coalition)',event_text,re.I): continue
        if re.search(r'(trump-say|which-countries|tariff|warship|endorse|ceasefire|visit-the-middle-east)',slug,re.I): continue
        dates=[month(r.get('end_date_ts') or r.get('start_date_ts')) for r in rs]
        if any(lo<=d<=hi for d in dates): MANUAL.setdefault(fam,[]).append(eid)

# Keep selections deterministic and avoid listing an event twice.
for fam in list(MANUAL):
    MANUAL[fam]=sorted(set(MANUAL[fam]),key=lambda x:(int(x) if str(x).isdigit() else str(x)))
for fam in list(AMBIGUOUS):
    AMBIGUOUS[fam]=sorted(set(AMBIGUOUS[fam]))

selection={
 'source_search':'PostgreSQL public.markets ILIKE search over question, slug, parent_event_slug, and description; indexed political-category slice.',
 'base_gamma_event_count':len(selected_gamma['events']),
 'events_by_family':MANUAL,
 'ambiguous_events':AMBIGUOUS,
 'event_counts':{k:len(v) for k,v in MANUAL.items()},
 'pg_candidate_parent_event_count':len(by),
 'unmatched_manual_ids':{k:[eid for eid in ids if eid not in by] for k,ids in MANUAL.items() if any(eid not in by for eid in ids)},
 'event_preview':{
  k:[{'event_id':eid,'market_rows_in_search':len(by.get(eid,[])),'event_slug':(by.get(eid) or [{}])[0].get('parent_event_slug'),'sample_question':(by.get(eid) or [{}])[0].get('question')} for eid in ids]
  for k,ids in MANUAL.items()
 },
 'ambiguous_preview':{
  k:[{'event_id':eid,'market_rows_in_search':len(by.get(eid,[])),'event_slug':(by.get(eid) or [{}])[0].get('parent_event_slug'),'sample_question':(by.get(eid) or [{}])[0].get('question')} for eid in ids]
  for k,ids in AMBIGUOUS.items()
 }
}
OUT.write_text(json.dumps(selection,ensure_ascii=False,indent=2)+'\n')
for family,ids in MANUAL.items():
    print(family,len(ids),'candidate_markets',sum(len(by.get(eid,[])) for eid in ids))
print('ambiguous',AMBIGUOUS)
print('output',OUT.name)
