"""Independent cash/marked-position oracle and diagnostic semantic counterexample."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
import random
import pytest
from predictions_cup.risk.capital import RiskFill, replay_fills
from predictions_cup.runtime.models import OrderAction, OutcomeSide
from predictions_cup.live_learn.contracts import ExecutionEvidence, FillEvidence
from predictions_cup.live_learn.scoring import QuoteEconomicsScorer, ScoreContext


def test_accounting_matches_independent_cash_and_terminal_payoff_oracle():
    rng = random.Random(197038)
    for trial in range(100):
        fills = []
        cash = D(0)
        yes = D(0)
        no = D(0)
        for i in range(30):
            side = rng.choice(list(OutcomeSide))
            action = rng.choice(list(OrderAction))
            qty = D(rng.randrange(1, 11))
            price = D(rng.randrange(1, 100)) / 100
            fee = D('0.001') * qty
            direction = D(1) if action == OrderAction.BUY else D(-1)
            cash -= direction * qty * price + fee
            if side == OutcomeSide.YES:
                yes += direction * qty
            else:
                no += direction * qty
            fills.append(RiskFill(f'{trial}-{i}', 'x', 'm', side, action, qty, price, i, fee))
        result = replay_fills(fills + fills)  # duplicate evidence must be idempotent
        for mark in (D(0), D('0.37'), D(1)):
            oracle = cash + yes * mark + no * (1 - mark)
            reconstructed = result.realised_pnl + sum(
                p.signed_quantity * (mark - p.avg_entry_yes) for p in result.positions
            )
            assert abs(reconstructed - oracle) < D('1e-20')


@pytest.mark.xfail(strict=True, reason='ASTRA-014: adverse selection hides adverse information move within spread')
def test_adverse_selection_reports_midpoint_loss_even_while_fill_profitable():
    t = datetime(2026, 10, 1, tzinfo=UTC)
    decision = SimpleNamespace(fair_value=None, observed_at=t)
    execution = ExecutionEvidence(True, None, 1, (
        FillEvidence('f', 'op', 'o', 'x', 'buy', 1, .49, t, 0),
    ))
    result = QuoteEconomicsScorer().score(ScoreContext(
        decision, SimpleNamespace(midpoint=.50), SimpleNamespace(midpoint=.495), execution,
    ))
    assert result.metrics['post_fill_markout'] == pytest.approx(.005)
    assert result.metrics['adverse_selection'] == pytest.approx(.005)
