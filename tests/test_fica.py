import pytest

from sherpy.calcs import calc_fica
from tests.test_tax import sherpas_tax


@pytest.mark.parametrize("case_id", ["daniel-emily-johnson", "michael-carter", "samantha-lewis", "timothy-melanie-weah"])
def test_fica_matches_sherpas(fh, legal, case_id):
    data = fh(case_id)
    assert calc_fica(data, legal, 2026).total == pytest.approx(sherpas_tax(data, "FICA tax"), abs=0.5)


def test_samantha_ss_capped_and_additional_medicare(fh, legal):
    r = calc_fica(fh("samantha-lewis"), legal, 2026)
    assert r.per_person[0].social_security == pytest.approx(184500 * 0.062)
    assert r.additional_medicare == pytest.approx(50000 * 0.009)


def test_mfj_additional_medicare(fh, legal):
    assert calc_fica(fh("daniel-emily-johnson"), legal, 2026).additional_medicare == pytest.approx(270)
