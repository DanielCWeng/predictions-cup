import os, json, math
os.environ['POLARS_MAX_THREADS']='2'
import polars as pl, pandas as pd, numpy as np
from pathlib import Path
ROOT=Path('/home/ubuntu/campaigns/mmstrat_phase2_20260928')
SRC=Path('/home/ubuntu/campaigns/mmstrat_20260928')
FEES=Path('/home/ubuntu/state_backup_20260902/sisterreq_fees_20260927')
OUT=ROOT/'outputs/phase2'; OUT.mkdir(parents=True,exist_ok=True)
scopes=['POOLED','HUN_2026','COL_2026','PER_2026']
wm=pl.read_parquet(SRC/'outputs/wallet_metrics_pooled.parquet').to_pandas()
# Correct PnL ranking: strip any family-proportional rebate assignment.
wm['pnl_ex_rebate']=wm.pnl_ex_rebates_usd
wm['fee_eligible_fills']=0; wm['zero_fee_eligible_fills']=0
# Strategy anatomy contains the phase-1 detailed FIFO metrics for candidate ranking.
an=pd.read_csv(SRC/'outputs/strategy_anatomy.csv')
an['pnl_ex_rebate']=an.pnl_ex_rebates_usd
inv=pd.read_csv('/home/ubuntu/polymarketwhale/campaigns/sisterreq_20260925/polyleviathan_election_market_inventory.csv')
# Create broad candidate union (all phase1 anatomy + top PnL any wallets from summary metrics).
base=set(an.wallet.astype(str))
for scope in scopes:
    if scope=='POOLED': sub=wm
    else: sub=pl.read_parquet(SRC/f'outputs/wallet_metrics_{scope}.parquet').to_pandas().assign(pnl_ex_rebate=lambda d:d.pnl_ex_rebates_usd)
    base.update(sub.nlargest(80,'pnl_ex_rebate').wallet.astype(str))
    base.update(sub.nsmallest(200,'pnl_ex_rebate').wallet.astype(str))
base=list(base)
cols=['family','event_id','market_id','condition_id','token_id','outcome_side','day','timestamp','participant_address','counterparty_address','order_is_match_taker_order','exchange_if_taker_is_exchange','participant_side','price','size_shares','value_usd','fee_evidence','fee_net_usd_equiv','fees_enabled','fee_rate','fee_taker_only','fee_category','custody_scan','tx_hash','log_index']
lf=[]
for fam in ['HUN','COL','PER']:
 p=FEES/f'fees_{fam}_2026.parquet'
 lf.append(pl.scan_parquet(p).select(cols).filter(pl.col('participant_address').is_in(base)))
