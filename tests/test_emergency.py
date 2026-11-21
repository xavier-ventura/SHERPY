import pytest

from sherpy.calcs import calc_emergency_fund


def overview(fh):
    return fh["key_metrics"]["protection"]["emergencyFund"]["cashAndExpensesOverview"]


@pytest.mark.parametrize("case_id", ["daniel-emily-johnson", "michael-carter", "samantha-lewis", "timothy-melanie-weah"])
def test_months_covered_matches_sherpas(fh, case_id):
    data = fh(case_id)
    ov = overview(data)
    r = calc_emergency_fund(data, money_market=ov["totalMoneyMarketAmount"])
    assert r.accessible_cash == pytest.approx(ov["totalCashAmount"])
    assert r.monthly_outflows == pytest.approx(ov["monthlyExpenses"])
    assert round(r.months_covered, 1) == pytest.approx(float(data["key_metrics"]["facts"]["cash"]["monthsCovered"]["value"]))


def test_bank_only_and_shortfall(fh):
    r = calc_emergency_fund(fh("daniel-emily-johnson"))
    assert r.bank_cash == 14000
    assert r.monthly_outflows == pytest.approx(7937.5 + 5420)
    assert r.shortfall == pytest.approx(13357.5 * 6 - 14000)
