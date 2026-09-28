import json
from pathlib import Path
import pandas as pd
import numpy as np
from PIL import Image, ImageDraw, ImageFont
R=Path('/home/ubuntu/campaigns/mmstrat_phase2_20260928'); O=R/'outputs/phase2'
m=pd.read_parquet(O/'per_wallet_question_metrics.parquet')
def f(v,fmt=',.2f'):
 try:
  if pd.isna(v): return 'n/a'
  return '$'+format(float(v),fmt[1:]) if fmt.startswith('$') else format(float(v),fmt)
 except: return str(v)
def table(rows,headers):
 return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(map(str,row))+' |' for row in rows)
lines=['# Phase 2: strategy anatomy deep-dive','',
'Analysis date: 2026-09-28. PnL rankings use fill cashflow + resolution payout − net fees; family-allocated rebates are removed. Platform-wide wallet rebate payouts are shown separately and are not attributed to these markets. Reconciliation identity: ex-rebate PnL = FIFO component + terminal/resolution component − fees + residual.',
'',
'## Scope and interpretation','',
f"Candidate universe screened: {json.load(open(O/'summary.json'))['candidate_wallets']:,} candidate wallets and {json.load(open(O/'summary.json'))['candidate_fill_rows']:,} fills. Detailed outputs include {m.wallet.nunique()} unique wallets across {len(m)} scope-wallet rows. Cohorts requested were strict MM top 15 pooled + top 3/family, any-type top 20 pooled + top 3/family, and matched losers; when fewer strict passers were found in the candidate scan, all passers are shown.",
'',
'Fee-regime scenario uses the current supplied market fee settings. The current regime table’s R6 is sports-only; the scoped election markets have fees disabled in the current snapshot, so the modeled current fee is zero for those settings. Fee-eligible means fees_enabled at the fill and custody scanned. Zero-fee share is calculated only over fee-eligible fills; it is n/a below 20 eligible fills. Strict MM screen: ≥30 FIFO sell matches, two-sided tokens ≥50%, volume-weighted median FIFO hold ≤72h, ≥5 returns within 10% of running peak inventory, FIFO component ≥50% of FIFO + |terminal PnL|, and fee-eligible zero-fee share ≥80% where applicable. Chain maker/taker role is descriptive only.',
'',
'Market type is inferred from inventory question text and is a heuristic. Market liquidity tier is not inferred without a consistent full market volume/quote-depth series. True quote-mid markouts are unavailable; fill-only marks are labeled as proxies.',
'',
'## Selected roster by strategy archetype','']
funnel_path=O/'strict_tier_funnel.parquet'
if funnel_path.exists():
 funnel=pd.read_parquet(funnel_path)
 lines += ['### Strict-tier funnel across scanned candidates','', 'Counts are cumulative wallet-scope rows in the scanned candidate set (phase 1 ranked anatomy plus candidate PnL selections), not a census of the full 121,639-wallet universe.','']
 for scope,gf in funnel.groupby('scope'):
  lines += [f'**{scope}**','',table([[r.criterion,r.wallet_scope_rows] for r in gf.itertuples()],['Cumulative criterion','Rows remaining']),'']
for arch,g in m.groupby('archetype',dropna=False):
 lines += [f'### {arch}','',table([[r.wallet,r.scope,r.cohort,f(r.pnl_ex_rebate,'$,.0f'),int(r.fills),int(r.market_count),f(r.fifo_pnl,'$,.0f'),f(r.terminal_pnl,'$,.0f'),f(r.hold_vw_median_proxy_hours)] for r in g.sort_values('pnl_ex_rebate',ascending=False).head(12).itertuples()],['Wallet','Scope','Cohort','PnL ex rebates','Fills','Markets','FIFO proxy','Terminal proxy','VW median hold h']),'']