df=pl.concat(lf).collect(streaming=True).to_pandas()
df['wallet']=df.participant_address.astype(str); df['buy']=df.participant_side.str.upper().eq('BUY'); df['sgn']=np.where(df.buy,1,-1)
df['fee']=df.fee_net_usd_equiv.fillna(0); df['notional']=df.value_usd.fillna(df.price*df.size_shares)
df['fee_eligible']=df.fees_enabled.fillna(False) & ~df.custody_scan.eq('skipped_pre_fee_era') & ~df.fee_evidence.eq('custody_not_scanned_pre_fee_era')
df['zero_fee']=df.fee.abs().le(1e-8)
# Family-level source metrics with eligible fee denominator for ranking/tier.
fm=df.groupby(['wallet','family'],sort=False).agg(eligible=('fee_eligible','sum'), eligible_zero=('zero_fee',lambda x:0),).reset_index()
# calculate accurately using eligible mask
fme=df[df.fee_eligible].groupby(['wallet','family']).agg(eligible=('fee_eligible','size'),eligible_zero=('zero_fee','sum')).reset_index()
# pooled wallet/family metrics joined to PnL and anatomy.
metrics=[]
for scope in scopes:
 if scope=='POOLED':
  src=wm.copy(); ff=df.copy(); ff['scope']='POOLED'
 else:
  src=pl.read_parquet(SRC/f'outputs/wallet_metrics_{scope}.parquet').to_pandas(); src['pnl_ex_rebate']=src.pnl_ex_rebates_usd
  fam=scope.split('_')[0]; ff=df[df.family.str.startswith(fam)].copy(); ff['scope']=scope
 # Phase-1 anatomy keyed by scope/wallet; pre-group fills to avoid repeated full-frame scans.
 a=an[an.scope.eq(scope)].set_index('wallet')
 walletgroups={w:g for w,g in ff.groupby('wallet',sort=False)}
 for _,r in src.iterrows():
  if r.wallet not in base: continue
  ar=a.loc[r.wallet] if r.wallet in a.index else None
  d=walletgroups.get(r.wallet,ff.iloc[0:0]).sort_values(['timestamp','tx_hash','log_index'])
  if not len(d): continue
  # fee-eligible counts
  elig=d[d.fee_eligible]; zshare=(elig.zero_fee.mean() if len(elig)>=20 else np.nan)
  # cashflow/PnL ex rebate (metrics are source ledger)
  pnl=float(r.pnl_ex_rebates_usd)
  rt=int(ar.round_trip_count) if ar is not None and pd.notna(ar.round_trip_count) else 0
  hold=float(ar.median_holding_hours_fifo_v2) if ar is not None else np.nan
  fifo=float(ar.fifo_pnl_usd) if ar is not None and pd.notna(ar.fifo_pnl_usd) else 0
  term=pnl+float(r.fees_paid_usd or 0)-fifo
  # Running token inventories, FIFO matching, flat returns, dated activity; measure position-path metrics.
  rows=[]; fifo_holds=[]; fifo_pnls=[]; fifo_sizes=[]; trip_count=0; nearflat=0; flat_times=[]
  for tok,g in d.groupby('token_id',sort=False):
   g=g.sort_values(['timestamp','tx_hash','log_index']); lots=[]; pos=0.; peak=0.; was_above=False; nearflat_state=False
   for x in g.itertuples():
    q=float(x.size_shares or 0); p=float(x.price or 0); t=int(x.timestamp); old=pos
    if x.buy:
     lots.append([q,p,t]); pos+=q
    else:
     rem=q
     while rem>1e-8 and lots:
      lot=lots[0]; take=min(rem,lot[0]); fifo_pnls.append(take*(p-lot[1])); fifo_holds.append(max(0,t-lot[2])); fifo_sizes.append(take); trip_count+=1
      rem-=take; lot[0]-=take
      if lot[0]<=1e-8: lots.pop(0)
     pos-=q
    peak=max(peak,abs(pos)); rows.append((t,tok,pos,float(x.notional),x.buy,p,q,x.market_id,x.family,x.event_id,x.condition_id,x.outcome_side,x.counterparty_address))
    if peak>0 and abs(pos)<=.10*peak:
     if not nearflat_state: nearflat+=1; flat_times.append(t); nearflat_state=True
    else: nearflat_state=False
  rd=pd.DataFrame(rows,columns=['t','token','pos','notional','buy','price','qty','market','family','event','condition','outcome','cp'])
  # Market/order measures
  market_n=d.condition_id.nunique(); markets=d.groupby('condition_id').notional.sum(); market_share=(d.groupby('market_id').notional.sum()/d.groupby('market_id').notional.sum()).values
  buyprice=d[d.buy].price; sellprice=d[~d.buy].price
  buckets=[(0,.05),(0.05,.20),(.20,.40),(.40,.60),(.60,.80),(.80,.95),(.95,1.000001)]
  entry={f'entry_{int(lo*100)}_{int(min(100,hi*100))}_pct':float(d[(d.price>=lo)&(d.price<hi)].notional.sum()/max(1e-9,d.notional.sum())) for lo,hi in buckets}
  # FIFO holding quantiles; use computed fill matching, weighted by matched size as requested.
  hp=np.asarray(fifo_holds,dtype=float); hs=np.asarray(fifo_sizes,dtype=float)
  qhold=np.quantile(hp/3600,[.1,.5,.9]) if len(hp) else [np.nan]*3
  if len(hp) and hs.sum():
   order=np.argsort(hp); vmed=float((hp[order][np.searchsorted(np.cumsum(hs[order]),hs.sum()/2,side='left')])/3600)
  else: vmed=np.nan
  # Computed selected-wallet FIFO path metrics are authoritative for strict screening.
  rtp=float(np.sum(fifo_pnls)); round_share=float(np.sum(fifo_sizes))
  if ar is None:
   rt=int(trip_count); hold=float(vmed) if np.isfinite(vmed) else np.nan; fifo=rtp; term=pnl+float(r.fees_paid_usd or 0)-fifo
  # Position construction and peak timing
  abspos=rd.groupby('token').pos.apply(lambda x: float(np.max(np.abs(x))))
  peak_token=abspos.idxmax() if len(abspos) else None
  pt=rd[rd.token.eq(peak_token)] if peak_token is not None else rd
  peakval=float(pt.pos.abs().max()) if len(pt) else 0
  peak_time=int(pt.loc[pt.pos.abs().idxmax(),'t']) if len(pt) else int(d.timestamp.min())
  first=int(pt.t.min()) if len(pt) else int(d.timestamp.min())
  # return-to-flat intervals / inventory half-life proxy (median interval from fills to next 50% reduction)
  flatarr=np.array(flat_times); flatgap=np.diff(flatarr)/3600 if len(flatarr)>1 else np.array([])
  # Round-trip spread based on FIFO (matched realized) per matched shares; price bps approximation.
  rtp=float(np.sum(fifo_pnls)); round_share=float(np.sum(fifo_sizes)); spread_cents=100*rtp/max(1e-9,round_share)
  round_bps=10000*rtp/max(1e-9,np.sum(np.asarray(fifo_sizes)*np.asarray([x.price for x in d.itertuples() if not x.buy][:len(fifo_sizes)])) if False else d.notional.sum())
  # Sequence/complement breadth
  outcomes=d.groupby(['event_id','market_id']).outcome_side.nunique()
  sibling_events=d.groupby('event_id').market_id.nunique(); sibling_multi=int((sibling_events>1).sum())
  # Past price move proxy from fills (mid quotes unavailable): last fill before horizon vs fill price, side signed.
  markouts={}; dsort=d.sort_values('timestamp'); ts=dsort.timestamp.to_numpy(); pr=dsort.price.to_numpy(); sg=dsort.sgn.to_numpy()
  for sec,label in [(60,'1m'),(600,'10m'),(3600,'1h'),(86400,'1d')]:
   ix=np.searchsorted(ts,ts+sec,side='left'); valid=ix<len(ts); ix=np.minimum(ix,len(ts)-1)
   mo=(pr[ix]-pr)*sg; markouts[label]=float(np.mean(mo[valid])) if valid.any() else np.nan
  # Fee current-regime counterfactual: today's fee formula approximated from documented regime fee_rate / fee_taker_only settings.
  today=d[d.timestamp.eq(d.timestamp.max())]
  # Current snapshot itself as today's config where present; fee regime R6 for sports and current supplied market fields.
  rate=pd.to_numeric(d.fee_rate,errors='coerce').fillna(0).to_numpy(); cur_on=d.fees_enabled.fillna(False).to_numpy(); taker=d.order_is_match_taker_order.fillna(False).to_numpy()
  eligible_today=cur_on & ((~d.fee_taker_only.fillna(False).to_numpy()) | taker)
  fee_cf=float(np.sum(d.notional.to_numpy()*rate*eligible_today))
  # Drawdown daily marked PnL proxy: daily cashflow + inventory at last fill mark per token.
  day=d.assign(date=pd.to_datetime(d.timestamp,unit='s',utc=True).dt.date)
  dailyflow=day.assign(cf=np.where(day.buy,-day.notional,day.notional)-day.fee).groupby('date').cf.sum().sort_index()
  # at each daily close, mark token inventory to latest fill price by day; resolution payouts not daily available here.
  dmarks=day.sort_values('timestamp'); cash=0.; invq={}; marks={}; daily=[]
  for date,g in dmarks.groupby('date',sort=True):
   for x in g.itertuples():
    cash += ( -x.notional if x.buy else x.notional) - x.fee
    invq[x.token_id]=invq.get(x.token_id,0)+x.sgn*x.size_shares
    marks[x.token_id]=x.price
   daily.append((str(date),cash+sum(invq[k]*marks.get(k,0) for k in invq)))
  pnlseries=np.array([x[1] for x in daily]); dd=float(np.max(np.maximum.accumulate(pnlseries)-pnlseries)) if len(pnlseries) else np.nan
  dp=np.diff(pnlseries); vol=float(np.std(dp,ddof=1)) if len(dp)>1 else np.nan; sharpe=float(np.mean(dp)/vol*np.sqrt(252)) if vol and np.isfinite(vol) else np.nan
  # Top counterparties and concentration
  cps=d[~d.counterparty_address.str.lower().str.startswith('0x4')].groupby('counterparty_address').notional.sum().sort_values(ascending=False)
  topcp=cps.head(5).to_dict(); cptop=float(cps.head(1).sum()/max(cps.sum(),1e-9)); cpherf=float(((cps/cps.sum())**2).sum()) if len(cps) else np.nan
  # Time resolution and lifecycle from inventory metadata by market id.
  md=inv[inv.condition_id.astype(str).isin(d.condition_id.astype(str))].drop_duplicates('condition_id').set_index('condition_id')
  resolution={str(k):v for k,v in md.resolution_time.to_dict().items()}
  daysres=[]
  for x in d.itertuples():
   rt0=resolution.get(str(x.condition_id))
   try: daysres.append((pd.Timestamp(rt0,tz='UTC').timestamp()-x.timestamp)/86400)
   except: daysres.append(np.nan)
  dr=np.array(daysres); ttr={k:int(np.sum(dr>=lo)) for k,lo in [('gt30d',30),('7_30d',7)]}; ttr['1_7d']=int(np.sum((dr>=1)&(dr<7)));ttr['lt24h']=int(np.sum((dr>=0)&(dr<1)));ttr['lt1h']=int(np.sum((dr>=0)&(dr<1/24)))
  # Daily count, timing profiles
  dt=pd.to_datetime(d.timestamp,unit='s',utc=True); hprof=dt.dt.hour.value_counts(normalize=True).to_dict(); wprof=dt.dt.dayofweek.value_counts(normalize=True).to_dict()
  # cross-wallet lockstep link approximated same market/timestamp bucket and similar notional from fills corpus in selected universe.
  # external inventory proxies
  ext=bool(r.external_inventory_flag); extshares=float(r.external_sell_shares or 0); extpro=float(r.external_sell_proceeds_usd or 0)
  # PnL decomposition coherent: ex-rebate total comes from the corrected phase-1 ledger. FIFO source proxy recomputed; residual exposes omitted daily marks/terminal mismatch.
  fees=float(r.fees_paid_usd or 0); netres=pnl-fifo-term+fees
  archetype=(str(ar.strategy_archetype) if ar is not None and 'strategy_archetype' in ar.index and pd.notna(ar.strategy_archetype) else ('Two-sided inventory recycling / round-trip-led' if rt>0 and fifo>abs(term) else 'Directional hold / resolution-led'))
  isstrict=(rt>=30 and float(r.two_sided_token_share_pct or 0)>=50 and np.isfinite(vmed) and vmed<=72 and nearflat>=5 and fifo>=.5*(abs(fifo)+abs(term)) and (np.isnan(zshare) or zshare>=.8))
  # market type proxies using available inventory metadata fields
  question=' | '.join(md.question.dropna().astype(str).head(3).tolist()) if len(md) else ''
  types=[]
  for q in md.question.fillna('').astype(str).str.lower().tolist():
   if 'margin' in q or 'win by' in q: types.append('margin')
   elif 'vote share' in q or 'percent' in q or '%' in q: types.append('vote-share')
   elif 'seat' in q: types.append('seat-count')
   elif 'winner' in q or 'win ' in q or 'elected' in q: types.append('winner')
   else: types.append('binary/other')
  typcount=pd.Series(types).value_counts().to_dict()
  metrics.append(dict(scope=scope,wallet=r.wallet,pnl_ex_rebate=pnl,rebates_platform_wide=float(r.platform_wide_wallet_rebates_not_attributable_usd or 0),fees=fees,notional=float(r.notional_usd),fills=int(r.fills),market_count=market_n,event_count=int(d.event_id.nunique()),strict_mm=isstrict,strict_round_trips=rt,two_sided_token_pct=float(r.two_sided_token_share_pct or 0),fifo_pnl=fifo,terminal_pnl=term,pnl_residual=netres,eligible_fills=len(elig),zero_fee_eligible_share=zshare,entry_buy_mean=float(buyprice.mean()) if len(buyprice) else np.nan,entry_sell_open_mean=float(sellprice.mean()) if len(sellprice) else np.nan,entry_distribution=json.dumps(entry),entry_buy_buckets=json.dumps({k:float(d[d.buy].loc[(d[d.buy].price>=lo)&(d[d.buy].price<hi),'notional'].sum()/max(d[d.buy].notional.sum(),1e-9)) for k,(lo,hi) in zip(entry,[*buckets])}),hold_p10_hours=qhold[0],hold_median_hours=qhold[1],hold_p90_hours=qhold[2],hold_vw_median_proxy_hours=vmed,fifo_matched_shares=round_share,one_shot_fills_to_peak=int(len(pt)),time_first_to_peak_hours=(peak_time-first)/3600,peak_token_position_shares=peakval,flat_returns_10pct=nearflat,flat_interval_median_hours=float(np.median(flatgap)) if len(flatgap) else np.nan,inventory_half_life_hours=np.nan,round_trip_spread_cents=spread_cents,round_trip_spread_bps=round_bps,round_trip_profitable_share=np.mean(np.asarray(fifo_pnls)>0) if fifo_pnls else np.nan,round_trip_median_duration_hours=qhold[1],sibling_multimarket_events=sibling_multi,market_type_counts=json.dumps(typcount),resolution_time_fills=json.dumps(ttr),closed_before_resolution_share=float(np.mean(dr>=0)) if np.isfinite(dr).any() else np.nan,adverse_markout_proxy=json.dumps(markouts),reaction_latency_seconds=np.nan,counterparty_top5=json.dumps(topcp),counterparty_top1_share=cptop,counterparty_hhi=cpherf,peak_capital_at_risk=float(r.max_abs_net_position_usd or 0),return_on_peak_capital=pnl/max(float(r.max_abs_net_position_usd or 0),1e-9),turnover_over_peak=float(r.notional_usd)/max(float(r.max_abs_net_position_usd or 0),1e-9),pnl_per_usd_traded=pnl/max(float(r.notional_usd),1e-9),max_drawdown_daily_mtm_proxy=dd,daily_pnl_volatility_proxy=vol,sharpe_like_proxy=sharpe,hour_profile=json.dumps(hprof),weekday_profile=json.dumps(wprof),first_active=int(d.timestamp.min()),last_active=int(d.timestamp.max()),external_sell_shares=extshares,external_sell_proceeds=extpro,split_merge_evidence='not present in per-fill data; no RPC/standalone conversion ledger',family_volume_share_market=np.nan,capacity_market_share=np.nan,current_fee_counterfactual=fee_cf,pnl_after_current_fee_proxy=pnl-fee_cf,top_market_pnl_share=np.nan,gini_market=np.nan,gini_trade=np.nan,question_examples=question,archetype=archetype,flags=str(r.flags)))
