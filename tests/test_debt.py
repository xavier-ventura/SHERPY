import pytest

from sherpy.calcs import calc_debt_payoff
from sherpy.calcs.debt import _amortize


def test_zero_interest():
    assert _amortize(1200, 0.0, 100) == (12, 0.0)


def test_payment_below_interest_never_pays():
    assert _amortize(10000, 0.24, 100) is None


def test_daniel_credit_card_matches_sherpas_payoff_year(fh):
    r = calc_debt_payoff(fh("daniel-emily-johnson"), "Chase Sapphire")
    # Sherpas: ccPayoffDate 2034-01 (≈ 7 años)
    assert r.pays_off and 80 <= r.baseline_months <= 90
    assert r.total_interest > 0


def test_extra_payment_saves_interest(fh):
    r = calc_debt_payoff(fh("daniel-emily-johnson"), "chase", extra_monthly=500)
    assert r.months < r.baseline_months
    assert r.interest_saved > 0
    assert r.months_saved == r.baseline_months - r.months


def test_zero_balance_debt(fh):
    r = calc_debt_payoff(fh("samantha-lewis"), "Amex Gold")
    assert r.months == 0 and r.total_interest == 0


def test_unknown_debt_raises(fh):
    with pytest.raises(ValueError):
        calc_debt_payoff(fh("michael-carter"), "nope")