lines += ['## Per-question comparisons','']
titles={1:'Market selection',2:'Entry distribution',3:'Holding period',4:'Position construction',5:'Inventory recycling',6:'Round trips',7:'Cross-market sequences',8:'Directional behavior',9:'Resolution exposure',10:'Regime behavior',11:'PnL attribution',12:'Consistency',13:'Adverse selection and markouts',14:'Reaction latency',15:'Counterparties',16:'Capital and returns',17:'Risk',18:'Timing',19:'Linked wallets',20:'Lifecycle and drift',21:'Capacity',22:'Fee-regime sensitivity',23:'Replicability verdict',24:'Split, merge, conversion usage'}
metrics={1:['market_count','event_count','negrisk_event_count','binary_market_count','market_type_counts','liquidity_tier'],2:['entry_buy_mean','entry_sell_open_mean','entry_buy_distribution','entry_sell_open_distribution'],3:['hold_p10_hours','hold_median_hours','hold_p90_hours','hold_vw_median_proxy_hours','holding_period_distribution'],4:['strict_round_trips','one_shot_fills_to_peak','time_first_to_peak_hours','fill_notional_p10_usd','fill_notional_median_usd','fill_notional_p90_usd'],5:['flat_returns_10pct','flat_interval_median_hours','inventory_half_life_hours'],6:['round_trip_spread_cents','round_trip_spread_bps','round_trip_duration_p10_hours','round_trip_duration_median_hours','round_trip_duration_p90_hours','round_trip_size_p10_shares','round_trip_size_median_shares','round_trip_size_p90_shares','round_trip_profitable_share'],7:['sibling_multimarket_events','buy_sibling_multi_events','yes_no_complement_events'],8:['archetype'],9:['closed_before_resolution_share','pnl_ex_rebate'],10:['resolution_time_fills'],11:['fifo_pnl','terminal_pnl','fees','rebates_platform_wide','pnl_residual','pnl_ex_rebate'],12:['market_count','strict_round_trips','top_market_pnl_share','top3_market_pnl_share','gini_market','gini_trade'],13:['adverse_markout_proxy'],14:['reaction_latency_seconds'],15:['counterparty_top5','counterparty_top1_share','counterparty_hhi'],16:['peak_capital_at_risk','return_on_peak_capital','turnover_over_peak','pnl_per_usd_traded'],17:['max_drawdown_daily_mtm_proxy','daily_pnl_volatility_proxy','sharpe_like_proxy'],18:['hour_profile','weekday_profile','hours_active','continuous_24h_activity'],19:['counterparty_top5'],20:['first_active_date','last_active_date'],21:['capacity_market_share','capacity_market_share_distribution'],22:['current_fee_counterfactual','pnl_after_current_fee_proxy'],23:['strict_mm','eligible_fills','zero_fee_eligible_share','archetype','pnl_ex_rebate'],24:['external_sell_shares','external_sell_proceeds','split_merge_evidence']}
textual={'market_type_counts','holding_period_distribution','entry_distribution','entry_buy_distribution','entry_sell_open_distribution','liquidity_tier','capacity_market_share_distribution','resolution_time_fills','adverse_markout_proxy','counterparty_top5','hour_profile','weekday_profile','archetype','split_merge_evidence','first_active_date','last_active_date'}
for q,title in titles.items():
 lines += [f'### {q}. {title}','']
 fields=metrics[q]; rows=[]
 for scope,g in m.groupby('scope'):
  row=[scope,len(g)]
  for metric in fields:
   if metric in textual:
    vals=[]
    for raw in g[metric].dropna():
     try: vals.append(json.loads(raw))
     except: pass
    if metric=='market_type_counts':
     agg={}
     for obj in vals:
      for k,v in obj.items(): agg[k]=agg.get(k,0)+int(v)
     row.append(json.dumps(agg,sort_keys=True))
    elif metric in ['entry_distribution','entry_buy_distribution','entry_sell_open_distribution']:
     agg={}
     for obj in vals:
      for k,v in obj.items(): agg[k]=agg.get(k,0)+float(v)/max(1,len(vals))
     row.append(json.dumps({k:round(v,3) for k,v in agg.items()},sort_keys=True))
    elif metric=='capacity_market_share_distribution':
     allshares={}
     for obj in vals:
      for k,v in obj.items(): allshares[k]=float(v)
     row.append(json.dumps({'markets':len(allshares),'top3':sorted(allshares.items(),key=lambda kv:-kv[1])[:3]},separators=(',',':')))
    elif metric in ['first_active_date','last_active_date']:
     ds=sorted(str(x) for x in g[metric].dropna()); row.append(f'{ds[len(ds)//2]} (wallet ISO dates in Q20 cells)' if ds else 'n/a')
    elif metric=='resolution_time_fills':
     agg={}
     for obj in vals:
      for k,v in obj.items():
       rec=agg.setdefault(k,{'fills':0,'notional_usd':0.0,'net_trade_cashflow_usd':0.0})
       if isinstance(v,dict):
        for z in rec: rec[z]+=float(v.get(z,0) or 0)
     row.append(json.dumps(agg,sort_keys=True))
    elif metric=='adverse_markout_proxy':
     agg={}
     for obj in vals:
      for k,v in obj.items():
       if v is not None and np.isfinite(float(v)): agg.setdefault(k,[]).append(float(v))
     row.append(json.dumps({k:round(float(np.mean(v)),6) for k,v in agg.items()},sort_keys=True))
    elif metric=='archetype':
     row.append(json.dumps(g.archetype.value_counts().to_dict(),sort_keys=True))
    elif metric in ['hour_profile','weekday_profile']:
     agg={}
     for obj in vals:
      for k,v in obj.items(): agg.setdefault(k,[]).append(float(v))
     row.append(json.dumps({k:round(float(np.mean(v)),3) for k,v in agg.items()},sort_keys=True))
    else: row.append('wallet-level values in parquet')
   else:
    vals=pd.to_numeric(g[metric],errors='coerce').dropna().astype(float)
    row.append(f"{f(vals.median())} (p10 {f(vals.quantile(.1))}, p90 {f(vals.quantile(.9))})" if len(vals) else 'n/a')
  rows.append(row)
 lines += [table(rows,['Scope','Wallets']+[x+' (median; p10–p90)' for x in fields]),'']
 for scope,g in m.groupby('scope'):
  sample=g.sort_values('pnl_ex_rebate',ascending=False).head(3)
  examples='; '.join(f"{r.wallet} ({r.cohort}, PnL ex rebates {r.pnl_ex_rebate:,.0f})" for r in sample.itertuples())
  if examples: lines.append(f"- **{scope} examples:** {examples}.")
 lines.append('')
