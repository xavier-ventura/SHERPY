"""Amortización de deudas."""

from __future__ import annotations

from typing import Any

from ._helpers import find_by_name
from .models import DebtPayoffResult

MAX_MONTHS = 1200


def _amortize(balance: float, annual_rate: float, payment: float) -> tuple[int, float] | None:
    """Meses e intereses totales hasta saldar; None si la cuota no cubre los intereses."""
    r = annual_rate / 12
    months = 0
    interest_total = 0.0
    bal = balance
    while bal > 1e-9:
        interest = bal * r
        if payment <= interest:
            return None
        interest_total += interest
        bal = bal + interest - payment
        months += 1
        if months > MAX_MONTHS:
            return None
    return months, interest_total


def calc_debt_payoff(fh: dict[str, Any], debt_name: str, extra_monthly: float = 0.0) -> DebtPayoffResult:
    """Meses e intereses totales para liquidar una deuda pagando su cuota actual + `extra_monthly`,
    comparado con seguir pagando solo la cuota actual."""
    debt = find_by_name(fh["profile"]["liabilities"], debt_name, "Deuda")
    balance = float(debt["balance"])
    rate = float(debt.get("interest_rate") or 0)
    payment = float(debt.get("monthly_payment") or 0)

    base = _amortize(balance, rate, payment) if balance > 0 else (0, 0.0)
    plan = _amortize(balance, rate, payment + extra_monthly) if balance > 0 else (0, 0.0)
    note = None
    if plan is None:
        note = "La cuota no cubre los intereses mensuales: la deuda no se salda nunca."
    elif base is None:
        note = "Con la cuota actual la deuda no se salda; con el extra sí."
    return DebtPayoffResult(
        debt_name=debt["name"],
        balance=balance,
        interest_rate=rate,
        monthly_payment=payment,
        extra_monthly=extra_monthly,
        months=plan[0] if plan else None,
        total_interest=plan[1] if plan else None,
        baseline_months=base[0] if base else None,
        baseline_total_interest=base[1] if base else None,
        months_saved=(base[0] - plan[0]) if base and plan else None,
        interest_saved=(base[1] - plan[1]) if base and plan else None,
        pays_off=plan is not None,
        note=note,
    )
