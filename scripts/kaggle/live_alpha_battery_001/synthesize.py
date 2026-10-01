
from pathlib import Path
import json
root=Path("lanes/live_alpha_battery_001"); items=[]
for p in root.rglob("result.json"):
    try:
        d=json.loads(p.read_text()); items.append((d.get("lane",p.parent.name),p,d))
    except Exception: pass
lines=["# LIVE ALPHA BATTERY 001 — Synthesis","",f"Completed lane results found: **{len(items)}**.",""]
for lane,p,d in sorted(items):
    lines += [f"## {lane}","",f"Artifact: {p}",""]
    if "candidate_count" in d: lines.append(f"Candidate count: **{d['candidate_count']}**.")
    if "candidates" in d: lines.append(f"Candidate models: **{len(d['candidates'])}**.")
    top=d.get("top") or d.get("top_validation") or d.get("candidates") or []
    if top: lines += ["","Top result snapshot:",json.dumps(top[:5],indent=2,sort_keys=True)]
    lines.append("")
(root/"BATTERY_SUMMARY.md").write_text("\n".join(lines)+"\n"); print("\n".join(lines))
