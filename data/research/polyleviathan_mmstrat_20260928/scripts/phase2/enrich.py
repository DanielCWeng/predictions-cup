import json
import numpy as np, pandas as pd, polars as pl
from pathlib import Path
R=Path('/home/ubuntu/campaigns/mmstrat_phase2_20260928'); O=R/'outputs/phase2'; F=Path('/home/ubuntu/state_backup_20260902/sisterreq_fees_20260927')
m=pd.read_parquet(O/'per_wallet_question_metrics.parquet')
wallets=m.wallet.unique().tolist()
cols=['family','event_id','condition_id','market_id','token_id','outcome_side','timestamp','participant_address','counterparty_address','order_is_match_taker_order','participant_side','price','size_shares','value_usd','fee_net_usd_equiv']
parts=[]
for fam in ['HUN','COL','PER']:
 p=F/f'fees_{fam}_2026.parquet'
 parts.append(pl.scan_parquet(p).select(cols).filter(pl.col('participant_address').is_in(wallets)))
df=pl.concat(parts).collect(streaming=True).to_pandas();df['wallet']=df.participant_address
# Map resolution times and token payouts.
inv=pd.read_csv('/home/ubuntu/polymarketwhale/campaigns/sisterreq_20260925/polyleviathan_election_market_inventory.csv').drop_duplicates('condition_id')
inv['resolution_ts']=pd.to_datetime(inv.resolution_time,utc=True,errors='coerce').astype('int64')//10**9
res=inv.set_index('condition_id').resolution_ts.to_dict();vol=inv.set_index('condition_id').traded_notional_usd.to_dict()
payout=pl.read_parquet('/home/ubuntu/campaigns/mmstrat_20260928/data/token_payouts.parquet').to_pandas().set_index('token_id').result.to_dict()
def gini(x):
 x=np.abs(np.asarray(x,dtype=float)); x=x[np.isfinite(x)]
 if not len(x) or x.sum()==0:return np.nan
 x=np.sort(x); n=len(x); return float((2*np.sum((np.arange(1,n+1))*x)/(n*x.sum()))-(n+1)/n)