# Full per-wallet Q1-Q24 response matrix (the backing values also live in the long parquet).
long=pd.read_parquet(O/'per_wallet_question_long.parquet')
long=long.merge(m[['scope','wallet','cohort','archetype']],on=['scope','wallet','cohort'],how='left')
lines += ['## Per-wallet answers for all 24 questions','', 'Each Q cell contains that wallet’s measured values for the numbered question. n/a/null means the fill-only inputs do not identify the requested quantity.','']
for arch,g in long.groupby('archetype',dropna=False):
 rows=[]
 for (scope,wallet,cohort),wg in g.groupby(['scope','wallet','cohort']):
  vals={int(r.question):r.metrics for r in wg.itertuples()}
  cells=[]
  for q in range(1,25):
   try:
    obj=json.loads(vals[q]); cell=json.dumps(obj,separators=(',',':'),default=str)
   except: cell='n/a'
   if q==21 and isinstance(obj,dict) and isinstance(obj.get('capacity_market_share_distribution'),str):
    try:
     sh=json.loads(obj['capacity_market_share_distribution']); obj['capacity_market_share_distribution']={'markets':len(sh),'top3':sorted(sh.items(),key=lambda kv:-kv[1])[:3]}; cell=json.dumps(obj,separators=(',',':'),default=str)
    except: pass
   if len(cell)>1500: cell=json.dumps({'detail':'full values in per_wallet_question_long.parquet','fields':list(obj.keys())},separators=(',',':'))
   cells.append(cell)
  rows.append([wallet,scope,cohort]+cells)
 lines += [f'### {arch}','',table(rows,['Wallet','Scope','Cohort']+[f'Q{i}' for i in range(1,25)]),'']
lines += ['## Interpretation','',
'FIFO buy-to-sell PnL is a recycling proxy and can include directional price movement. A FIFO match does not prove passive quoting. Terminal-led PnL is resolution/directional exposure. Chain role shares do not identify liquidity provision. The strict tier is a behavioral screen, not proof of posted quotes.',
'',
'## Charts','',
'Charts cover selected wallets and are descriptive, not population estimates. Entry shares use executed fill notional; holding durations are FIFO matched; PnL components exclude allocated rebates.','']
po=m[m.scope.eq('POOLED')]
def draw_bar(path,title,labels,values):
 W,H=1000,520; im=Image.new('RGB',(W,H),'white'); d=ImageDraw.Draw(im)
 try: font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',15); bold=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',22)
 except: font=bold=ImageFont.load_default()
 d.text((40,20),title,fill='black',font=bold); left,right,top,bottom=70,970,80,440
 d.line((left,bottom,right,bottom),fill='black',width=2)
 if len(values):
  lo=min(0,min(values)); hi=max(1e-8,max(values)); span=hi-lo or 1
  bw=(right-left)/max(1,len(values))
  for i,(lab,val) in enumerate(zip(labels,values)):
   x0=left+i*bw+bw*.15; x1=left+(i+1)*bw-bw*.15
   y=bottom-(val-lo)/span*(bottom-top)
   zero=bottom-(0-lo)/span*(bottom-top)
   d.rectangle((x0,min(y,zero),x1,max(y,zero)),fill=(54,104,170))
   d.text((x0,bottom+8),str(lab)[:14],fill='black',font=font)
 im.save(path)
