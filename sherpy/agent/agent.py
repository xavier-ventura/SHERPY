"""Agente Pydantic AI: LLM (Gemini → Groq) + herramientas de contexto y de cálculo."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

import httpx
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from sherpy import calcs
from sherpy.agent.deps import AgentDeps
from sherpy.agent.lexico import obtener_perfil_lexico_adultos
from sherpy.agent.prompts import SYSTEM_PROMPT
from sherpy.calcs import models as m
from sherpy.config import Settings, get_settings
from sherpy.data.finnhub import consultar_cotizacion_finnhub

Part = Literal["financial-health", "investment-analysis", "legal-values", "persona", "signals"]
MAX_SECTION_CHARS = 60_000  # ~15k tokens: evita mandar bloques enormes al LLM


class NoModelConfigured(RuntimeError):
    pass


def build_model(settings: Settings) -> Model:
    """LLM de Sherpas (TEAM_KEY) como principal; Gemini y Groq como respaldo si hay claves.
    FallbackModel salta al siguiente ante 429 y otros errores de API."""
    from pydantic_ai.models.fallback import FallbackModel

    models: list[Model] = []
    if settings.team_key:
        from sherpy.agent.llm import build_sherpas_model

        models.append(build_sherpas_model(settings))
    if settings.google_api_key:
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        models.append(GoogleModel(settings.gemini_model, provider=GoogleProvider(api_key=settings.google_api_key)))
    if settings.groq_api_key:
        from pydantic_ai.models.groq import GroqModel
        from pydantic_ai.providers.groq import GroqProvider

        models.append(GroqModel(settings.groq_model, provider=GroqProvider(api_key=settings.groq_api_key)))
    if not models:
        raise NoModelConfigured("Configura TEAM_KEY (o GOOGLE_API_KEY / GROQ_API_KEY).")
    return models[0] if len(models) == 1 else FallbackModel(*models)


async def _fh(ctx: RunContext[AgentDeps], *sections: str) -> dict[str, Any]:
    return await ctx.deps.source.financial_health(ctx.deps.case_id, *sections)


def _wrap(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except (ValueError, KeyError) as e:
        raise ModelRetry(str(e)) from e


def build_agent(model: Model | None = None) -> Agent[AgentDeps, str]:
    agent: Agent[AgentDeps, str] = Agent(
        model or build_model(get_settings()),
        deps_type=AgentDeps,
        instructions=SYSTEM_PROMPT,
        retries=2,
    )

    @agent.instructions
    def active_case(ctx: RunContext[AgentDeps]) -> str:
        return f"Caso activo: `{ctx.deps.case_id}`."

    @agent.instructions
    async def estilo_lexico(ctx: RunContext[AgentDeps]) -> str:
        """Tono según la edad de los adultos (> 18) del hogar; "" si no hay datos (comportamiento estándar)."""
        if not ctx.deps.case_id:
            return ""
        try:
            case = await ctx.deps.source.get_case(ctx.deps.case_id, "financial-health", "case")
        except Exception:
            return ""
        return obtener_perfil_lexico_adultos(case)

    # ---------- Contexto ----------

    @agent.tool
    async def list_cases(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
        """Lista los 4 casos ficticios de Sherpas: id, hogar, ciudad, score y banda."""
        return await ctx.deps.source.list_cases()

    @agent.tool
    async def get_case(ctx: RunContext[AgentDeps], source: Literal["local", "mcp"], part: Part | None = None,
                       section: str | None = None) -> dict[str, Any]:
        """Lee datos del caso activo. Decide la fuente en cada consulta según lo que sea más rápido y útil.

        Args:
            source: "local" = copia local de los datos de Sherpas, sin red, instantánea: úsala para preguntas
                generales, cabeceras, varias secciones o datos ya consultados. "mcp" = API oficial de Sherpas:
                úsala para una pregunta concreta sobre un dato puntual (una sección o un título de persona) o si
                "local" no lo tiene. Si la fuente elegida falla, se usa la otra automáticamente.
            part: parte del caso. Sin `part` devuelve la cabecera con secciones y tamaños en tokens.
            section: clave de primer nivel (JSON) o título "## " (persona, p. ej. "D5"). Obligatoria para
                financial-health e investment-analysis, que son muy grandes.

        Returns:
            {"source": origen real ("cache" | "local" | "mcp"), "data": datos}
        """
        if part in ("financial-health", "investment-analysis") and not section:
            raise ModelRetry(f"'{part}' es muy grande: pide una sección (llama a get_case() sin part para verlas).")
        try:
            data, origin = await ctx.deps.source.get_case_with_origin(ctx.deps.case_id, part, section, prefer=source)
        except KeyError as e:
            raise ModelRetry(str(e)) from e
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        if len(text) > MAX_SECTION_CHARS:
            if isinstance(data, dict):
                data = {"too_large": True, "approx_tokens": len(text) // 4, "subkeys": list(data.keys()),
                        "hint": "Sección demasiado grande; usa las herramientas calc_* o pregunta por una subclave."}
            else:
                data = text[:MAX_SECTION_CHARS] + "\n…[truncado]"
        return {"source": origin, "data": data}

    # ---------- Cálculo ----------

    @agent.tool
    async def calc_federal_tax(ctx: RunContext[AgentDeps], year: int = 2026) -> m.FederalTaxResult:
        """Impuesto federal sobre la renta del hogar en `year` (2026 o 2027): salarios − aportaciones pre-tax
        − deducción estándar → tramos, menos el crédito por hijo con phase-out. Incluye tipo marginal y efectivo."""
        fh = await _fh(ctx, "case", "profile")
        legal = await ctx.deps.source.legal(ctx.deps.case_id)
        return _wrap(calcs.calc_federal_tax, fh, legal, year)

    @agent.tool
    async def calc_fica(ctx: RunContext[AgentDeps], year: int = 2026) -> m.FicaResult:
        """FICA del hogar en `year`: Seguridad Social hasta el wage base, Medicare y Additional Medicare."""
        fh = await _fh(ctx, "case", "profile")
        legal = await ctx.deps.source.legal(ctx.deps.case_id)
        return _wrap(calcs.calc_fica, fh, legal, year)

    @agent.tool
    async def calc_emergency_fund(ctx: RunContext[AgentDeps], target_months: float = 6.0) -> m.EmergencyFundResult:
        """Meses de gastos que cubre el efectivo accesible (checking + savings + money market) frente a las
        salidas mensuales (gastos + cuotas de deuda), el objetivo en USD para `target_months` y el hueco."""
        fh = await _fh(ctx, "profile")
        km = await _fh(ctx, "key_metrics")
        mm = (km["key_metrics"].get("protection", {}).get("emergencyFund", {})
              .get("cashAndExpensesOverview", {}).get("totalMoneyMarketAmount", 0.0))
        return _wrap(calcs.calc_emergency_fund, fh, float(mm or 0), target_months)

    @agent.tool
    async def calc_debt_payoff(ctx: RunContext[AgentDeps], debt_name: str, extra_monthly: float = 0.0) -> m.DebtPayoffResult:
        """Meses e intereses totales para liquidar la deuda `debt_name` (nombre en profile.liabilities) pagando
        su cuota + `extra_monthly` USD, comparado con pagar solo la cuota actual."""
        fh = await _fh(ctx, "profile")
        return _wrap(calcs.calc_debt_payoff, fh, debt_name, extra_monthly)

    @agent.tool
    async def calc_goal_gap(ctx: RunContext[AgentDeps], goal: str, assumed_return: float = 0.05,
                            current_savings: float | None = None) -> m.GoalGapResult:
        """Hueco de un objetivo (nombre en profile.goals: p. ej. 'College Oliver', 'Purchase a home', 'Retirement')
        y ahorro mensual extra para cerrarlo. `assumed_return` anual en decimal (supuesto: dilo al usuario).
        `current_savings` solo si el usuario indica ahorro ya asignado a ese objetivo."""
        fh = await _fh(ctx, "case", "profile", "key_metrics")
        return _wrap(calcs.calc_goal_gap, fh, goal, assumed_return, current_savings)

    @agent.tool
    async def calc_market_shock(ctx: RunContext[AgentDeps], drop_pct: float) -> m.MarketShockResult:
        """Pérdida en USD si la cartera invertible cae `drop_pct` (decimal: 0.2 = −20 %), por cuenta y en meses
        de salidas mensuales. Para caídas históricas reales mira investment-analysis.key_metrics.performanceAnalysis."""
        fh = await _fh(ctx, "profile")
        positions = await ctx.deps.source.get_case(ctx.deps.case_id, "investment-analysis", "positions")
        ef = calcs.calc_emergency_fund(fh)
        return _wrap(calcs.calc_market_shock, fh, drop_pct, positions, ef.monthly_outflows)

    # ---------- Mercado en tiempo real (Finnhub) ----------

    @agent.tool_plain
    async def get_quote(symbol: str) -> dict[str, Any]:
        """Cotización en tiempo real de UNA acción o ETF (Finnhub): precio actual, variación del día (absoluta y %),
        máximo, mínimo, apertura y cierre anterior. Úsala SOLO si preguntan por el precio / cómo va hoy un valor
        concreto. `symbol` es el ticker oficial de EE. UU.: dedúcelo del nombre (Apple → AAPL, Nvidia → NVDA,
        Microsoft → MSFT, S&P 500 → SPY, Nasdaq 100 → QQQ). NO la uses para preguntas teóricas ni de gastos,
        ingresos, deudas o ahorro. Si devuelve ok=false, explica el motivo sin inventar el precio."""
        return await consultar_cotizacion_finnhub(symbol)

    @agent.tool
    async def get_portfolio_live(ctx: RunContext[AgentDeps]) -> m.PortfolioLiveResult:
        """Valor ACTUAL de las inversiones de la familia activa: toma sus participaciones (tickers y nº de
        participaciones de investment-analysis), consulta Finnhub para cada ticker y calcula participaciones ×
        precio actual, la variación de hoy y la diferencia con el valor registrado por Sherpas. Úsala cuando
        pregunten por "mis inversiones", "mi cartera", "cómo van mis acciones/fondos" o el valor actual de un activo
        que tienen. Las posiciones sin cotización (p. ej. fondos de inversión que Finnhub no cubre) mantienen el
        valor registrado y aparecen en `unpriced_tickers`. El efectivo no se incluye."""
        key_metrics = await ctx.deps.source.get_case(ctx.deps.case_id, "investment-analysis", "key_metrics")
        positions = await ctx.deps.source.get_case(ctx.deps.case_id, "investment-analysis", "positions")
        holdings = calcs.holdings_from_investment_analysis(key_metrics, positions)
        tickers = sorted({h["ticker"] for h in holdings})
        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(*(consultar_cotizacion_finnhub(t, client) for t in tickers))
        return calcs.calc_portfolio_live(holdings, dict(zip(tickers, results, strict=True)))

    # ---------- Noticias personalizadas ----------

    @agent.tool
    async def get_personal_news(ctx: RunContext[AgentDeps], max_items: int = 5) -> list[dict[str, Any]]:
        """Últimas noticias financieras REALES relevantes para la familia activa (de sus ETFs/acciones, tipos de
        interés, inflación, vivienda, universidad, jubilación…), cada una con por qué les afecta, fuente, enlace y
        fecha. Úsala SOLO si el usuario pide noticias explícitamente ("últimas noticias de mis inversiones",
        "noticias financieras para mí"). Tarda unos segundos. Si falla, dilo sin inventar noticias."""
        from sherpy.agent.news import NewsUnavailable, generar_noticias

        try:
            feed = await generar_noticias(ctx.deps.source, ctx.deps.case_id, ctx.model, max_items=max(1, min(max_items, 10)))
        except NewsUnavailable as e:
            raise ModelRetry(str(e)) from e
        return [i.model_dump(exclude={"image"}) for i in feed.items]

    return agent
