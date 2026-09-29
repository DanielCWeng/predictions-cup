# ruff: noqa
from __future__ import annotations

import hashlib, json, math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import duckdb
import pandas as pd

OUT=Path("/kaggle/working/pred006_phase0")
OUT.mkdir(parents=True, exist_ok=True)

def q(p:Path)->str:
    return str(p).replace("'","''")

def sha256(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""): h.update(chunk)
    return h.hexdigest()

def locate(name:str, fragment:str)->Path:
    m=[p for p in Path("/kaggle/input").rglob(name) if fragment in str(p)]
    if len(m)!=1: raise RuntimeError(f"expected one {name} under {fragment}, got {m}")
    return m[0]

def next_midnight(ts:int)->int:
    d=datetime.fromtimestamp(int(ts),tz=timezone.utc)
    n=(d.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1))
    return int(n.timestamp())

def pct_row(con, view:str, frac:float)->tuple[int,int,int]:
    n=int(con.execute(f"select count(*) from {view}").fetchone()[0])
    if n<100: raise RuntimeError("insufficient chronology")
    idx=max(1,min(n,int(math.floor(frac*n))))
    r=con.execute(f"""
      select timestamp, block_number, log_index
      from {view}
      order by block_number, log_index
      limit 1 offset {idx-1}
    """).fetchone()
    return int(r[0]),int(r[1]),int(r[2])

