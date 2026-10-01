#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-/home/ec2-user/predictions-cup}"
WORK_ROOT="${1:-/home/ec2-user/codex_lane/live_alpha_v6_morning}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="${PYTHON_BIN:-$SOURCE_ROOT/.venv/bin/python}"
START="2026-10-01T17:18:47Z"

mkdir -p "$WORK_ROOT/package" "$WORK_ROOT/score"

END="$("$PY" - "$SOURCE_ROOT" <<'PY'
import glob, sqlite3, sys
from datetime import datetime, timezone
import pyarrow.parquet as pq
root=sys.argv[1]
con=sqlite3.connect(f"file:{root}/data/sig_realtime.sqlite3?mode=ro",uri=True)
sig=con.execute("select max(rest_observed_at) from price_observations").fetchone()[0]
con.close()
sig_dt=datetime.fromisoformat(sig.replace("Z","+00:00")).astimezone(timezone.utc)
files=sorted(glob.glob(root+"/data/004c_c_crossvenue/polymarket_research/observations/**/*.parquet",recursive=True))[-20:]
pm_dt=None
for f in files:
    t=pq.read_table(f,columns=["source_timestamp"])["source_timestamp"].to_pylist()
    if t:
        x=max(t)
        if pm_dt is None or x>pm_dt: pm_dt=x
common=min(sig_dt,pm_dt)
end=common.timestamp()-1800
print(datetime.fromtimestamp(end,timezone.utc).isoformat().replace("+00:00","Z"))
PY
)"

"$PY" - "$START" "$END" <<'PY'
import sys
from datetime import datetime
s=datetime.fromisoformat(sys.argv[1].replace("Z","+00:00"))
e=datetime.fromisoformat(sys.argv[2].replace("Z","+00:00"))
if e<=s:
    raise SystemExit(f"V6 mature window unavailable: start={s} end={e}")
print(f"V6 mature OOS window: {s.isoformat()} -> {e.isoformat()}")
PY

PAPER_ARGS=()
if [ -s /home/ec2-user/codex_lane/paper/events.jsonl ]; then
  PAPER_ARGS=(--paper-events /home/ec2-user/codex_lane/paper/events.jsonl)
fi

"$PY" "$HERE/scripts/research/live_alpha_v6/build_snapshot.py" \
  --source-root "$SOURCE_ROOT" \
  --output "$WORK_ROOT/package" \
  --start "$START" \
  --end "$END" \
  "${PAPER_ARGS[@]}"

cp "$HERE/data/research/live_alpha_v6/FREEZE.json" "$WORK_ROOT/package/FREEZE.json"
cat > "$WORK_ROOT/package/dataset-metadata.json" <<'JSON'
{
  "title": "SIG Live Alpha V6 Overnight OOS",
  "id": "polyleviathan/sig-live-alpha-v6-overnight",
  "licenses": [{"name": "CC0-1.0"}]
}
JSON

"$PY" "$HERE/scripts/research/live_alpha_v6/evaluate_oos.py" \
  --input "$WORK_ROOT/package" \
  --freeze "$HERE/data/research/live_alpha_v6/FREEZE.json" \
  --output "$WORK_ROOT/score"

cp "$WORK_ROOT/score/V6_RESULT.json" "$WORK_ROOT/package/LOCAL_FROZEN_SCORE.json"
cp "$WORK_ROOT/score/V6_REPORT.md" "$WORK_ROOT/package/LOCAL_FROZEN_REPORT.md"

echo
echo "V6_PACKAGE=$WORK_ROOT/package"
echo "V6_SCORE=$WORK_ROOT/score"
echo "V6_END=$END"
echo
sed -n '1,160p' "$WORK_ROOT/score/V6_REPORT.md"
