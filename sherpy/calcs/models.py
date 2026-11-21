"""Modelos de resultado de las funciones calc_*. Importes en USD (float), tasas en decimal."""

from __future__ import annotations

from pydantic import BaseModel


class BracketSlice(BaseModel):
    rate: float
    taxable_in_bracket: float
    tax: float


class FederalTaxResult(BaseModel):
    year: int
    filing_status: str
    wages: float
    pretax_deferrals: float
    standard_deduction: float
    taxable_income: float
    brackets: list[BracketSlice]
    tax_before_credits: float
    qualifying_children: int
    child_tax_credit: float
    tax_after_credits: float
    marginal_rate: float
    effective_rate: float


class FicaPerson(BaseModel):
    owner: str
    wages: float
    social_security: float
    medicare: float


class FicaResult(BaseModel):
    year: int
    ss_wage_base: float
    per_person: list[FicaPerson]
    additional_medicare: float
    total: float


class EmergencyFundResult(BaseModel):
    bank_cash: float
    money_market: float
    accessible_cash: float
    monthly_expenses: float
    monthly_debt_payments: float
    monthly_outflows: float
    months_covered: float
    target_months: float
    target_amount: float
    shortfall: float


class DebtPayoffResult(BaseModel):
    debt_name: str
    balance: float
    interest_rate: float
    monthly_payment: float
    extra_monthly: float
    months: int | None
    total_interest: float | None
    baseline_months: int | None
    baseline_total_interest: float | None
    months_saved: int | None
    interest_saved: float | None
    pays_off: bool
    note: str | None = None


class GoalGapResult(BaseModel):
    goal_name: str
    goal_type: str
    target_year: int | None
    years_to_goal: int | None
    total_cost: float | None
    current_savings: float
    annual_contribution: float
    assumed_return: float
    projected_savings: float | None
    gap: float | None
    required_extra_monthly: float | None
    source: str
    notes: list[str] = []


class AccountShock(BaseModel):
    account: str
    value: float
    loss: float


class MarketShockResult(BaseModel):
    drop_pct: float
    invested_total: float
    loss: float
    value_after: float
    per_account: list[AccountShock]
    loss_in_months_of_outflows: float | None
