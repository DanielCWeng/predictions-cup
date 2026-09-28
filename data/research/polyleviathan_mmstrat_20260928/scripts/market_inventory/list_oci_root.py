#!/usr/bin/env python3
"""Confirm whether root-level legacy daily Parquet objects remain in OCI."""
from __future__ import annotations
import collections,json,os,re,sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path('/home/ubuntu/polymarketwhale')
OUT=ROOT/'campaigns/sisterreq_20260925/oci_root_inventory.json'
load_dotenv(ROOT/'Sonar/.env',override=False)
sys.path.insert(0,str(ROOT/'Sonar'))
import config,oci
signer,cfg=config.get_oci_signer_and_config()
client=oci.object_storage.ObjectStorageClient(cfg,signer=signer,timeout=(10,120)) if signer else oci.object_storage.ObjectStorageClient(cfg,timeout=(10,120))
ns=client.get_namespace().data; bucket=config.OCI_BUCKET_NAME
names=[];start=None
while True:
 args={'namespace_name':ns,'bucket_name':bucket,'limit':1000}
 if start:args['start']=start
 page=client.list_objects(**args); names.extend(o.name for o in page.data.objects)
 start=page.data.next_start_with
 if not start:break
roots=[n for n in names if '/' not in n]
daily=[]
for n in roots:
 if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.parquet',n):
  try:datetime.strptime(n[:10],'%Y-%m-%d')
  except ValueError:pass
  else:daily.append(n)
daily_outside_trades=[]
for n in names:
 if n.startswith('trades/'): continue
 base=n.rsplit('/',1)[-1]
 if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.parquet',base):
  try:datetime.strptime(base[:10],'%Y-%m-%d')
  except ValueError:pass
  else:daily_outside_trades.append(n)
counts=collections.Counter(n.split('/',1)[0] for n in names)
OUT.write_text(json.dumps({'object_count':len(names),'root_object_count':len(roots),'root_daily_parquet':sorted(daily),'daily_parquet_outside_trades':sorted(daily_outside_trades),'top_level_prefix_counts':counts},indent=2)+'\n')
print('objects',len(names),'root_objects',len(roots),'root_daily_parquet',len(daily))
print('daily_parquet_outside_trades',len(daily_outside_trades),'prefixes',sorted({n.split('/',1)[0] for n in daily_outside_trades}))
print('root_names',roots[:40])
print('top_level_prefix_counts',counts.most_common(30))
print('output',OUT.name)