def main():
    econ=locate("economic_fills_DATA003.parquet","005b-data003-block-gate")
    txb=locate("tx_block_DATA003.parquet","005b-data003-block-gate")
    bts=locate("block_timestamp.parquet","005b-data003-block-gate")
    gate=locate("data003_block_gate_report.json","005b-data003-block-gate")
    gatej=json.loads(gate.read_text())
    if gatej.get("all_hard_gates_pass") is not True:
        raise RuntimeError("accepted DATA-003 block gate did not pass")
    if gatej.get("predictive_outcomes_accessed") is not False:
        raise RuntimeError("upstream block gate accessed predictive outcomes")
    con=duckdb.connect()
    con.execute("pragma threads=4")
    con.execute(f"""
      create temp view chron as
      select e.*, t.block_number, b.block_timestamp
      from read_parquet('{q(econ)}') e
      join read_parquet('{q(txb)}') t on lower(cast(e.tx_hash as varchar))=t.tx_hash
      join read_parquet('{q(bts)}') b using(block_number)
    """)
    audit=con.execute("""
      select count(*) as n_rows,
             count(distinct condition_id) conditions,
             count(distinct sig_market_id) sig_markets,
             count(distinct block_number) blocks,
             min(timestamp) min_ts, max(timestamp) max_ts,
             count(*) filter(where block_number is null) missing_blocks,
             count(*) filter(where block_timestamp is null) missing_block_ts,
             count(*) filter(where timestamp<>block_timestamp) timestamp_mismatch
      from chron
    """).fetchone()
    dup=int(con.execute("""
      select count(*) from (
        select block_number,log_index,count(*) n from chron group by 1,2 having n>1
      )
    """).fetchone()[0])
    if any(int(x) for x in (audit[6],audit[7],audit[8],dup)):
        raise RuntimeError(f"chronology hard gate failed: {audit}, dup={dup}")
    p82=pct_row(con,"chron",0.82)
    final_start=next_midnight(p82[0])
    con.execute(f"create temp view prefinal as select * from chron where timestamp < {final_start}")
    p65=pct_row(con,"prefinal",0.65)
    dev_start=next_midnight(p65[0])
    if not (int(audit[4]) < dev_start < final_start <= int(audit[5])):
        raise RuntimeError("invalid chronological split")
    purge=1800
    counts=con.execute(f"""
      select
       count(*) filter(where timestamp < {dev_start}-{purge}) train_eligible,
       count(*) filter(where timestamp >= {dev_start} and timestamp < {final_start}-{purge}) dev_eligible,
       count(*) filter(where timestamp >= {final_start}) final_rows,
       count(*) filter(where timestamp >= {dev_start}-{purge} and timestamp < {dev_start}) train_dev_purge,
       count(*) filter(where timestamp >= {final_start}-{purge} and timestamp < {final_start}) dev_final_purge
      from chron
    """).fetchone()
    class_rows=con.execute("""
      select mapping_class, count(*) AS row_count, count(distinct condition_id) AS condition_count
      from chron group by 1 order by 1
    """).fetchdf().to_dict("records")
    monthly=con.execute("""
      select strftime(to_timestamp(timestamp),'%Y-%m') month,
             count(*) AS row_count, count(distinct condition_id) AS condition_count,
             count(distinct sig_market_id) AS sig_market_count
      from chron group by 1 order by 1
    """).fetchdf().to_dict("records")
    price=con.execute("""
      select
        avg(p_yes) mean_p,
        quantile_cont(p_yes,0.01) p01, quantile_cont(p_yes,0.05) p05,
        quantile_cont(p_yes,0.25) p25, quantile_cont(p_yes,0.5) p50,
        quantile_cont(p_yes,0.75) p75, quantile_cont(p_yes,0.95) p95,
        quantile_cont(p_yes,0.99) p99
      from chron
    """).fetchone()
    same_block=con.execute("""
      select
        count(*) AS block_count,
        sum(n) AS fill_count,
        sum(case when n>1 then n else 0 end) AS fills_in_multifill_blocks,
        count(*) filter(where n>1) AS multifill_block_count
      from (select block_number,count(*) n from chron group by 1)
    """).fetchone()
    cond_stats=con.execute("""
      select
        quantile_cont(n,0.5) median_fills_per_condition,
        quantile_cont(n,0.9) p90_fills_per_condition,
        max(n) max_fills_per_condition
      from (select condition_id,count(*) n from chron group by 1)
    """).fetchone()
    lifetimes=con.execute("""
      select
       quantile_cont(mx-mn,0.5) median_lifetime_s,
       quantile_cont(mx-mn,0.9) p90_lifetime_s,
       max(mx-mn) max_lifetime_s
      from (select condition_id,min(timestamp) mn,max(timestamp) mx from chron group by 1)
    """).fetchone()
    final_coverage=con.execute(f"""
      select count(distinct condition_id), count(distinct sig_market_id)
      from chron where timestamp >= {final_start}
    """).fetchone()
    absent=int(con.execute(f"""
      select count(*) from (
        select distinct condition_id from chron
        except
        select distinct condition_id from chron where timestamp >= {final_start}
      )
    """).fetchone()[0])
    split={
      "schema_version":1,
      "experiment_id":"PRED-006",
      "ordering":["block_number","log_index"],
      "selection_rule":{
        "final_anchor_fraction":0.82,
        "final_start_rule":"first UTC midnight strictly after the floor(82% * N) canonical economic-fill row timestamp",
        "dev_anchor_fraction_of_prefinal":0.65,
        "dev_start_rule":"first UTC midnight strictly after the floor(65% * N_prefinal) canonical economic-fill row timestamp",
        "target_purge_seconds":purge
      },
      "dev_start_utc":datetime.fromtimestamp(dev_start,tz=timezone.utc).isoformat(),
      "dev_start_epoch":dev_start,
      "final_start_utc":datetime.fromtimestamp(final_start,tz=timezone.utc).isoformat(),
      "final_start_epoch":final_start,
      "train_target_eligible_rows":int(counts[0]),
      "dev_target_eligible_rows":int(counts[1]),
      "final_rows":int(counts[2]),
      "train_dev_purge_rows":int(counts[3]),
      "dev_final_purge_rows":int(counts[4]),
      "final_conditions":int(final_coverage[0]),
      "final_sig_markets":int(final_coverage[1]),
      "conditions_absent_from_final":absent,
      "predictive_outcomes_accessed":False
    }
    sp=OUT/"split_manifest.json"
    sp.write_text(json.dumps(split,indent=2,sort_keys=True)+"\n")
    chronology={
      "schema_version":1,
      "economic_rows":int(audit[0]),"conditions":int(audit[1]),"sig_markets":int(audit[2]),
      "blocks":int(audit[3]),"min_timestamp":int(audit[4]),"max_timestamp":int(audit[5]),
      "missing_block_numbers":int(audit[6]),"missing_block_timestamps":int(audit[7]),
      "timestamp_mismatches":int(audit[8]),"duplicate_block_log_groups":dup,
      "upstream_gate_protocol_commit":gatej.get("protocol_commit"),
      "upstream_gate_report_sha256":sha256(gate)
    }
    cp=OUT/"chronology_audit.json"; cp.write_text(json.dumps(chronology,indent=2,sort_keys=True)+"\n")
    universe={
      "schema_version":1,
      "mapping_class_rows":class_rows,
      "monthly_activity":monthly,
      "price_distribution":{"mean":price[0],"p01":price[1],"p05":price[2],"p25":price[3],"p50":price[4],"p75":price[5],"p95":price[6],"p99":price[7]},
      "same_block_structure":{"blocks":int(same_block[0]),"fills":int(same_block[1]),"fills_in_multifill_blocks":int(same_block[2] or 0),"multifill_blocks":int(same_block[3])},
      "condition_activity":{"median_fills":cond_stats[0],"p90_fills":cond_stats[1],"max_fills":cond_stats[2]},
      "market_lifetimes_seconds":{"median":lifetimes[0],"p90":lifetimes[1],"max":lifetimes[2]},
      "note":"Non-predictive Phase-0 characterization only; no targets/models/performance computed."
    }
    up=OUT/"universe_characterization.json"; up.write_text(json.dumps(universe,indent=2,sort_keys=True)+"\n")
    result={
      "split_manifest_sha256":sha256(sp),
      "chronology_audit_sha256":sha256(cp),
      "dev_start_utc":split["dev_start_utc"],
      "final_start_utc":split["final_start_utc"],
      "train_target_eligible_rows":split["train_target_eligible_rows"],
      "dev_target_eligible_rows":split["dev_target_eligible_rows"],
      "final_rows":split["final_rows"],
      "final_conditions":split["final_conditions"],
      "final_sig_markets":split["final_sig_markets"],
      "conditions_absent_from_final":split["conditions_absent_from_final"],
      "predictive_outcomes_accessed":False
    }
    print("PRED006_PHASE0_RESULT="+json.dumps(result,sort_keys=True),flush=True)
    con.close()
if __name__=="__main__": main()
