# Operations

## Current state

- No production deployment exists.
- No trading daemon exists.
- No restart/recovery guarantees exist yet.

The current application shell starts, logs FOUNDATION/non-trading status, and exits cleanly.

## Eventual operating expectations

Future production operation is expected to provide, at minimum:

- a supervised process;
- automatic restart;
- reconciliation on restart;
- health visibility;
- log rotation;
- no dependency on a developer laptop.

These mechanisms are expectations only and are not implemented by BUILD-001.