for ix,r in m.iterrows():
 d=df[df.wallet.eq(r.wallet)].copy()
 if r.scope!='POOLED': d=d[d.family.str.startswith(r.scope.split('_')[0])]
 if not len(d):continue
 d['notional']=d.value_usd.fillna(d.price*d.size_shares);d['buy']=d.participant_side.str.upper().eq('BUY');d['sgn']=np.where(d.buy,1,-1);d['flow']=np.where(d.buy,-d.notional,d.notional)-d.fee_net_usd_equiv.fillna(0)
 d['resolution_ts']=d.condition_id.map(res);d['days_to_res']=(d.resolution_ts-d.timestamp)/86400
 dr=d.days_to_res.to_numpy(); masks={'gt30d':dr>=30,'7_30d':(dr>=7)&(dr<30),'1_7d':(dr>=1)&(dr<7),'lt24h':(dr>=0)&(dr<1),'lt1h':(dr>=0)&(dr<1/24)}
 q10={}
 for k,mask in masks.items():
  q10[k]={'fills':int(mask.sum()),'notional_usd':float(d.loc[mask,'notional'].sum()),'volume_share':float(d.loc[mask,'notional'].sum()/max(d.notional.sum(),1e-9)),'net_trade_cashflow_usd':float(d.loc[mask,'flow'].sum())}
 m.at[ix,'resolution_time_fills']=json.dumps(q10)
 md=inv[inv.condition_id.astype(str).isin(d.condition_id.astype(str))]
 m.at[ix,'negrisk_event_count']=int(md.loc[md.negrisk_market_id.notna() & (md.negrisk_market_id.astype(str)!=''),'event_id'].nunique()) if len(md) else 0
 def isbinary(x):
  try: return len(json.loads(x))==2
  except: return str(x).count(',')==1
 m.at[ix,'binary_market_count']=int(md.outcome_labels.map(isbinary).sum()) if len(md) else 0
 m.at[ix,'liquidity_tier']='unavailable: no consistent market depth/volume tier'
 m.at[ix,'closed_before_resolution_share']=float(d.loc[dr>=0,'notional'].sum()/max(d.notional.sum(),1e-9)) if np.isfinite(dr).any() else np.nan
 # Buy and sell-to-open entry distributions by notional; sell-side is an observed sell price, not proof of opening short.
 buckets=[(0,.05,'lt5c'),(.05,.20,'5_20c'),(.20,.40,'20_40c'),(.40,.60,'40_60c'),(.60,.80,'60_80c'),(.80,.95,'80_95c'),(.95,1.000001,'gt95c')]
 bdist={}; sdist={}
 for lo,hi,k in buckets:
  bdist[k]=float(d.loc[d.buy & (d.price>=lo)&(d.price<hi),'notional'].sum()/max(d.loc[d.buy,'notional'].sum(),1e-9))
  sdist[k]=float(d.loc[(~d.buy) & (d.price>=lo)&(d.price<hi),'notional'].sum()/max(d.loc[~d.buy,'notional'].sum(),1e-9))
 m.at[ix,'entry_buy_distribution']=json.dumps(bdist);m.at[ix,'entry_sell_open_distribution']=json.dumps(sdist)
 # Fill-to-peak count and half-life: largest token inventory path and first later 50% drawdown, per token median.
 peak_fill_n=[];half_lives=[]
 for tok,g in d.sort_values('timestamp').groupby('token_id'):
  q=(g.sgn*g.size_shares).cumsum().to_numpy();ts=g.timestamp.to_numpy();
  if not len(q):continue
  imax=int(np.argmax(np.abs(q)));pk=abs(q[imax]);
  if pk>0:
   if tok==d.groupby('token_id').size().index[0]: pass
   peak_fill_n.append(imax+1)
   hit=np.flatnonzero(np.abs(q[imax+1:])<=pk*.5)
   if len(hit): half_lives.append((ts[imax+1+hit[0]]-ts[imax])/3600)
 if peak_fill_n: m.at[ix,'one_shot_fills_to_peak']=int(max(peak_fill_n))
 m.at[ix,'inventory_half_life_hours']=float(np.median(half_lives)) if half_lives else np.nan
 m.at[ix,'hours_active']=len(json.loads(r.hour_profile)) if pd.notna(r.hour_profile) else 0
 m.at[ix,'continuous_24h_activity']=bool(m.at[ix,'hours_active']==24)
 m.at[ix,'first_active_date']=pd.to_datetime(int(r.first_active),unit='s',utc=True).date().isoformat();m.at[ix,'last_active_date']=pd.to_datetime(int(r.last_active),unit='s',utc=True).date().isoformat()
 # Event sibling patterns from observed outcome-side fills.
 buyrows=d[d.buy]
 ev=buyrows.groupby('event_id').agg(conditions=('condition_id','nunique'),outcomes=('outcome_side','nunique'))
 m.at[ix,'buy_sibling_multi_events']=int((ev.conditions>=2).sum()) if len(ev) else 0
 comp=buyrows.groupby(['event_id','condition_id']).outcome_side.agg(lambda x:set(str(z).upper() for z in x.dropna())).reset_index()
 byevent={}
 for er in comp.itertuples(): byevent.setdefault(er.event_id,[]).append(er.outcome_side)
 m.at[ix,'yes_no_complement_events']=sum(1 for values in byevent.values() if len(values)>=2 and any('YES' in x for x in values) and any('NO' in x for x in values))
 # FIFO spread bps over matched entry notional, using a buy-lot queue by token.
 matched_pnl=0.;matched_entry=0.;match_wins=0.;match_n=0;matched_holds=[];matched_sizes=[]
 for tok,g in d.sort_values(['timestamp']).groupby('token_id'):
  lots=[]
  for x in g.itertuples():
   q=float(x.size_shares);p=float(x.price)
   if x.buy: lots.append([q,p,int(x.timestamp)]);continue
   rem=q
   while rem>1e-8 and lots:
    lot=lots[0];take=min(rem,lot[0]);profit=take*(p-lot[1]);matched_pnl+=profit;matched_entry+=take*lot[1];match_wins+=int(profit>0);match_n+=1;matched_sizes.append(take);matched_holds.append(max(0,int(x.timestamp)-lot[2]));rem-=take;lot[0]-=take
    if lot[0]<=1e-8:lots.pop(0)
 m.at[ix,'round_trip_spread_bps']=10000*matched_pnl/max(matched_entry,1e-9) if matched_entry else np.nan
 m.at[ix,'round_trip_profitable_share']=match_wins/max(match_n,1) if match_n else np.nan
 if matched_holds:
  hs=np.asarray(matched_holds);sz=np.asarray(matched_sizes)
  bucket_defs=[('lt1m',0,60),('1m_1h',60,3600),('1h_24h',3600,86400),('1d_7d',86400,604800),('gt7d',604800,np.inf)]
  m.at[ix,'holding_period_distribution']=json.dumps({name:{'matched_lots':int(((hs>=lo)&(hs<hi)).sum()),'matched_shares':float(sz[(hs>=lo)&(hs<hi)].sum())} for name,lo,hi in bucket_defs})
  m.at[ix,'round_trip_size_p10_shares']=float(np.quantile(sz,.1));m.at[ix,'round_trip_size_median_shares']=float(np.quantile(sz,.5));m.at[ix,'round_trip_size_p90_shares']=float(np.quantile(sz,.9))
  hh=hs/3600;m.at[ix,'round_trip_duration_p10_hours']=float(np.quantile(hh,.1));m.at[ix,'round_trip_duration_median_hours']=float(np.quantile(hh,.5));m.at[ix,'round_trip_duration_p90_hours']=float(np.quantile(hh,.9))
 else:
  m.at[ix,'holding_period_distribution']=json.dumps({});m.at[ix,'round_trip_size_p10_shares']=np.nan;m.at[ix,'round_trip_size_median_shares']=np.nan;m.at[ix,'round_trip_size_p90_shares']=np.nan;m.at[ix,'round_trip_duration_p10_hours']=np.nan;m.at[ix,'round_trip_duration_median_hours']=np.nan;m.at[ix,'round_trip_duration_p90_hours']=np.nan
 m.at[ix,'fill_notional_p10_usd']=float(d.notional.quantile(.1));m.at[ix,'fill_notional_median_usd']=float(d.notional.median());m.at[ix,'fill_notional_p90_usd']=float(d.notional.quantile(.9))
 # Market-level fill flow + payout on terminal position (scope-limited).
 d['position_change']=d.sgn*d.size_shares
 # Counterparty estimates use paired wallet-order rows; taker-order rows point to the exchange contract.
 peer=d[~d.order_is_match_taker_order.fillna(False) & d.counterparty_address.notna()]
 cp=peer.groupby('counterparty_address').notional.sum().sort_values(ascending=False)
 cpjson={str(k):float(v) for k,v in cp.head(5).items()}
 m.at[ix,'counterparty_top5']=json.dumps(cpjson)
 m.at[ix,'counterparty_top1_share']=float(cp.head(1).sum()/max(cp.sum(),1e-9)) if len(cp) else np.nan
 m.at[ix,'counterparty_hhi']=float(((cp/cp.sum())**2).sum()) if len(cp) and cp.sum()>0 else np.nan
 tok=d.groupby(['condition_id','token_id']).agg(flow=('flow','sum'),position=('position_change','sum')).reset_index()
 tok['payout']=tok.token_id.map(payout).fillna(0)
 tok['market_pnl']=tok.flow+tok.position*tok.payout
 markets=tok.groupby('condition_id').market_pnl.sum()
 v=np.abs(markets.to_numpy()); total=float(markets.sum());top=markets.sort_values(ascending=False)
 m.at[ix,'top_market_pnl_share']=float(top.head(1).sum()/total) if total>0 else np.nan
 m.at[ix,'top3_market_pnl_share']=float(top.head(3).sum()/total) if total>0 else np.nan
 m.at[ix,'gini_market']=gini(markets.to_numpy());m.at[ix,'gini_trade']=np.nan
 # market volume capacity using supplied inventory market traded notional denominator.
 bymarket=d.groupby('condition_id').notional.sum(); cap=[]
 for cond,n in bymarket.items():
  den=vol.get(cond,np.nan)
  if pd.notna(den) and den>0:cap.append(n/den)
 m.at[ix,'capacity_market_share']=float(max(cap)) if cap else np.nan
 m.at[ix,'capacity_market_share_distribution']=json.dumps({str(k):float(v/vol.get(k,np.nan)) for k,v in bymarket.items() if pd.notna(vol.get(k,np.nan)) and vol.get(k,0)>0})
