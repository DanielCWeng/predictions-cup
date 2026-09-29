# Risk Policy

BUILD-001 implements no numeric limits and no autonomous trading.

Non-negotiable architectural principles:

- strategies never directly submit orders;
- all proposed trades ultimately pass central Risk;
- stale or unreliable data must be capable of preventing trading;
- kill-switch capability is required before live autonomous ordering;
- no autonomous trading exists yet;
- live limits remain TBD pending market observation and later tickets.

No tournament-specific exposure values are defined in this repository foundation.


## BUILD-009 branch status

Draft PR #50 implements the central fail-closed Risk mechanism but does not choose tournament risk
appetite. LIVE numeric caps remain externally configured and have no guessed repository defaults.
Untrusted account/depth state and uncertain orders are explicitly conservative.

This section describes branch-only capability under review; it is not accepted on `main` until
BUILD-009 is independently reviewed and merged.
