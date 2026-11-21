"""Resumen financiero personalizado de una familia, para enviar por email o WhatsApp.

El LLM redacta el contenido (estructurado); las cifras salen de los datos de Sherpas y de calc_* (nunca del LLM),
y el HTML / texto / mensaje de WhatsApp se montan aquí de forma determinista y segura (HTML escapado).
"""

from __future__ import annotations

import html
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from sherpy.agent.lexico import obtener_perfil_lexico_adultos
from sherpy.agent.news import perfil_para_noticias
from sherpy.data.source import CaseSource

Channel = Literal["email", "whatsapp"]
WHATSAPP_MAX_CHARS = 1500  # Twilio admite 1600 por mensaje
DISCLAIMER = ("Información educativa basada en datos sintéticos de Sherpas; no es asesoramiento financiero regulado.")


class Metric(BaseModel):
    label: str = Field(description="Nombre corto de la métrica, p. ej. 'Colchón de emergencia'.")
    value: str = Field(description="Valor tal como aparece en el perfil, p. ej. '3,2 meses' o '$30,000'.")


class SummaryContent(BaseModel):
    subject: str = Field(description="Asunto del email: claro y personal, sin emojis.")
    greeting: str = Field(description="Saludo con el nombre de los adultos del hogar.")
    overview: str = Field(description="2-3 frases sobre su salud financiera general.")
    key_metrics: list[Metric] = Field(description="4-6 métricas clave sacadas del perfil.")
    investments: str = Field(description="1-2 frases sobre el estado de sus inversiones.")
    recommendations: list[str] = Field(description="Máximo 3 acciones priorizadas, concretas y cortas.")
    closing: str = Field(description="Cierre breve y cercano.")


class GeneratedSummary(BaseModel):
    subject: str
    text: str
    html: str | None = None


SUMMARY_INSTRUCTIONS = """\
Eres SHERPY y redactas un RESUMEN FINANCIERO personal para una familia, que se le enviará por {canal}.
Recibes su PERFIL (datos sintéticos de Sherpas, importes en USD). Escribe en español.

Reglas:
- No hagas cuentas ni inventes cifras: usa solo los números que aparecen en el perfil (están en `totales`,
  `liquidez`, `deudas`, `inversiones`, `puntuacion`, `mejores_acciones`…), copiándolos tal cual.
- Los importes ya vienen formateados ("$218,500"): escríbelos exactamente así.
- Di "puntuación financiera", nunca "score".
- Métricas clave sugeridas: puntuación financiera, colchón de emergencia (meses), patrimonio neto, total invertido,
  deuda total y la deuda más cara, ahorro anual.
- Recomendaciones: máximo 3, priorizadas (fondo de emergencia → deuda cara → protección → jubilación → otros
  objetivos), apoyadas en `mejores_acciones`. Si la situación es delicada (banda critical o vulnerable), pocas,
  concretas y sin recomendar asumir más riesgo.
- Lenguaje de persona: sin nombres de campos, secciones (D4, score…), herramientas ni JSON. Traduce las etiquetas
  internas ("critical" → "delicada", "Growth" → "perfil de crecimiento").
- Texto plano en cada campo: sin Markdown, sin HTML y sin emojis (el formato lo pone el sistema).
{extra}"""

WHATSAPP_EXTRA = "- Es para WhatsApp en el móvil: frases muy cortas, 4 métricas como máximo."
EMAIL_EXTRA = "- Es un email: puede ser algo más completo, pero claro y fácil de escanear."


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _usd(x: Any) -> Any:
    n = _num(x)
    return f"${n:,.0f}" if n is not None else x


