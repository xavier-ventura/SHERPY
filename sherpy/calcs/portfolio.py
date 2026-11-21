"""Cruce de las participaciones de la familia con cotizaciones en tiempo real (Python puro, sin red ni LLM).

Las participaciones salen de `investment-analysis.key_metrics.taxDragAnalysis.fundHoldingsInputs`
(tabla con cabecera: Account, Holding = ticker, Balance, Shares). Si no existe, se usa `positions`
(solo valor en USD, sin participaciones).
"""

from __future__ import annotations

from typing import Any

from .models import PortfolioLine, PortfolioLiveResult

CASH_HOLDINGS = {"cash (usd)", "cash", ""}


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def holdings_from_investment_analysis(key_metrics: dict[str, Any] | None,
                                      positions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Lista de {account, ticker, shares, recorded_value} sin efectivo."""
    out: list[dict[str, Any]] = []
    table = ((key_metrics or {}).get("taxDragAnalysis") or {}).get("fundHoldingsInputs") or []
    if len(table) > 1:
        header = [str(h).strip().lower() for h in table[0]]
        idx = {name: header.index(name) for name in ("account", "holding", "balance", "shares") if name in header}
        for row in table[1:]:
            ticker = str(row[idx["holding"]]).strip() if "holding" in idx else ""
            if ticker.lower() in CASH_HOLDINGS or " " in ticker:
                continue
            out.append({
                "account": str(row[idx["account"]]) if "account" in idx else "",
                "ticker": ticker.upper(),
                "shares": _num(row[idx["shares"]]) if "shares" in idx else None,
                "recorded_value": _num(row[idx["balance"]]) or 0.0 if "balance" in idx else 0.0,
            })
        if out:
            return out
    for p in positions or []:
        ticker = str(p.get("ticker") or "").strip()
        if not ticker or p.get("type") == "CASH":
            continue
        out.append({"account": p.get("account", ""), "ticker": ticker.upper(),
                    "shares": _num(p.get("shares") or p.get("quantity") or p.get("units")),
                    "recorded_value": _num(p.get("value")) or 0.0})
    return out


def calc_portfolio_live(holdings: list[dict[str, Any]], quotes: dict[str, dict[str, Any]]) -> PortfolioLiveResult:
    """Valor actual de cada posición = participaciones × precio actual (c); variación del día = participaciones × d.
    Las posiciones sin cotización o sin participaciones conservan el valor registrado por Sherpas."""
    lines: list[PortfolioLine] = []
    for h in holdings:
        q = quotes.get(h["ticker"]) or {}
        shares, recorded = h.get("shares"), float(h.get("recorded_value") or 0.0)
        price = _num(q.get("current")) if q.get("ok") else None
        line = PortfolioLine(account=h.get("account", ""), ticker=h["ticker"], shares=shares,
                             recorded_value=recorded, recorded_price=(recorded / shares) if shares else None,
                             live_price=price, change_pct_today=_num(q.get("change_pct")) if q.get("ok") else None,
                             live_value=None, change_vs_recorded=None, change_today=None,
                             priced=False, note=None if q.get("ok") else q.get("error", "Sin cotización."))
        if price is not None and shares:
            line.live_value = shares * price
            line.change_vs_recorded = line.live_value - recorded
            d = _num(q.get("change"))
            line.change_today = shares * d if d is not None else None
            line.priced = True
        elif price is not None:
            line.note = "Sin número de participaciones: no se puede valorar a precio actual."
        lines.append(line)

    priced = [ln for ln in lines if ln.priced]
    recorded_priced = sum(ln.recorded_value for ln in priced)
    live_priced = sum(ln.live_value or 0.0 for ln in priced)
    today = sum(ln.change_today or 0.0 for ln in priced)
    prev = live_priced - today
    return PortfolioLiveResult(
        lines=lines,
        tickers=sorted({ln.ticker for ln in lines}),
        unpriced_tickers=sorted({ln.ticker for ln in lines if not ln.priced}),
        recorded_value_total=sum(ln.recorded_value for ln in lines),
        recorded_value_priced=recorded_priced,
        live_value_priced=live_priced,
        change_vs_recorded=live_priced - recorded_priced,
        change_vs_recorded_pct=(live_priced / recorded_priced - 1) if recorded_priced else None,
        change_today=today,
        change_today_pct=(today / prev) if prev else None,
        live_value_total=live_priced + sum(ln.recorded_value for ln in lines if not ln.priced),
    )
