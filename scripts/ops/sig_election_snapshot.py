"""Read-only SIG election snapshot command; implementation is importable/tested.

No account writes, no order dispatch and no production service integration.
"""

from predictions_cup.analysis.election_sig_read import main

if __name__ == "__main__":
    raise SystemExit(main())