async def perfil_para_resumen(source: CaseSource, case_id: str) -> dict[str, Any]:
    """Perfil de noticias + totales calculados aquí (el LLM no suma) + mejores acciones del score."""
    perfil = await perfil_para_noticias(source, case_id)
    fh = await source.financial_health(case_id, "score", "key_metrics")
    km = fh["key_metrics"]
    net_worth = (km.get("netWorthStatement") or {}).get("netWorth") or {}
    annual = (km.get("savingsAndExpenses") or {}).get("annualSavings") or {}
    deudas = perfil.get("deudas", [])
    cara = max(deudas, key=lambda d: _num(d.get("tipo_interes")) or 0, default=None)
    ahorro = sum(_num(annual.get(k)) or 0 for k in ("currentAnnualSavingsAtRetirementAccounts",
                                                    "currentAnnualSavingsAtTaxableAccounts",
                                                    "currentAnnualSavingsAtHSA", "currentAnnualSavingsAt529Accounts"))
    perfil["totales"] = {
        "patrimonio_neto_usd": _num(net_worth.get("total")) if isinstance(net_worth, dict) else _num(net_worth),
        "total_invertido_usd": round(sum(i["valor_usd"] for i in perfil.get("inversiones", []))),
        "deuda_total_usd": round(sum(_num(d.get("saldo_usd")) or 0 for d in deudas)),
        "deuda_mas_cara": ({"nombre": cara.get("nombre"), "saldo_usd": cara.get("saldo_usd"),
                            "tipo_interes_pct": round((_num(cara.get("tipo_interes")) or 0) * 100, 1)}
                           if cara and _num(cara.get("tipo_interes")) else None),
        "ahorro_anual_usd": round(ahorro),
        "salario_anual_hogar_usd": round(sum(_num(s.get("importe")) or 0
                                             for s in perfil.get("salarios_anuales_usd", []))),
    }
    # Importes ya formateados ("$218,500"): el LLM los copia tal cual, sin redondear ni reescribir.
    perfil["totales"] = {k: (_usd(v) if k.endswith("_usd") else v) for k, v in perfil["totales"].items()}
    if isinstance(perfil["totales"].get("deuda_mas_cara"), dict):
        d = perfil["totales"]["deuda_mas_cara"]
        d["saldo_usd"] = _usd(d.get("saldo_usd"))
        d["tipo_interes_pct"] = f'{d["tipo_interes_pct"]}%'
    liq = perfil.get("liquidez") or {}
    if "efectivo_accesible_usd" in liq:
        liq["efectivo_accesible_usd"] = _usd(liq["efectivo_accesible_usd"])
    for d in perfil.get("deudas", []):
        d["saldo_usd"] = _usd(d.get("saldo_usd"))
        if _num(d.get("tipo_interes")) is not None:
            d["tipo_interes"] = f'{round(_num(d["tipo_interes"]) * 100, 1)}%'
    for i in perfil.get("inversiones", []):
        i["valor_usd"] = _usd(i.get("valor_usd"))
    perfil["mejores_acciones"] = [
        {"accion": m.get("action"), "esfuerzo": m.get("effort"),
         "puntuacion_antes": m.get("score_before"), "puntuacion_despues": m.get("score_after")}
        for m in (fh["score"].get("top_moves") or [])[:5]
    ]
    return perfil


# ---------- Render determinista ----------

_TAG_RE = re.compile(r"<[^>]+>")


def _plain(s: str) -> str:
    return " ".join(_TAG_RE.sub("", s or "").replace("**", "").split())


def render_text(c: SummaryContent, note: str | None) -> str:
    lines = [_plain(c.greeting), "", _plain(c.overview), "", "MÉTRICAS CLAVE"]
    lines += [f"- {_plain(m.label)}: {_plain(m.value)}" for m in c.key_metrics]
    lines += ["", "INVERSIONES", _plain(c.investments), "", "PRÓXIMOS PASOS"]
    lines += [f"{i}. {_plain(r)}" for i, r in enumerate(c.recommendations[:3], 1)]
    if note:
        lines += ["", f"Nota: {note.strip()}"]
    lines += ["", _plain(c.closing), "", DISCLAIMER]
    return "\n".join(lines)


