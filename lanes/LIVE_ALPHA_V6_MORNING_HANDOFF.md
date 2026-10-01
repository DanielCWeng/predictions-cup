# LIVE-ALPHA-V6 Morning Handoff

Branch: `analysis/live-alpha-v6-overnight-001`

## Frozen boundary

Primary training cutoff: **2026-10-01T17:18:47Z**.

Anything later is out-of-sample for the frozen primary score. Do not tune thresholds on the overnight tape before producing `V6_RESULT.json`.

## One-command run

On West, use an isolated worktree of this branch and run:

```bash
bash scripts/research/live_alpha_v6/run_morning.sh /home/ec2-user/codex_lane/live_alpha_v6_morning
```

The script:

1. reads SIG + PM capture health;
2. chooses the latest common mature end time minus 1800s;
3. builds `trade_features.csv.gz` and `state_minute.csv.gz`;
4. includes PAPER events when present;
5. scores the frozen OOS rules;
6. writes `V6_REPORT.md` and `V6_RESULT.json`;
7. creates a compact Kaggle-ready package.

It does not stop or modify capture.

## Frozen primary rules

- Maker: 1.5c from PM mid; PM <=1c; >=50 top size each side; SIG excess spread >=0.5c.
- Residual taker: >=2c executable residual; PM <=2c; >=50 top size; 60s cooldown.
- Structural pair: >=1c same-race D/R pair discrepancy; coherent PM pair; PM <=1c each leg.
- 005I five-minute reversal: context only.
- PRED-006-inspired hazard: context only.
- 1026 and 1072 remain quarantined.
- Lead-lag cannot be promoted.

## Mandatory evidence

Report raw and 5-minute de-clustered counts; market/race counts; mean, median and 10% trimmed mean; market-cluster bootstrap 95% lower bound; positive-market fraction; leave-one-market-out worst mean; max market share and HHI.

For maker/residual/structural, report PM markouts and SIG executable recycle economics at 60s, 300s and 1800s. Stress 0c / 0.25c / 0.5c / 1c costs.

Maker must show both TOUCH and strict PRINT-THROUGH fill models. Do not treat TOUCH alone as queue-realistic.

## Tested tonight

The full morning procedure was smoke-tested against a mature post-freeze window ending 2026-10-01T18:54:56Z.

It produced 6,027 trade features and 13,344 minute states in under 30 seconds, while both live capture services continued normally. This proves the pipeline plumbing, not the overnight scientific conclusion.

## Output labels

- SURVIVES_OOS
- PAPER_EXTEND
- CONTEXT_ONLY
- FAILS_OOS
- QUARANTINE

No label is automatic LIVE approval.