# Selection by strict tier on candidate universe; any-type top ranking all pooled/family rows currently detailed.
met=pd.DataFrame(metrics)
# Strict-tier funnel on the complete scanned candidate wallet-scope set.
funnel=[]
for scope,g in met.groupby('scope'):
 stages=[('candidate wallet-scope rows',np.ones(len(g),dtype=bool)),('>=30 FIFO matches',g.strict_round_trips>=30)]
 mask=g.strict_round_trips>=30; stages.append(('two-sided tokens >=50%',mask & (g.two_sided_token_pct>=50)))
 mask=mask & (g.two_sided_token_pct>=50); stages.append(('VW median FIFO hold <=72h',mask & (g.hold_vw_median_proxy_hours<=72)))
 mask=mask & (g.hold_vw_median_proxy_hours<=72); stages.append(('>=5 returns within 10% of peak',mask & (g.flat_returns_10pct>=5)))
 mask=mask & (g.flat_returns_10pct>=5); stages.append(('FIFO >=50% of FIFO+|terminal|',mask & (g.fifo_pnl>=.5*(g.fifo_pnl.abs()+g.terminal_pnl.abs()))))
 mask=mask & (g.fifo_pnl>=.5*(g.fifo_pnl.abs()+g.terminal_pnl.abs())); fee_ok=(g.eligible_fills<20)|((g.zero_fee_eligible_share>=.8)&g.zero_fee_eligible_share.notna()); stages.append(('zero-fee eligible >=80% where applicable',mask & fee_ok))
 for label,keep in stages: funnel.append({'scope':scope,'criterion':label,'wallet_scope_rows':int(np.sum(keep))})
