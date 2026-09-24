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
