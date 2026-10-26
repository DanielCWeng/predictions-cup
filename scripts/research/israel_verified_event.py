#!/usr/bin/env python3
"""Manual research verification of official results or corroborated reports.

Never interprets reporting as a trading signal. A human must read underlying
sources; this program validates basic provenance/timestamps, NOT truthfulness.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse
import time

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "israel_verified_results_manual.jsonl"
EVENTS = HERE / "israel_rehearsal_events.jsonl"


def valid_https(url: str) -> str:
    parsed=urlparse(url)
    if parsed.scheme!="https" or not parsed.hostname or not parsed.path:
        raise ValueError("full HTTPS source URL with path required")
    if parsed.hostname.endswith(".local") or parsed.hostname in {"localhost","news.google.com"}:
        raise ValueError("not a valid independent primary-source host")
    return parsed.hostname


def known_assets() -> set[str]:
    for p in (EVENTS, EVENTS.with_suffix(".jsonl.1")):
        if not p.exists():
            continue
        for line in p.open(encoding="utf-8"):
            try:
                item=json.loads(line)
            except ValueError:
                continue
            if item.get("kind")=="collector_started":
                return set(item.get("instruments",{}))
    return set()


def iso_epoch_ms(raw: str) -> int:
    dt=datetime.fromisoformat(raw.replace("Z","+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must specify explicit timezone")
    return int(dt.timestamp()*1000)


def make_record(*,asset_id: str, description: str, kind: str, url: str,
                corroborator: str, published_iso: str, reviewer: str,
                methodology: str, now_ms: int | None = None) -> dict:
    if asset_id not in known_assets():
        raise ValueError("asset id absent from the six observed markets")
    if kind not in {"official_count","race_call","verified_report","exit_poll"}:
        raise ValueError("unrecognised evidence kind")
    domain, other = valid_https(url), valid_https(corroborator)
    if domain == other:
        raise ValueError("independent second domain required")
    if len(description.strip())<12 or len(reviewer.strip())<3 or len(methodology.strip())<16:
        raise ValueError("document who reviewed and how independence was checked")
    received=int(time.time()*1000) if now_ms is None else now_ms
    published=iso_epoch_ms(published_iso)
    if published>received+2000 or received-published>24*3600*1000:
        raise ValueError("source publication time is future or older than one day")
    return {
        "kind":"verified_results_event",
        "evidence_type":kind,
        "asset_id":asset_id,
        "description":description.strip()[:500],
        "source_url":url,
        "corroborator_url":corroborator,
        "source_domains":[domain,other],
        "source_published_claim_utc":published_iso,
        "published_epoch_ms":published,
        "received_epoch_ms":received,
        "received_utc":datetime.fromtimestamp(received/1000,timezone.utc).isoformat(),
        "independent_evidence":True,
        "reviewer":reviewer.strip(),
        "independence_method":methodology.strip()[:500],
        "trading_authority":False,
        "causality_established":False,
        "note":"Manual source-identity and provenance attestation; not machine-authenticated results or proof of causal price impact",
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--asset-id",required=True)
    p.add_argument("--type",choices=["official_count","race_call","verified_report","exit_poll"],required=True)
    p.add_argument("--description",required=True)
    p.add_argument("--source-url",required=True)
    p.add_argument("--second-url",required=True)
    p.add_argument("--source-published-utc",required=True)
    p.add_argument("--reviewer",required=True)
    p.add_argument("--independence-method",required=True)
    a=p.parse_args()
    record=make_record(asset_id=a.asset_id,description=a.description,
                       kind=a.type,url=a.source_url,corroborator=a.second_url,
                       published_iso=a.source_published_utc,reviewer=a.reviewer,
                       methodology=a.independence_method)
    with OUTPUT.open("a",encoding="utf-8") as out:
        out.write(json.dumps(record,ensure_ascii=False)+"\n")
    print("MANUAL_EVIDENCE_RECORDED",record["received_utc"],record["asset_id"])
if __name__=="__main__":
    main()
