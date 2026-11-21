"""Impacto de una caída de mercado sobre la cartera invertible."""

from __future__ import annotations

from typing import Any

from ._helpers import CASH_ACCOUNT_TYPES
from .models import AccountShock, MarketShockResult


CASH_CATEGORY_MARKERS = ("money market", "cash")


def _is_cash(position: dict[str, Any]) -> bool:
    return any(m in str(position.get("category", "")).lower() for m in CASH_CATEGORY_MARKERS)


def calc_market_shock(fh: dict[str, Any], drop_pct: float, positions: list[dict[str, Any]] | None = None,
                      monthly_outflows: float | None = None) -> MarketShockResult:
    """Pérdida en USD si la cartera invertible cae `drop_pct` (decimal, 0.2 = −20 %).

    Usa `investment-analysis.positions` si se pasan (excluyendo efectivo y money market, que no caen con el
    mercado); si no, las cuentas que no son checking/savings. Aplica la misma caída a todo lo invertido
    (escenario simple: no distingue renta fija de variable).
    """
    if not 0 <= drop_pct <= 1:
        raise ValueError("drop_pct debe estar entre 0 y 1 (0.2 = caída del 20 %).")
    by_account: dict[str, float] = {}
    if positions:
        for p in positions:
            if _is_cash(p):
                continue
            by_account[p["account"]] = by_account.get(p["account"], 0.0) + float(p["value"])
    else:
        for a in fh["profile"]["accounts"]:
            if a["account_type"] not in CASH_ACCOUNT_TYPES and float(a["balance"]) > 0:
                by_account[a["name"]] = by_account.get(a["name"], 0.0) + float(a["balance"])
    total = sum(by_account.values())
    loss = total * drop_pct
    return MarketShockResult(
        drop_pct=drop_pct,
        invested_total=total,
        loss=loss,
        value_after=total - loss,
        per_account=[AccountShock(account=k, value=v, loss=v * drop_pct) for k, v in by_account.items()],
        loss_in_months_of_outflows=(loss / monthly_outflows) if monthly_outflows else None,
    )