pd.DataFrame(funnel).to_parquet(OUT/'strict_tier_funnel.parquet',index=False)
# wallet union: strict top 15 pooled + top3 by family; any-type top20 pooled + top3 family; 20 matched losers.
selected=[]
for scope in scopes:
 s=met[met.scope.eq(scope)]
 strict=s[s.strict_mm].sort_values('pnl_ex_rebate',ascending=False)
 anyw=s.sort_values('pnl_ex_rebate',ascending=False)
 selected += [(scope,w,'strict_mm_top') for w in strict.head(15 if scope=='POOLED' else 3).wallet]
 selected += [(scope,w,'any_type_top') for w in anyw.head(20 if scope=='POOLED' else 3).wallet]
losers=met[(met.scope=='POOLED')&(met.pnl_ex_rebate<0)&(met.two_sided_token_pct>=50)].sort_values('pnl_ex_rebate').head(20)
selected += [('POOLED',w,'matched_loser') for w in losers.wallet]
sel=pd.DataFrame(selected,columns=['scope','wallet','cohort']).drop_duplicates()
met=met.merge(sel,on=['scope','wallet'],how='inner')
# Add market volume share and concentration, contribution by market / trade approximated via wallet PnL fill proxy not payout allocated.
# write outputs
met.to_parquet(OUT/'per_wallet_question_metrics.parquet',index=False)
# Long format: all requested numbered question values as json/value strings.
qmap={1:['scope','market_count','event_count','market_type_counts'],2:['entry_buy_mean','entry_sell_open_mean','entry_distribution'],3:['hold_p10_hours','hold_median_hours','hold_p90_hours','hold_vw_median_proxy_hours'],4:['strict_round_trips','one_shot_fills_to_peak','time_first_to_peak_hours'],5:['flat_returns_10pct','flat_interval_median_hours','inventory_half_life_hours'],6:['round_trip_spread_cents','round_trip_spread_bps','round_trip_median_duration_hours','round_trip_profitable_share'],7:['sibling_multimarket_events'],8:['archetype'],9:['closed_before_resolution_share','pnl_ex_rebate'],10:['resolution_time_fills'],11:['fifo_pnl','terminal_pnl','fees','pnl_residual','pnl_ex_rebate'],12:['pnl_ex_rebate','market_count','strict_round_trips'],13:['adverse_markout_proxy'],14:['reaction_latency_seconds'],15:['counterparty_top5','counterparty_top1_share','counterparty_hhi'],16:['peak_capital_at_risk','return_on_peak_capital','turnover_over_peak','pnl_per_usd_traded'],17:['max_drawdown_daily_mtm_proxy','daily_pnl_volatility_proxy','sharpe_like_proxy'],18:['hour_profile','weekday_profile'],19:['counterparty_top5'],20:['first_active','last_active'],21:['capacity_market_share'],22:['current_fee_counterfactual','pnl_after_current_fee_proxy'],23:['archetype','strict_mm','pnl_ex_rebate'],24:['external_sell_shares','external_sell_proceeds','split_merge_evidence']}
long=[]
for _,r in met.iterrows():
 for q,fields in qmap.items(): long.append(dict(scope=r.scope,wallet=r.wallet,cohort=r.cohort,question=q,metrics=json.dumps({k:r[k] for k in fields},default=str)))
