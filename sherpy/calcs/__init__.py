"""Cálculos financieros deterministas (Python puro, sin LLM)."""

from .cashflow import calc_emergency_fund
from .debt import calc_debt_payoff
from .goals import calc_goal_gap
from .market import calc_market_shock
from .portfolio import calc_portfolio_live, holdings_from_investment_analysis
from .tax import PLAN_YEAR, calc_federal_tax, calc_fica

__all__ = [
    "PLAN_YEAR",
    "calc_debt_payoff",
    "calc_emergency_fund",
    "calc_federal_tax",
    "calc_fica",
    "calc_goal_gap",
    "calc_market_shock",
    "calc_portfolio_live",
    "holdings_from_investment_analysis",
]
