"""Fondo de emergencia."""

from __future__ import annotations

from typing import Any

from ._helpers import CASH_ACCOUNT_TYPES
from .models import EmergencyFundResult


def calc_emergency_fund(fh: dict[str, Any], money_market: float = 0.0, target_months: float = 6.0) -> EmergencyFundResult:
    """Meses cubiertos = efectivo accesible (checking + savings + money market) /
    salidas mensuales (gastos + cuotas de deuda). Devuelve también el objetivo y el hueco."""
    p = fh["profile"]
    bank = sum(float(a["balance"]) for a in p["accounts"] if a["account_type"] in CASH_ACCOUNT_TYPES)
    expenses = sum(float(e["monthly"]) for e in p["expenses"])
    debt = sum(float(l.get("monthly_payment") or 0) for l in p["liabilities"])
    outflows = expenses + debt
    cash = bank + money_market
    target = outflows * target_months
    return EmergencyFundResult(
        bank_cash=bank,
        money_market=money_market,
        accessible_cash=cash,
        monthly_expenses=expenses,
        monthly_debt_payments=debt,
        monthly_outflows=outflows,
        months_covered=(cash / outflows) if outflows else float("inf"),
        target_months=target_months,
        target_amount=target,
        shortfall=max(target - cash, 0.0),
    )
