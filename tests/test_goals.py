import pytest

from sherpy.calcs import calc_goal_gap


def test_education_uses_529_split(fh):
    data = fh("daniel-emily-johnson")
    r = calc_goal_gap(data, "College Oliver", assumed_return=0.0)
    goal = next(g for g in data["profile"]["goals"] if g["name"] == "College Oliver")
    expected = sum(c["yearly"] * (c["to_year"] - c["from_year"] + 1) for c in goal["yearly_costs"])
    assert r.total_cost == pytest.approx(expected)  # coste nominal (inflado) de Sherpas
    assert r.current_savings == 15000  # 529 de 30k repartido entre 2
    assert r.annual_contribution == 4000
    assert r.years_to_goal == 7
    assert r.projected_savings == pytest.approx(15000 + 4000 * 7)
    assert r.gap == pytest.approx(expected - 43000)
    assert r.required_extra_monthly == pytest.approx((expected - 43000) / 84)


def test_education_matches_custodial_by_child_name(fh):
    r = calc_goal_gap(fh("samantha-lewis"), "College Ethan", assumed_return=0.0)
    ethan = next(a for a in fh("samantha-lewis")["profile"]["accounts"] if "Ethan" in a["name"])
    assert r.current_savings == ethan["balance"]  # solo la cuenta custodial de Ethan
    assert r.years_to_goal == 0


def test_home_purchase_down_payment(fh):
    r = calc_goal_gap(fh("michael-carter"), "Purchase a home", assumed_return=0.0)
    assert r.total_cost == pytest.approx(113300 + 9464)
    assert r.years_to_goal == 1
    assert r.gap == pytest.approx(r.total_cost)


def test_home_purchase_with_savings(fh):
    r = calc_goal_gap(fh("michael-carter"), "Purchase a home", assumed_return=0.0, current_savings=50000)
    assert r.gap == pytest.approx(113300 + 9464 - 50000)


def test_retirement_uses_sherpas_metrics(fh):
    data = fh("daniel-emily-johnson")
    r = calc_goal_gap(data, "Retirement")
    km = data["key_metrics"]["savingsAndExpenses"]["annualSavings"]
    assert r.gap == km["gapInRetirementAccountsAtRetirementAge"]
    assert r.required_extra_monthly == pytest.approx(km["requiredRetirementExtraAnnualSavings"] / 12)
    assert r.target_year == 2044


def test_fv_positive_return_increases_projection(fh):
    a = calc_goal_gap(fh("timothy-melanie-weah"), "College Liam", assumed_return=0.0)
    b = calc_goal_gap(fh("timothy-melanie-weah"), "College Liam", assumed_return=0.05)
    assert b.projected_savings >= a.projected_savings
