#!/usr/bin/env python3
"""List canonical and legacy daily Parquet objects without downloading rows."""
from __future__ import annotations
import json, os, re, sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path('/home/ubuntu/polymarketwhale')
OUT=ROOT/'campaigns/sisterreq_20260925/oci_trade_object_inventory.json'
load_dotenv(ROOT/'Sonar/.env',override=False)
sys.path.insert(0,str(ROOT/'Sonar'))
import config,oci
signer,cfg=config.get_oci_signer_and_config()
if signer: client=oci.object_storage.ObjectStorageClient(cfg,signer=signer,timeout=(10,120))
else: client=oci.object_storage.ObjectStorageClient(cfg,timeout=(10,120))
namespace=client.get_namespace().data; bucket=config.OCI_BUCKET_NAME

def list_prefix(prefix):
    names=[]; start=None
    while True:
        args={'namespace_name':namespace,'bucket_name':bucket,'prefix':prefix,'limit':1000}
        if start:args['start']=start
        page=client.list_objects(**args)
        names.extend(obj.name for obj in page.data.objects)
        start=page.data.next_start_with
        if not start:break
    return names

def valid_daily(name,source):
    base=name.removeprefix(source)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.parquet',base):return False
    try:datetime.strptime(base[:10],'%Y-%m-%d')
    except ValueError:return False
    return True

trade_names=list_prefix('trades/')
canonical=sorted(n for n in trade_names if valid_daily(n,'trades/'))
non_daily=sorted(n for n in trade_names if n.endswith('.parquet') and n not in canonical)
root_names=list_prefix('202')
legacy=sorted(n for n in root_names if valid_daily(n,''))
other_root=sorted(n for n in root_names if n.endswith('.parquet') and n not in legacy)
result={'canonical_daily_objects':canonical,'canonical_other_parquet_objects':non_daily,'legacy_root_daily_objects':legacy,'legacy_other_parquet_objects':other_root,'namespace':namespace,'bucket':bucket}
OUT.write_text(json.dumps(result,indent=2)+'\n')
for label,names in [('canonical',canonical),('legacy',legacy)]:
    dates=[n.split('/')[-1][:10] for n in names]
    print(label,'daily_objects',len(names),'first',dates[0] if dates else None,'last',dates[-1] if dates else None)
print('canonical_other',non_daily)
print('legacy_other',other_root)
print('output',OUT.name)
