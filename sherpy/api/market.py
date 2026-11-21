"""Endpoint de cotizaciones en tiempo real (Finnhub)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from sherpy.data.finnhub import consultar_cotizacion_finnhub

router = APIRouter()

ERROR_STATUS = {"bad_symbol": 422, "no_key": 503, "no_data": 404, "rate_limit": 429, "no_access": 403,
                "bad_key": 502, "upstream": 502, "network": 504}


@router.get("/api/quote/{symbol}")
async def quote(symbol: str) -> dict[str, Any]:
    """Cotización de `symbol`: {symbol, current, change, change_pct, high, low, open, prev_close, as_of}."""
    q = await consultar_cotizacion_finnhub(symbol)
    if not q["ok"]:
        raise HTTPException(ERROR_STATUS.get(q["code"], 502), q["error"])
    return q