labels=['<5c','5-20c','20-40c','40-60c','60-80c','80-95c','>95c']; vals=np.zeros(7)
for x in po.entry_distribution.dropna():
 try: vals+=np.array(list(json.loads(x).values()),float)
 except: pass
if vals.sum(): vals/=vals.sum()
draw_bar(O/'entry_price_distribution.png','Entry prices, pooled selected wallets',labels,vals.tolist())
h=po.hold_median_hours.dropna().clip(upper=1000).to_numpy(); hist,edges=np.histogram(h,bins=20) if len(h) else (np.zeros(20),np.arange(21))
draw_bar(O/'holding_time.png','FIFO holding-time medians (hours, capped 1,000)',[str(int(x)) for x in edges[:-1]],hist.tolist())
draw_bar(O/'pnl_attribution.png','Selected pooled wallet FIFO and terminal PnL',list(range(len(po))),po.fifo_pnl.fillna(0).tolist())
lines += ['![Entry price distribution](outputs/phase2/entry_price_distribution.png)','',
'![FIFO holding time](outputs/phase2/holding_time.png)','',
'![PnL attribution](outputs/phase2/pnl_attribution.png)','',
'## Replicability verdicts','']
for arch,g in m.groupby('archetype'):
 strict_examples=g[g.cohort.eq('strict_mm_top')]
 r=(strict_examples if len(strict_examples) else g).sort_values('pnl_ex_rebate',ascending=False).iloc[0]
 lines.append(f"- **{arch}:** example {r.wallet} ({r.scope}) earned {r.pnl_ex_rebate:,.0f} USD ex rebates on {r.notional:,.0f} USD turnover; peak capital proxy {r.peak_capital_at_risk:,.0f} USD; FIFO {r.fifo_pnl:,.0f} vs terminal {r.terminal_pnl:,.0f} USD; VW median FIFO hold {f(r.hold_vw_median_proxy_hours)} hours. Recycling needs inventory capital and fast repricing; terminal-led returns need outcome selection/information and tolerance for resolution risk. We cannot infer queue speed from fills.")
lines += ['','## What I did NOT do','',
'- No RPC, external funding graph, or database access. The scoped per-fill corpus cannot identify standalone mint, merge, or conversion flows; outside-basis sales are reported as negative-inventory evidence.',
'- No historical order book is supplied. The markouts use next-wallet-fill price as a proxy, not the true mid. Reaction-latency, own-fill impact, historical depth/liquidity tiers, and posted queue position cannot be established.',
'- Counterparties are observed peer-order addresses, but the data does not classify retail vs professional wallets or prove common ownership. Same-market timing and mirrored-size clusters were not used to assert linked entities.',
'- Result-night/count-release labels are absent as a structured calendar. Time-to-resolution tables show fill counts, notional and signed trade cashflow; they do not allocate resolution PnL to a release night.',
'- Strict-tier ranking covers the scanned candidate set (phase 1 ranked anatomy plus top 80 ex-rebate winners and bottom 200 wallets per scope), not every wallet in the 121,639-wallet pooled universe.',
'- Trade-level Gini is null. Market Gini and top-market PnL shares use fill cashflow plus mapped terminal payouts; top shares may exceed 100% when losing markets offset profitable ones. Resolution share is pre-resolution fill notional, not a complete reconstruction of position closure.',
'- Capacity uses wallet notional divided by supplied inventory market notional. It does not estimate capacity decay or own-fill impact. The current-fee scenario applies the current snapshot; it is not a counterfactual using a universal taker charge.',
'',
'Full per-wallet fields and comparison metrics are in outputs/phase2/*.parquet.']
(R/'REPORT_PHASE2.md').write_text('\n'.join(lines)+'\n')
print('wrote report',len(m),'wallet-scope rows')
