import json

import pytest

from sherpy.calcs import calc_market_shock
from tests.conftest import load


def test_shock_on_positions(fh):
    inv = load("daniel-emily-johnson", "investment-analysis")
    r = calc_market_shock(fh("daniel-emily-johnson"), 0.2, positions=inv["positions"], monthly_outflows=13357.5)
    assert r.invested_total == pytest.approx(330000)
    assert r.loss == pytest.approx(66000)
    assert r.value_after == pytest.approx(264000)
    assert sum(a.loss for a in r.per_account) == pytest.approx(r.loss)
    assert r.loss_in_months_of_outflows == pytest.approx(66000 / 13357.5)


def test_shock_falls_back_to_accounts(fh):
    r = calc_market_shock(fh("daniel-emily-johnson"), 0.1)
    assert r.invested_total == pytest.approx(330000)


def test_invalid_drop():
    with pytest.raises(ValueError):
        calc_market_shock({"profile": {"accounts": []}}, 20)
