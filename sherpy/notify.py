"""Envío de mensajes: email (smtplib) y WhatsApp (API REST de Twilio), con enlace wa.me como alternativa.

Las credenciales se leen de `os.getenv` y, si no están, de `.env` (Settings). Nunca se registran.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote

import httpx

from sherpy.config import get_settings

logger = logging.getLogger("sherpy.notify")

TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
TIMEOUT_S = 15.0


class SendError(RuntimeError):
    """Fallo al enviar (credenciales rechazadas, red, proveedor). El mensaje es apto para el usuario."""


def _env(name: str) -> str | None:
    return os.getenv(name) or getattr(get_settings(), name.lower(), None) or None


# ---------- Email ----------

def smtp_configured() -> bool:
    return all(_env(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))


def _send_email_sync(to: str, subject: str, text: str, html: str | None) -> None:
    host, user, password = _env("SMTP_HOST"), _env("SMTP_USER"), _env("SMTP_PASSWORD")
    port = int(_env("SMTP_PORT") or 587)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, _env("SMTP_FROM") or user, to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=TIMEOUT_S) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=TIMEOUT_S) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)


async def enviar_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    try:
        await asyncio.to_thread(_send_email_sync, to, subject, text, html)
    except smtplib.SMTPAuthenticationError as e:
        logger.warning("SMTP: autenticación rechazada")
        raise SendError("El servidor de correo rechazó las credenciales SMTP.") from e
    except (smtplib.SMTPException, OSError) as e:
        logger.warning("SMTP: fallo al enviar (%s)", type(e).__name__)
        raise SendError("No se pudo enviar el email (servidor de correo no disponible o destinatario rechazado).") from e


# ---------- WhatsApp ----------

def twilio_configured() -> bool:
    return all(_env(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"))


def limpiar_telefono(phone: str) -> str:
    """'+34 600-11-22-33' → '34600112233' (solo dígitos, sin '+')."""
    return re.sub(r"\D", "", phone or "")


def whatsapp_link(phone: str, text: str) -> str:
    return f"https://wa.me/{limpiar_telefono(phone)}?text={quote(text, safe='')}"


def _wa(number: str) -> str:
    n = number.strip()
    if n.startswith("whatsapp:"):
        return n
    return f"whatsapp:+{limpiar_telefono(n)}"


async def enviar_whatsapp(to: str, body: str, client: httpx.AsyncClient | None = None) -> str:
    """Envía por Twilio y devuelve el SID del mensaje."""
    sid, token = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")
    data = {"From": _wa(_env("TWILIO_WHATSAPP_FROM") or ""), "To": _wa(to), "Body": body}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
                r = await c.post(TWILIO_URL.format(sid=sid), data=data, auth=(sid, token))
        else:
            r = await client.post(TWILIO_URL.format(sid=sid), data=data, auth=(sid, token), timeout=TIMEOUT_S)
    except httpx.HTTPError as e:
        logger.warning("Twilio sin respuesta (%s)", type(e).__name__)
        raise SendError("No se pudo contactar con Twilio.") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("message", "")
        except ValueError:
            detail = ""
        logger.warning("Twilio %s: %s", r.status_code, detail)
        raise SendError(f"Twilio rechazó el mensaje ({r.status_code}). {detail}".strip())
    return str(r.json().get("sid", ""))