# Save wide and question-long regenerated with all original Q field selections plus enrichment fields.
m.to_parquet(O/'per_wallet_question_metrics.parquet',index=False)
# update question comparison, which summarizes all numeric metrics grouped by q mapping
qmap={1:['market_count','event_count','negrisk_event_count','binary_market_count'],2:['entry_buy_mean','entry_sell_open_mean','entry_buy_distribution','entry_sell_open_distribution'],3:['hold_p10_hours','hold_median_hours','hold_p90_hours','hold_vw_median_proxy_hours','holding_period_distribution'],4:['strict_round_trips','one_shot_fills_to_peak','time_first_to_peak_hours','fill_notional_p10_usd','fill_notional_median_usd','fill_notional_p90_usd'],5:['flat_returns_10pct','flat_interval_median_hours','inventory_half_life_hours'],6:['round_trip_spread_cents','round_trip_spread_bps','round_trip_duration_p10_hours','round_trip_duration_median_hours','round_trip_duration_p90_hours','round_trip_size_p10_shares','round_trip_size_median_shares','round_trip_size_p90_shares','round_trip_profitable_share'],7:['sibling_multimarket_events','buy_sibling_multi_events','yes_no_complement_events'],9:['closed_before_resolution_share'],11:['fifo_pnl','terminal_pnl','fees','rebates_platform_wide','pnl_residual','pnl_ex_rebate'],12:['top_market_pnl_share','top3_market_pnl_share','gini_market','gini_trade'],14:['reaction_latency_seconds'],15:['counterparty_top1_share','counterparty_hhi'],16:['peak_capital_at_risk','return_on_peak_capital','turnover_over_peak','pnl_per_usd_traded'],17:['max_drawdown_daily_mtm_proxy','daily_pnl_volatility_proxy','sharpe_like_proxy'],20:['first_active_date','last_active_date'],21:['capacity_market_share','capacity_market_share_distribution'],22:['current_fee_counterfactual','pnl_after_current_fee_proxy'],23:['strict_mm','eligible_fills','zero_fee_eligible_share'],24:['external_sell_shares','external_sell_proceeds']}
comp=[]
for q,fields in qmap.items():
 for fld in fields:
  for scope,g in m.groupby('scope'):
   v=pd.to_numeric(g[fld],errors='coerce').dropna().astype(float)
   comp.append(dict(question=q,metric=fld,scope=scope,wallets=len(g),median=float(v.median()) if len(v) else np.nan,p10=float(v.quantile(.1)) if len(v) else np.nan,p90=float(v.quantile(.9)) if len(v) else np.nan))
