"""Noticias financieras personalizadas para una familia.

Flujo: perfil compacto del caso (datos de Sherpas + calc) → noticias de Finnhub (de sus ETFs/acciones + generales)
→ el LLM elige las relevantes y explica por qué les afectan → JSON listo para el frontend.
El LLM solo elige y redacta: fuente, URL y fecha salen siempre de Finnhub (nunca se inventan).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from sherpy import calcs
from sherpy.agent.lexico import obtener_perfil_lexico_adultos
from sherpy.data.finnhub import obtener_noticias_empresa_finnhub, obtener_noticias_generales_finnhub
from sherpy.data.source import CaseSource

MAX_TICKERS = 6          # límite gratuito de Finnhub: 60 peticiones/min
NEWS_PER_TICKER = 5
GENERAL_NEWS = 15
FEED_CACHE_TTL_S = 900.0  # 15 min: entrar en la sección de noticias no relanza el LLM cada vez
_feed_cache: dict[tuple[str, str, int], tuple[float, NewsFeed]] = {}


class NewsUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------- Modelos de salida (lo que recibe el frontend) ----------

class NewsItem(BaseModel):
    headline: str
    summary: str
    why_it_matters: str
    related_topic_or_ticker: str
    source: str
    url: str
    datetime: str | None
    image: str | None = None


class NewsFeed(BaseModel):
    case_id: str
    generated_at: str
    language: str
    items: list[NewsItem]
    tickers_consulted: list[str]


class _Pick(BaseModel):
    id: str = Field(description="id EXACTO de la noticia elegida (campo `id` de la lista).")
    headline: str = Field(description="Titular claro, en el idioma pedido.")
    summary: str = Field(description="Resumen breve (1-2 frases), en el idioma pedido.")
    why_it_matters: str = Field(description="Por qué afecta a ESTA familia, con datos concretos de su perfil.")
    related_topic_or_ticker: str = Field(description='Ticker (p. ej. "VTI") o tema (p. ej. "Tipos de interés").')


_TAG_RE = re.compile(r"</?[A-Za-z_][\w-]*>")


def _clean(text: str) -> str:
    """Quita restos de etiquetas que a veces deja el modelo (p. ej. `</related_topic_or_ticker>`)."""
    return " ".join(_TAG_RE.sub("", text or "").split())


class _Selection(BaseModel):
    items: list[_Pick]


NEWS_INSTRUCTIONS = """\
Eres el analista de noticias de SHERPY. Recibes el PERFIL de una familia (datos sintéticos de Sherpas) y una lista de
NOTICIAS reales de Finnhub. Elige las más relevantes para ESE hogar y explica por qué le afectan.

Criterios de relevancia (de más a menos):
1. Noticias de los ETFs/acciones que la familia tiene (campo `inversiones`), sobre todo los de más peso.
2. Tipos de interés y crédito si tienen hipoteca, tarjetas u otros préstamos (cita la deuda y su tipo).
3. Inflación, rentabilidad del efectivo y bonos si tienen mucha liquidez o bonos/TIPS en cartera.
4. Mercado de vivienda e hipotecas si planean comprar casa; coste de la universidad si hay hijos cerca de ella;
   jubilación y Seguridad Social según edades y edad de jubilación.
5. Impuestos (federales de EE. UU.), aranceles y caídas de mercado que toquen su cartera o sus eventos (`eventos`).
Descarta noticias sin relación con el perfil (deportes, viajes, política sin efecto financiero, empresas que no tienen).

Reglas:
- `id` debe ser exactamente uno de la lista. No inventes noticias, fuentes, fechas ni cifras.
- `why_it_matters`: 1-3 frases que conecten la noticia con datos CONCRETOS de su perfil (qué tienen, cuánto, qué
  planean). No hagas cuentas nuevas: usa solo las cifras que aparecen en el perfil o en la noticia.
- Si es relevante para su perfil de riesgo o puntuación, adáptalo (banda critical/vulnerable: prudencia, sin
  recomendar más riesgo). Es información educativa, no asesoramiento: no des órdenes de compra o venta.