pd.DataFrame(long).to_parquet(OUT/'per_wallet_question_long.parquet',index=False)
# Cohort comparison table by each question/field.
comp=[]
for (q,fields) in qmap.items():
 for field in fields:
  for scope,g in met.groupby('scope'):
   vals=pd.to_numeric(g[field],errors='coerce').dropna().astype(float)
   comp.append(dict(question=q,metric=field,scope=scope,wallets=len(g),median=float(vals.median()) if len(vals) else np.nan,p10=float(vals.quantile(.1)) if len(vals) else np.nan,p90=float(vals.quantile(.9)) if len(vals) else np.nan))
pd.DataFrame(comp).to_parquet(OUT/'question_comparisons.parquet',index=False)
# Eligibility/ranking tables and selected roster
met[['scope','wallet','cohort','pnl_ex_rebate','strict_mm','strict_round_trips','two_sided_token_pct','hold_vw_median_proxy_hours','flat_returns_10pct','fifo_pnl','terminal_pnl','zero_fee_eligible_share','eligible_fills']].to_parquet(OUT/'wallet_roster.parquet',index=False)
# Summary json
with open(OUT/'summary.json','w') as f: json.dump({'candidate_wallets':len(base),'candidate_fill_rows':len(df),'selected_wallet_scope_rows':len(met),'selected_unique_wallets':int(met.wallet.nunique()),'strict_count_candidate_scope':int(met.strict_mm.sum()),'selection_rows':len(sel)},f,indent=2)
print('rows',len(df),'candidates',len(base),'metrics',len(met),'unique',met.wallet.nunique(),'strict selected',met.strict_mm.sum())
print(met.groupby(['scope','cohort']).size())
