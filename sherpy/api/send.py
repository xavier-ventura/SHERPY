"""POST /api/send-summary: resumen financiero personalizado por email o WhatsApp (con alternativa sin credenciales)."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from pydantic_ai.exceptions import FallbackExceptionGroup, ModelHTTPError, UnexpectedModelBehavior

from sherpy.agent.agent import NoModelConfigured
from sherpy.agent.summary import generar_resumen
from sherpy.notify import (SendError, enviar_email, enviar_whatsapp, limpiar_telefono, smtp_configured,
                           twilio_configured, whatsapp_link)

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,}$")


class SendSummaryRequest(BaseModel):
    case_id: str
    channel: Literal["email", "whatsapp"]
    recipient: str = Field(max_length=254)
    custom_note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _check_recipient(self) -> SendSummaryRequest:
        r = self.recipient.strip()
        if self.channel == "email" and not EMAIL_RE.match(r):
            raise ValueError("recipient debe ser un email válido.")
        if self.channel == "whatsapp" and not (r.startswith("+") and 8 <= len(limpiar_telefono(r)) <= 15):
            raise ValueError("recipient debe ser un teléfono con prefijo internacional, p. ej. +34600112233.")
        self.recipient = r
        return self


class SendSummaryResponse(BaseModel):
    status: Literal["sent", "link_generated", "preview_only"]
    channel: str
    recipient: str
    summary: str
    subject: str | None = None
    summary_html: str | None = None
    whatsapp_url: str | None = None
    message: str | None = None


@router.post("/api/send-summary", response_model=SendSummaryResponse)
async def send_summary(request: Request, body: SendSummaryRequest) -> SendSummaryResponse:
    source = request.app.state.source
    if body.case_id not in {c["id"] for c in await source.list_cases()}:
        raise HTTPException(404, f"Caso '{body.case_id}' no encontrado.")
    try:
        s = await generar_resumen(source, body.case_id, body.channel,
                                  getattr(request.app.state, "summary_model", None), body.custom_note)
    except NoModelConfigured as e:
        raise HTTPException(503, "No hay LLM configurado.") from e
    except (FallbackExceptionGroup, ModelHTTPError, UnexpectedModelBehavior) as e:
        status = 429 if "429" in str(e) else 502
        raise HTTPException(status, "El modelo de IA no pudo preparar el resumen; inténtalo en unos minutos.") from e

    base = {"channel": body.channel, "recipient": body.recipient, "summary": s.text, "subject": s.subject}

    if body.channel == "email":
        base["summary_html"] = s.html
        if not smtp_configured():
            return SendSummaryResponse(status="preview_only", **base,
                                       message="Credenciales SMTP no configuradas; resumen generado en vista previa")
        try:
            await enviar_email(body.recipient, s.subject, s.text, s.html)
        except SendError as e:
            return SendSummaryResponse(status="preview_only", **base, message=f"{e} Resumen en vista previa.")
        return SendSummaryResponse(status="sent", **base, message="Email enviado.")

    link = whatsapp_link(body.recipient, s.text)
    if not twilio_configured():
        return SendSummaryResponse(status="link_generated", **base, whatsapp_url=link,
                                   message="Credenciales de Twilio no configuradas; abre el enlace para enviarlo.")
    try:
        await enviar_whatsapp(body.recipient, s.text)
    except SendError as e:
        return SendSummaryResponse(status="link_generated", **base, whatsapp_url=link,
                                   message=f"{e} Abre el enlace para enviarlo.")
    return SendSummaryResponse(status="sent", **base, whatsapp_url=link, message="WhatsApp enviado.")