- Lenguaje de persona, sin detalles técnicos del sistema: nada de nombres de campos, secciones (D4, score…),
  herramientas ni JSON. Fechas en lenguaje natural.
- No copies etiquetas internas en inglés ni entre comillas ('critical', 'vulnerable', 'Growth', 'Moderate'…):
  tradúcelas a lenguaje normal ("su situación financiera es delicada", "perfil de inversión de crecimiento").
- Texto plano: sin etiquetas XML/HTML ni Markdown.
- Ordena de más a menos relevante. Sin duplicados del mismo hecho.
- Procura variedad: además de las noticias de su cartera, si hay alguna relevante de tipos de interés, inflación,
  vivienda, impuestos o empleo que conecte con sus deudas, liquidez u objetivos, incluye al menos una.
"""


# ---------- Perfil del caso ----------

def _persona_sections(md: str, prefixes: tuple[str, ...], max_chars: int = 500) -> dict[str, str]:
    out: dict[str, str] = {}
    for block in md.split("\n## ")[1:]:
        title, _, body = block.partition("\n")
        if title.startswith(prefixes):
            out[title.strip()] = " ".join(body.split())[:max_chars]
    return out


async def perfil_para_noticias(source: CaseSource, case_id: str) -> dict[str, Any]:
    """Perfil compacto del hogar con todo lo que sirve para elegir noticias."""
    fh = await source.financial_health(case_id, "case", "profile", "score", "key_metrics")
    positions = await source.get_case(case_id, "investment-analysis", "positions")
    signals = await source.get_case(case_id, "signals")
    try:
        persona = await source.get_case(case_id, "persona")
    except Exception:
        persona = ""
    case, profile, score = fh["case"], fh["profile"], fh["score"]

    inversiones: dict[str, dict[str, Any]] = {}
    for p in positions or []:
        t = str(p.get("ticker") or "").upper()
        if not t or p.get("type") == "CASH":
            continue
        inv = inversiones.setdefault(t, {"ticker": t, "nombre": p.get("name"), "tipo": p.get("type"),
                                         "categoria": p.get("category"), "valor_usd": 0.0})
        inv["valor_usd"] += float(p.get("value") or 0)
    mm = (fh["key_metrics"].get("protection", {}).get("emergencyFund", {})
          .get("cashAndExpensesOverview", {}).get("totalMoneyMarketAmount", 0.0))
    try:
        ef = calcs.calc_emergency_fund(fh, float(mm or 0))
        liquidez = {"efectivo_accesible_usd": round(ef.accessible_cash), "meses_cubiertos": round(ef.months_covered, 1)}
    except (KeyError, ValueError):
        liquidez = {}

    return {
        "ubicacion": f"{case.get('city')}, {case.get('state')}",
        "estado_fiscal": case.get("tax_filing_status"),
        "hogar": [{"nombre": m.get("first_name"), "rol": m.get("role"), "edad": m.get("age"),
                   "perfil_riesgo": m.get("risk_profile"), "edad_jubilacion": m.get("retirement_age"),
                   "empleo": m.get("employment")} for m in case.get("household", [])],
        "salarios_anuales_usd": [{"titular": ", ".join(i.get("owner") or []), "importe": i["yearly"]}
                                 for i in profile.get("incomes", []) if i.get("type") == "SALARY_AND_WAGES"],
        "deudas": [{"nombre": d.get("name"), "tipo": d.get("type") or d.get("liability_type"),
                    "saldo_usd": d.get("balance"), "tipo_interes": d.get("interest_rate")}
                   for d in profile.get("liabilities", []) if d.get("balance")],
        "liquidez": liquidez,
        "inversiones": sorted(inversiones.values(), key=lambda x: -x["valor_usd"]),
        "seguros": [{"tipo": s.get("insurance_type") or s.get("type"), "cobertura": s.get("coverage")}
                    for s in profile.get("insurance", [])],
        "objetivos": [{"nombre": g.get("name"), "tipo": g.get("type")} for g in profile.get("goals", [])],
        "puntuacion": {"valor": score.get("published_score"), "banda": score.get("band"),
                       "pilares": {p["pillar"]: p.get("score") for p in score.get("pillars", [])}},
        "eventos": [{"fecha": s.get("date"), "tipo": s.get("moment"), "titular": s.get("headline")}
                    for s in (signals or {}).get("signals", [])],
        "persona": _persona_sections(persona if isinstance(persona, str) else "", ("D1", "D4", "D6")),
    }


# ---------- Noticias crudas ----------

async def recopilar_noticias(perfil: dict[str, Any], dias: int = 7) -> tuple[list[dict[str, Any]], list[str]]:
    """Noticias de los ETFs/acciones con más peso (los fondos de inversión no los cubre Finnhub gratis)
    + noticias generales de mercado. Sin duplicados; cada una con un `id` corto."""
    tickers = [i["ticker"] for i in perfil.get("inversiones", []) if i.get("tipo") in ("ETF", "STOCK", "EQUITY")]
    tickers = tickers[:MAX_TICKERS]
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(obtener_noticias_empresa_finnhub(t, dias, NEWS_PER_TICKER, client) for t in tickers),
            obtener_noticias_generales_finnhub("general", GENERAL_NEWS, client),
        )
    if all(not r["ok"] and r.get("code") == "no_key" for r in results):
        raise NewsUnavailable("no_key", "FINNHUB_API_KEY no está configurada: no hay noticias.")
    seen: set[str] = set()
    raw: list[dict[str, Any]] = []
    for r in results:
        for n in r.get("items", []) if r.get("ok") else []:
            if n["url"] in seen:
                continue
            seen.add(n["url"])
            raw.append({**n, "id": f"n{len(raw) + 1}"})
    return raw, tickers


# ---------- Selección y explicación con el LLM ----------

async def generar_noticias(source: CaseSource, case_id: str, model: Model | None = None, language: str = "es",
                           max_items: int = 6, use_cache: bool = True) -> NewsFeed:
    """Noticias relevantes para `case_id`, explicadas para esa familia y con su adaptación léxica por edad."""
    key = (case_id, language, max_items)
    hit = _feed_cache.get(key)
    if use_cache and hit and time.monotonic() - hit[0] < FEED_CACHE_TTL_S:
        return hit[1]

    perfil = await perfil_para_noticias(source, case_id)
    raw, tickers = await recopilar_noticias(perfil)
    now = datetime.now(UTC).isoformat()
    if not raw:
        return NewsFeed(case_id=case_id, generated_at=now, language=language, items=[], tickers_consulted=tickers)

    if model is None:
        from sherpy.agent.agent import build_model  # import diferido: agent.py importa este módulo
        from sherpy.config import get_settings
        model = build_model(get_settings())

    case = await source.get_case(case_id, "financial-health", "case")
    instructions = NEWS_INSTRUCTIONS + "\n" + obtener_perfil_lexico_adultos(case)
    agent: Agent[None, _Selection] = Agent(model, output_type=_Selection, instructions=instructions, retries=2)
    prompt = (
        f"Idioma de la respuesta: {language}. Elige como máximo {max_items} noticias.\n\n"
        f"PERFIL DE LA FAMILIA:\n{json.dumps(perfil, ensure_ascii=False, default=str)}\n\n"
        "NOTICIAS:\n" + json.dumps([{k: n[k] for k in ("id", "headline", "summary", "source", "datetime", "related")}
                                    for n in raw], ensure_ascii=False)
    )
    result = await agent.run(prompt)

    by_id = {n["id"]: n for n in raw}
    items: list[NewsItem] = []
    used: set[str] = set()
    for p in result.output.items:
        n = by_id.get(p.id.strip())
        if not n or p.id in used:
            continue  # id inventado o repetido: se descarta
        used.add(p.id)
        items.append(NewsItem(headline=_clean(p.headline), summary=_clean(p.summary),
                              why_it_matters=_clean(p.why_it_matters),
                              related_topic_or_ticker=_clean(p.related_topic_or_ticker), source=n["source"],
                              url=n["url"], datetime=n["datetime"], image=n.get("image")))
        if len(items) >= max_items:
            break
    feed = NewsFeed(case_id=case_id, generated_at=now, language=language, items=items, tickers_consulted=tickers)
    _feed_cache[key] = (time.monotonic(), feed)
    return feed