def render_html(c: SummaryContent, note: str | None) -> str:
    e = lambda s: html.escape(_plain(s))  # noqa: E731
    metrics = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;color:#555">{e(m.label)}</td>'
        f'<td style="padding:6px 0;font-weight:600">{e(m.value)}</td></tr>' for m in c.key_metrics)
    recs = "".join(f'<li style="margin-bottom:6px">{e(r)}</li>' for r in c.recommendations[:3])
    note_html = (f'<p style="background:#f4f6f8;padding:10px 12px;border-radius:6px"><b>Nota:</b> '
                 f'{html.escape(note.strip())}</p>' if note else "")
    h2 = 'style="font-size:16px;margin:24px 0 8px;color:#1a3c5a"'
    return (
        '<!doctype html><html><body style="margin:0;background:#ffffff">'
        '<div style="max-width:600px;margin:0 auto;padding:24px;font-family:Arial,Helvetica,sans-serif;'
        'font-size:15px;line-height:1.5;color:#222">'
        f'<p>{e(c.greeting)}</p><p>{e(c.overview)}</p>'
        f'<h2 {h2}>Métricas clave</h2><table style="border-collapse:collapse">{metrics}</table>'
        f'<h2 {h2}>Inversiones</h2><p>{e(c.investments)}</p>'
        f'<h2 {h2}>Próximos pasos</h2><ol style="padding-left:20px">{recs}</ol>'
        f'{note_html}<p>{e(c.closing)}</p>'
        f'<p style="font-size:12px;color:#888;margin-top:24px">{html.escape(DISCLAIMER)}</p>'
        '</div></body></html>'
    )


def render_whatsapp(c: SummaryContent, note: str | None) -> str:
    lines = [f"*{_plain(c.subject)}*", "", _plain(c.greeting), _plain(c.overview), "", "*📊 Métricas clave*"]
    lines += [f"• {_plain(m.label)}: *{_plain(m.value)}*" for m in c.key_metrics[:4]]
    lines += ["", "*📈 Inversiones*", _plain(c.investments), "", "*✅ Próximos pasos*"]
    lines += [f"{i}. {_plain(r)}" for i, r in enumerate(c.recommendations[:3], 1)]
    if note:
        lines += ["", f"📝 {note.strip()}"]
    lines += ["", _plain(c.closing), "", f"_{DISCLAIMER}_"]
    text = "\n".join(lines)
    return text if len(text) <= WHATSAPP_MAX_CHARS else text[: WHATSAPP_MAX_CHARS - 1] + "…"


# ---------- Generación ----------

async def generar_resumen(source: CaseSource, case_id: str, channel: Channel, model: Model | None = None,
                          custom_note: str | None = None) -> GeneratedSummary:
    """Redacta el resumen con el LLM (con adaptación léxica por edad) y lo formatea para el canal."""
    import json

    if model is None:
        from sherpy.agent.agent import build_model  # import diferido: evita ciclos
        from sherpy.config import get_settings
        model = build_model(get_settings())

    perfil = await perfil_para_resumen(source, case_id)
    case = await source.get_case(case_id, "financial-health", "case")
    instructions = SUMMARY_INSTRUCTIONS.format(
        canal="WhatsApp" if channel == "whatsapp" else "email",
        extra=WHATSAPP_EXTRA if channel == "whatsapp" else EMAIL_EXTRA,
    ) + "\n" + obtener_perfil_lexico_adultos(case)
    agent: Agent[None, SummaryContent] = Agent(model, output_type=SummaryContent, instructions=instructions, retries=2)
    result = await agent.run("PERFIL DE LA FAMILIA:\n" + json.dumps(perfil, ensure_ascii=False, default=str))
    content = result.output
    note = (custom_note or "").strip() or None  # la nota va tal cual, no pasa por el LLM
    if channel == "whatsapp":
        return GeneratedSummary(subject=_plain(content.subject), text=render_whatsapp(content, note))
    return GeneratedSummary(subject=_plain(content.subject), text=render_text(content, note),
                            html=render_html(content, note))
