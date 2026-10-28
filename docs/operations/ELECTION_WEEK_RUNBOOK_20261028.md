# Election-week delivery runbook — 28 October through 4 November 2026

## The four commitments and their independently checkable gates

1. **Execution and capital:** live service must have trusted account reconciliation and recover unresolved operations; a taker candidate with independently validated edge must either execute with a recorded fill or have a specific documented rejection. Until that is achieved, counters such as `candidate_ready` are NOT fills. `python -m predictions_cup.analysis.election_capital --runtime-env /home/ec2-user/.config/predictions-cup/runtime.env --max-positions 12` performs a GET-only, 12-position liquidation survey. It estimates selling YES at YES bids and selling held NO at (1 minus YES asks), includes visible depth, a 2-cent-per-share cost reserve, and the actual tournament P&L gap; **it NEVER submits, cancels, or approves an order**. Review open orders, age and locked collateral separately before deciding to exit.
2. **Official results input:** `python scripts/ops/election_official_probe.py` GETs the Texas Secretary of State official results page, parses the 2026 U.S. Senate section, and returns a SHA-256 fingerprint plus source/status/precincts. Before 3 November it must return PRE_ELECTION; a 0% reporting page later returns NO_RESULTS_YET. A later nonzero result remains UNVERIFIED_COUNTS and is NOT a probability. No alternative website, mock data, prior-election URL, unexpected candidates, or missing precinct data can silently satisfy the parser. The official page is https://electionresults.sos.state.tx.us/results.html.
3. **Event-repricing decision:** the existing `observe/election_lag.py` is a pure, shadow-only scorer; `research/election_nowcast.py` can represent verified count observations and remaining-ballot models. **There is no authorised production official-count-to-calibrated-probability adapter yet**. Require source publication time, independently validated race/contract mapping, as-of SIG bid/ask depth, bounded data age, trusted account state, independently evaluated calibration and central RISK approval. No forecast or trade may be invented just because the official report prints a vote lead. Ohio currently serves mock results; Maine's Senate contest uses ranked-choice tabulation; Texas has multiple candidates and outstanding ballots.
4. **Terminal decisions:** `python scripts/ops/election_terminal_score.py <reviewed_input.json>` already scores **joint** resolution scenarios for baseline holdings and proposed trades against a target balance. Its inputs must include every material portfolio holding and a defensible range of scenario weights. Comparing only selected markets while pretending other positions are cash would be materially wrong. Recheck source/venue/risk economics through the 4 November 16:59:55 UTC candidate cutoff; the exchange trading end is 17:00 UTC. No model can establish its own accuracy simply by producing probabilities.

## Deployment and rollback

Main presently includes the reconciliation fix PR #367, terminal scorer PR #368 and SIG read-only books PR #369. Source changes are CI-reviewed before a controlled West service restart. Inspect `systemctl status predictions-cup-maker-live.service`, the execution journal and authoritative account before enabling any new exposure. Never assume successful `git switch` restarts a running Python process. Preserve the previous commit for rollback and confirm the active process loaded the new version after the restart. Prior West observations had repeated unresolved startup operations and taker depth rejection, with rotation flags disabled. Those are hard blockers until newly observed post-restart metrics prove recovery.

If official results are absent or probability calibration is missing at 3 November poll close, **shadow mode and no new orders are the correct observed outcomes**, not a failure to act promptly. An order must be the consequence of measured conservative *executable* advantage and permitted capital, never of timetable pressure or leaderboard desperation.

## Read-only commands

```bash
python scripts/ops/election_official_probe.py
python scripts/ops/sig_election_snapshot.py --race Maine --race Kansas
python -m predictions_cup.analysis.election_capital --runtime-env /home/ec2-user/.config/predictions-cup/runtime.env --max-positions 12
python scripts/ops/election_terminal_score.py tests/fixtures/election_terminal_example.json
```

The final command uses a **synthetic example**, not a real election or trade recommendation.
