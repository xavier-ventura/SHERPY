"""Utilidades comunes para leer el `financial-health` y `legal-values` de Sherpas."""

from __future__ import annotations

from typing import Any

CASH_ACCOUNT_TYPES = {"Checking account", "Savings account"}
PRETAX_ACCOUNT_TYPES = {"HSA"}  # además de cualquier cuenta TAX_DEFERRED


def legal_value(legal: dict[str, Any], key: str, year: int | None = None) -> Any:
    """Devuelve `legal[key][year].value` (o `legal[key]` sin año) quitando el envoltorio `value`."""
    node = legal[key]
    if year is not None:
        y = str(year)
        if y not in node:
            raise ValueError(f"{key}: no hay valores para {year} (disponibles: {sorted(node)})")
        node = node[y]
    return node["value"] if isinstance(node, dict) and "value" in node else node


def legal_scalar(legal: dict[str, Any], key: str, field: str) -> float:
    return float(legal[key][field]["value"])


def wages_by_owner(fh: dict[str, Any], year: int) -> dict[str, float]:
    """Salarios (`SALARY_AND_WAGES`) activos en `year`, agrupados por titular."""
    out: dict[str, float] = {}
    for inc in fh["profile"]["incomes"]:
        if inc["type"] != "SALARY_AND_WAGES":
            continue
        if not (inc.get("starts", year) <= year <= inc.get("ends", year)):
            continue
        owner = ", ".join(inc.get("owner") or ["unknown"])
        out[owner] = out.get(owner, 0.0) + float(inc["yearly"])
    return out


def pretax_deferrals(fh: dict[str, Any]) -> float:
    """Aportaciones del cliente que reducen la base imponible federal (401k tradicional, HSA)."""
    total = 0.0
    for acc in fh["profile"]["accounts"]:
        if acc.get("tax_treatment") == "TAX_DEFERRED" or acc.get("account_type") in PRETAX_ACCOUNT_TYPES:
            total += float(acc.get("yearly_contribution_client") or 0)
    return total


def children(fh: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in fh["case"]["household"] if m.get("role") == "child"]


def find_by_name(items: list[dict[str, Any]], name: str, kind: str) -> dict[str, Any]:
    """Busca por nombre exacto (sin mayúsculas) y, si no, por coincidencia parcial única."""
    low = name.strip().lower()
    exact = [i for i in items if i["name"].lower() == low]
    if exact:
        return exact[0]
    partial = [i for i in items if low in i["name"].lower()]
    if len(partial) == 1:
        return partial[0]
    names = [i["name"] for i in items]
    raise ValueError(f"{kind} '{name}' no encontrado. Opciones: {names}")
