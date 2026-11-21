"""Impuesto federal sobre la renta y FICA (EE. UU.)."""

from __future__ import annotations

import math
from typing import Any

from ._helpers import children, legal_scalar, legal_value, pretax_deferrals, wages_by_owner
from .models import BracketSlice, FederalTaxResult, FicaPerson, FicaResult

PLAN_YEAR = 2026


def calc_federal_tax(fh: dict[str, Any], legal: dict[str, Any], year: int = PLAN_YEAR) -> FederalTaxResult:
    """Impuesto federal estimado del hogar para `year`.

    salarios − aportaciones pre-tax (401k tradicional, HSA) − deducción estándar → tramos,
    y después el crédito por hijo (< childUnderAge, con phase-out por cada 1.000 USD sobre el umbral).
    Las edades de los hijos se proyectan desde el año del plan.
    """
    status: str = fh["case"]["tax_filing_status"]
    wages = sum(wages_by_owner(fh, year).values())
    deferrals = pretax_deferrals(fh) if wages > 0 else 0.0
    std = float(legal_value(legal, "StandardDeductionByYear", year)[status])
    brackets = legal_value(legal, "FederalIncomeTaxBracketsByYear", year)[status]

    agi = max(wages - deferrals, 0.0)
    taxable = max(agi - std, 0.0)

    slices: list[BracketSlice] = []
    lower = 0.0
    tax = 0.0
    marginal = 0.0
    for b in brackets:
        upper = float("inf") if b["upTo"] is None else float(b["upTo"])
        rate = float(b["rate"])
        portion = max(min(taxable, upper) - lower, 0.0)
        if portion > 0:
            t = portion * rate
            tax += t
            marginal = rate
            slices.append(BracketSlice(rate=rate, taxable_in_bracket=portion, tax=t))
        if taxable <= upper:
            break
        lower = upper

    ctc_cfg = legal_value(legal, "ChildTaxCreditByYear", year)
    under = int(ctc_cfg["childUnderAge"])
    n_kids = sum(1 for c in children(fh) if c["age"] + (year - PLAN_YEAR) < under)
    ctc = n_kids * float(ctc_cfg["perChild"])
    phase_start = float(ctc_cfg["phaseOutStart"].get(status, ctc_cfg["phaseOutStart"]["SINGLE"]))
    if agi > phase_start:
        steps = math.ceil((agi - phase_start) / 1000)
        ctc = max(ctc - steps * float(ctc_cfg["reductionPer1000"]), 0.0)
    ctc = min(ctc, tax)  # tratado como no reembolsable

    after = tax - ctc
    return FederalTaxResult(
        year=year,
        filing_status=status,
        wages=wages,
        pretax_deferrals=deferrals,
        standard_deduction=std,
        taxable_income=taxable,
        brackets=slices,
        tax_before_credits=tax,
        qualifying_children=n_kids,
        child_tax_credit=ctc,
        tax_after_credits=after,
        marginal_rate=marginal,
        effective_rate=(after / wages) if wages else 0.0,
    )


def calc_fica(fh: dict[str, Any], legal: dict[str, Any], year: int = PLAN_YEAR) -> FicaResult:
    """FICA del hogar para `year`: Seguridad Social (por persona, hasta el wage base),
    Medicare y Additional Medicare (sobre el umbral del estado civil, a nivel hogar)."""
    status: str = fh["case"]["tax_filing_status"]
    base = float(legal_value(legal, "SSWageBaseByYear", year))
    ss_rate = legal_scalar(legal, "FICATaxSS", "employedTaxRate")
    med_rate = legal_scalar(legal, "FICATaxMedicare", "employedTaxRate")
    prefix = {"SINGLE": "single", "MARRIED_JOINTLY": "marriedJointly",
              "MARRIED_SEPARATELY": "marriedSeparately", "HEAD_OF_HOUSEHOLD": "head"}[status]
    add_threshold = legal_scalar(legal, "FICATaxAdditionalMedicare", f"{prefix}TopRange")
    add_rate = legal_scalar(legal, "FICATaxAdditionalMedicare", f"{prefix}TaxRate")

    people: list[FicaPerson] = []
    for owner, w in wages_by_owner(fh, year).items():
        people.append(FicaPerson(owner=owner, wages=w, social_security=min(w, base) * ss_rate, medicare=w * med_rate))
    total_wages = sum(p.wages for p in people)
    additional = max(total_wages - add_threshold, 0.0) * add_rate
    total = sum(p.social_security + p.medicare for p in people) + additional
    return FicaResult(year=year, ss_wage_base=base, per_person=people, additional_medicare=additional, total=total)
