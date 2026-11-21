import pytest

from sherpy.calcs import calc_federal_tax


def sherpas_tax(fh, name):
    taxes = next(o for o in fh["key_metrics"]["cashflowStatement"]["outflows"]["items"] if o["name"] == "Taxes")
    return next(i for i in taxes["items"] if i["name"] == name)["total"]


@pytest.mark.parametrize("case_id", ["daniel-emily-johnson", "michael-carter", "samantha-lewis", "timothy-melanie-weah"])
def test_federal_tax_before_credits_matches_sherpas(fh, legal, case_id):
    data = fh(case_id)
    r = calc_federal_tax(data, legal, 2026)
    assert r.tax_before_credits == pytest.approx(sherpas_tax(data, "Federal Income Tax"), abs=0.5)


def test_daniel_detail(fh, legal):
    r = calc_federal_tax(fh("daniel-emily-johnson"), legal, 2026)
    assert r.wages == 280000
    assert r.pretax_deferrals == 0
    assert r.taxable_income == 247800
    assert r.marginal_rate == 0.24
    assert r.qualifying_children == 2
    assert r.child_tax_credit == 4400  # AGI < 400k: sin phase-out
    assert r.tax_after_credits == pytest.approx(44668 - 4400)


def test_samantha_ctc_phase_out(fh, legal):
    # Ava 16 califica, Ethan 18 no. AGI 250k > 200k SINGLE → 50 * 50 = 2.500 ≥ 2.200 → 0
    r = calc_federal_tax(fh("samantha-lewis"), legal, 2026)
    assert r.qualifying_children == 1
    assert r.child_tax_credit == 0


def test_pretax_deferrals_reduce_taxable(fh, legal):
    r = calc_federal_tax(fh("michael-carter"), legal, 2026)
    assert r.pretax_deferrals == 21000  # 401k 17k + HSA 4k
    assert r.taxable_income == 180000 - 21000 - 16100


def test_2027_uses_new_values(fh, legal):
    r = calc_federal_tax(fh("michael-carter"), legal, 2027)
    assert r.standard_deduction == 16600


def test_unknown_year_raises(fh, legal):
    with pytest.raises(ValueError):
        calc_federal_tax(fh("michael-carter"), legal, 2030)
