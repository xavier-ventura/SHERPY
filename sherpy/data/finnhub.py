"""Cotizaciones en tiempo real desde Finnhub (plan gratuito: 60 peticiones/min).

`consultar_cotizacion_finnhub` nunca lanza excepciones: devuelve `{"ok": False, "code", "error"}` si falta la
clave, falla la red o el ticker no tiene datos, para que el agente pueda seguir respondiendo.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from sherpy.config import get_settings

logger = logging.getLogger("sherpy.finnhub")

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
TIMEOUT_S = 6.0
CACHE_TTL_S = 60.0  # evita gastar el límite gratuito si se repite la pregunta
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _api_key() -> str | None:
    return os.getenv("FINNHUB_API_KEY") or get_settings().finnhub_api_key


def _error(symbol: str, code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "symbol": symbol, "code": code, "error": message}


async def consultar_cotizacion_finnhub(symbol: str, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Cotización de `symbol` en Finnhub: precio actual (c), variación del día (d, dp %), máximo (h), mínimo (l),
    apertura (o) y cierre anterior (pc)."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return _error(sym, "bad_symbol", "Símbolo vacío.")
    key = _api_key()
    if not key:
        return _error(sym, "no_key", "FINNHUB_API_KEY no está configurada: no hay cotizaciones en tiempo real.")

    hit = _cache.get(sym)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]

    params = {"symbol": sym, "token": key}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
                r = await c.get(FINNHUB_QUOTE_URL, params=params)
        else:
            r = await client.get(FINNHUB_QUOTE_URL, params=params, timeout=TIMEOUT_S)
        r.raise_for_status()
        q = r.json()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        # Nunca se registra la URL: lleva el token.
        logger.warning("Finnhub %s para %s", status, sym)
        if status == 429:
            return _error(sym, "rate_limit", "Finnhub ha alcanzado el límite de peticiones; prueba en un minuto.")
        if status == 401:
            return _error(sym, "bad_key", "Finnhub rechaza la clave (FINNHUB_API_KEY inválida).")
        if status == 403:
            return _error(sym, "no_access", f"El plan gratuito de Finnhub no cubre {sym} "
                                            "(p. ej. fondos de inversión): se usa el valor registrado.")
        return _error(sym, "upstream", f"Finnhub respondió con error {status}.")
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Finnhub sin respuesta para %s: %s", sym, type(e).__name__)
        return _error(sym, "network", "No se pudo contactar con Finnhub (red o tiempo de espera).")

    if not isinstance(q, dict) or (not q.get("c") and not q.get("pc")):
        return _error(sym, "no_data", f"Finnhub no tiene cotización para {sym} (ticker incorrecto o no cubierto).")

    t = q.get("t") or 0
    result = {
        "ok": True,
        "symbol": sym,
        "current": q.get("c"),        # c
        "change": q.get("d"),         # d
        "change_pct": q.get("dp"),    # dp (en %)
        "high": q.get("h"),           # h
        "low": q.get("l"),            # l
        "open": q.get("o"),           # o
        "prev_close": q.get("pc"),    # pc
        "as_of": datetime.fromtimestamp(t, UTC).isoformat() if t else None,
    }
    _cache[sym] = (time.monotonic(), result)
    return result
