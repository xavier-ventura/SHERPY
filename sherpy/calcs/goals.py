"""Hueco de objetivos: universidad, compras (casa/vehículo) y jubilación."""

from __future__ import annotations

from typing import Any

from ._helpers import find_by_name
from .models import GoalGapResult
from .tax import PLAN_YEAR

DEFAULT_RETURN = 0.05
EDUCATION_ACCOUNT_MARKERS = ("529", "UGMA", "UTMA", "Custodial")


def _fv(present: float, annual_contrib: float, rate: float, years: int) -> float:
    """Valor futuro de un saldo más aportaciones mensuales constantes."""
    months = max(years, 0) * 12
    r = rate / 12
    if r == 0:
        return present + annual_contrib * years
    growth = (1 + r) ** months
    return present * growth + (annual_contrib / 12) * (growth - 1) / r


def _monthly_needed(gap: float, rate: float, years: int) -> float | None:
    """Ahorro mensual adicional para reunir `gap` en `years` años."""
    if gap <= 0:
        return 0.0
    months = years * 12
    if months <= 0:
        return None
    r = rate / 12
    if r == 0:
        return gap / months
    return gap * r / ((1 + r) ** months - 1)


def _education_funding(fh: dict[str, Any], goal: dict[str, Any]) -> tuple[float, float, list[str]]:
    """Saldo y aportación anual destinados a un objetivo de universidad.

    Cuentas 529/UGMA cuyo nombre incluye el del hijo → ese hijo. El resto se reparte a partes
    iguales entre todos los objetivos de universidad.
    """
    accounts = [a for a in fh["profile"]["accounts"]
                if any(m.lower() in (a["account_type"] + " " + a["name"]).lower() for m in EDUCATION_ACCOUNT_MARKERS)]
    edu_goals = [g for g in fh["profile"]["goals"] if g["type"] == "EDUCATION"]
    child = goal["name"].split()[-1].lower()
    other_children = {g["name"].split()[-1].lower() for g in edu_goals} - {child}
    bal = contrib = 0.0
    notes: list[str] = []
    for a in accounts:
        name = a["name"].lower()
        if child in name:
            share = 1.0
        elif any(o in name for o in other_children):
            continue
        else:
            share = 1.0 / max(len(edu_goals), 1)
        bal += float(a["balance"]) * share
        contrib += float(a.get("yearly_contribution_total") or 0) * share
        if share < 1:
            notes.append(f"'{a['name']}' se reparte a partes iguales entre {len(edu_goals)} objetivos de universidad.")
    return bal, contrib, notes


def calc_goal_gap(fh: dict[str, Any], goal_name: str, assumed_return: float = DEFAULT_RETURN,
                  current_savings: float | None = None) -> GoalGapResult:
    """Hueco de un objetivo del caso (por nombre, p. ej. 'College Oliver', 'Purchase a home', 'Retirement').

    - EDUCATION: coste total (suma de `yearly_costs`) frente a 529/UGMA proyectados al año de inicio.
    - Compras (REAL_ESTATE_PURCHASE, VEHICLE_PURCHASE…): pagos únicos del año de compra (entrada +
      gastos de cierre) frente a `current_savings` (0 si no se indica) proyectado.
    - RETIREMENT: usa las métricas ya calculadas por Sherpas (`key_metrics.savingsAndExpenses`).
    """
    goal = find_by_name(fh["profile"]["goals"], goal_name, "Objetivo")
    gtype = goal["type"]
    notes: list[str] = []

    if gtype == "RETIREMENT":
        km = fh.get("key_metrics", {}).get("savingsAndExpenses", {}).get("annualSavings", {})
        if not km:
            raise ValueError("Faltan key_metrics.savingsAndExpenses para calcular la jubilación.")
        gap = float(km.get("gapInRetirementAccountsAtRetirementAge") or 0)
        extra_annual = float(km.get("requiredRetirementExtraAnnualSavings") or 0)
        ret_age = fh.get("key_metrics", {}).get("savingsAndExpenses", {}).get("goals", {}).get("clientRetirementAge")
        client = next(m for m in fh["case"]["household"] if m["role"] == "client")
        target_year = PLAN_YEAR + (int(ret_age) - int(client["age"])) if ret_age else None
        return GoalGapResult(
            goal_name=goal["name"], goal_type=gtype, target_year=target_year,
            years_to_goal=(target_year - PLAN_YEAR) if target_year else None,
            total_cost=None, current_savings=0.0,
            annual_contribution=float(km.get("currentAnnualSavingsAtRetirementAccounts") or 0),
            assumed_return=0.0, projected_savings=None, gap=gap,
            required_extra_monthly=extra_annual / 12,
            source="key_metrics.savingsAndExpenses.annualSavings (calculado por Sherpas)",
            notes=["Hueco en cuentas de jubilación a la edad de retiro y ahorro extra anual requerido, según Sherpas."],
        )

    costs = goal.get("yearly_costs") or []
    if gtype == "EDUCATION":
        target_year = int(goal.get("start_year") or min(c["from_year"] for c in costs))
        total = sum(float(c["yearly"]) * (c["to_year"] - c["from_year"] + 1) for c in costs)
        saved, contrib, notes = _education_funding(fh, goal)
        source = "profile.goals + cuentas 529/UGMA"
    elif "planned_purchase" in goal or gtype.endswith("_PURCHASE"):
        target_year = int(goal.get("start_year") or goal["planned_purchase"]["year"])
        one_time = [c for c in costs if c["from_year"] == c["to_year"] == target_year]
        total = sum(float(c["yearly"]) for c in one_time)
        if not one_time and "planned_loan" in goal:
            total = float(goal["planned_purchase"]["value_at_purchase"]) - float(goal["planned_loan"]["amount"])
        saved, contrib = float(current_savings or 0), 0.0
        if current_savings is None:
            notes.append("No hay ahorro asignado a este objetivo en los datos; se asume 0.")
        notes.append("Coste = pagos únicos del año de compra: " + ", ".join(c["name"] for c in one_time))
        source = "profile.goals.yearly_costs"
    else:
        target_year = int(goal.get("start_year") or (min(c["from_year"] for c in costs) if costs else PLAN_YEAR))
        total = sum(float(c["yearly"]) * (c["to_year"] - c["from_year"] + 1) for c in costs)
        saved, contrib = float(current_savings or 0), 0.0
        notes.append("Objetivo recurrente: el coste total es la suma de todos los años; normalmente se paga del flujo de caja.")
        source = "profile.goals.yearly_costs"

    if current_savings is not None and gtype == "EDUCATION":
        saved = current_savings
    years = max(target_year - PLAN_YEAR, 0)
    projected = _fv(saved, contrib, assumed_return, years)
    gap = max(total - projected, 0.0)
    return GoalGapResult(
        goal_name=goal["name"], goal_type=gtype, target_year=target_year, years_to_goal=years,
        total_cost=total, current_savings=saved, annual_contribution=contrib,
        assumed_return=assumed_return, projected_savings=projected, gap=gap,
        required_extra_monthly=_monthly_needed(gap, assumed_return, years),
        source=source, notes=notes,
    )