pd.DataFrame(comp).to_parquet(O/'question_comparisons.parquet',index=False)
# Rebuild long Q metrics from enriched current columns.
from json import dumps
fields={1:['scope','market_count','event_count','negrisk_event_count','binary_market_count','liquidity_tier','market_type_counts'],2:['entry_buy_mean','entry_sell_open_mean','entry_buy_distribution','entry_sell_open_distribution'],3:['hold_p10_hours','hold_median_hours','hold_p90_hours','hold_vw_median_proxy_hours','holding_period_distribution'],4:['strict_round_trips','one_shot_fills_to_peak','time_first_to_peak_hours','fill_notional_p10_usd','fill_notional_median_usd','fill_notional_p90_usd'],5:['flat_returns_10pct','flat_interval_median_hours','inventory_half_life_hours'],6:['round_trip_spread_cents','round_trip_spread_bps','round_trip_duration_p10_hours','round_trip_duration_median_hours','round_trip_duration_p90_hours','round_trip_size_p10_shares','round_trip_size_median_shares','round_trip_size_p90_shares','round_trip_profitable_share'],7:['sibling_multimarket_events','buy_sibling_multi_events','yes_no_complement_events'],8:['archetype'],9:['closed_before_resolution_share','pnl_ex_rebate'],10:['resolution_time_fills'],11:['fifo_pnl','terminal_pnl','fees','rebates_platform_wide','pnl_residual','pnl_ex_rebate'],12:['top_market_pnl_share','top3_market_pnl_share','gini_market','gini_trade'],13:['adverse_markout_proxy'],14:['reaction_latency_seconds'],15:['counterparty_top5','counterparty_top1_share','counterparty_hhi'],16:['peak_capital_at_risk','return_on_peak_capital','turnover_over_peak','pnl_per_usd_traded'],17:['max_drawdown_daily_mtm_proxy','daily_pnl_volatility_proxy','sharpe_like_proxy'],18:['hour_profile','weekday_profile','hours_active','continuous_24h_activity'],19:['counterparty_top5'],20:['first_active_date','last_active_date'],21:['capacity_market_share','capacity_market_share_distribution'],22:['current_fee_counterfactual','pnl_after_current_fee_proxy'],23:['strict_mm','eligible_fills','zero_fee_eligible_share','archetype','pnl_ex_rebate'],24:['external_sell_shares','external_sell_proceeds','split_merge_evidence']}
rows=[]
for r in m.itertuples(index=False):
 for q,fs in fields.items(): rows.append(dict(scope=r.scope,wallet=r.wallet,cohort=r.cohort,question=q,metrics=dumps({f:getattr(r,f) for f in fs},default=str)))
pd.DataFrame(rows).to_parquet(O/'per_wallet_question_long.parquet',index=False)
print('enriched',len(m),'unique wallets',m.wallet.nunique())
