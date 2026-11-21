"""Endpoint compatible con OpenAI Chat Completions para usar SHERPY como "Custom LLM" en ElevenLabs.

ElevenLabs pone la voz (STT/TTS); SHERPY sigue siendo el cerebro: mismas reglas, datos y calc_*.

Cómo se elige la familia (en este orden):
1. `elevenlabs_extra_body.case_id` (enviado por el SDK de ElevenLabs al iniciar la conversación).
2. Una línea `case_id: <id>` en el system prompt del agente de ElevenLabs (admite variables dinámicas,
   p. ej. `case_id: {{case_id}}`).
3. `DEFAULT_CASE_ID` en el entorno.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic_ai import AgentRunResultEvent, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.messages import TextPart as MsgTextPart

from sherpy.agent.deps import AgentDeps
from sherpy.config import get_settings

logger = logging.getLogger("sherpy.voice")
router = APIRouter()

VOICE_INSTRUCTIONS = """\
MODO VOZ: tu respuesta se va a leer en voz alta.
- 2 a 4 frases cortas por turno. Sin Markdown, sin tablas, sin listas con símbolos, sin emojis.
- Di los números como se hablan y redondeados ("unos tres meses", "trece mil seiscientos dólares").
- Menciona la fuente de forma natural ("según vuestro score…"), sin rutas ni nombres de secciones técnicos.
- Como máximo una acción por turno y termina, si encaja, con una pregunta corta para seguir.
"""

CASE_RE = re.compile(r"case[_ ]?id\s*[:=]\s*([a-z0-9][a-z0-9-]+)", re.IGNORECASE)


def _text(content: Any) -> str:
    """Contenido OpenAI → texto (admite string o lista de partes {type: text})."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def resolve_case_id(body: dict[str, Any], default: str | None) -> str | None:
    extra = body.get("elevenlabs_extra_body") or {}
    if isinstance(extra, dict) and extra.get("case_id"):
        return str(extra["case_id"]).strip()
    for msg in body.get("messages") or []:
        if msg.get("role") == "system":
            m = CASE_RE.search(_text(msg.get("content")))
            if m:
                return m.group(1).lower()
    return default


def to_history(messages: list[dict[str, Any]]) -> tuple[list[ModelMessage], str]:
    """Convierte los mensajes OpenAI (sin system) en historial de Pydantic AI + último mensaje del usuario."""
    turns = [m for m in messages if m.get("role") in ("user", "assistant") and _text(m.get("content")).strip()]
    if not turns or turns[-1]["role"] != "user":
        raise HTTPException(400, "El último mensaje debe ser del usuario.")
    history: list[ModelMessage] = []
    for m in turns[:-1]:
        text = _text(m["content"])
        if m["role"] == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=text)]))
        else:
            history.append(ModelResponse(parts=[MsgTextPart(content=text)]))
    return history, _text(turns[-1]["content"])


def _chunk(cid: str, model: str, delta: dict[str, Any], finish: str | None = None) -> str:
    payload = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _check_auth(request: Request) -> None:
    key = get_settings().voice_api_key
    if key and request.headers.get("authorization", "") != f"Bearer {key}":
        raise HTTPException(401, "API key inválida.")


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    _check_auth(request)
    try:
        body: dict[str, Any] = await request.json()
    except Exception as e:
        raise HTTPException(400, "JSON inválido.") from e

    agent = request.app.state.agent
    source = request.app.state.source
    if agent is None:
        raise HTTPException(503, "No hay LLM configurado.")

    case_id = resolve_case_id(body, get_settings().default_case_id)
    valid = {c["id"] for c in await source.list_cases()}
    if case_id not in valid:
        raise HTTPException(400, f"case_id no indicado o inválido ({case_id!r}). Opciones: {sorted(valid)}")

    history, prompt = to_history(body.get("messages") or [])
    deps = AgentDeps(case_id=case_id, source=source)
    model = str(body.get("model") or "sherpy")
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    run_kwargs = {"deps": deps, "message_history": history or None, "instructions": VOICE_INSTRUCTIONS}

    if not body.get("stream", False):
        result = await agent.run(prompt, **run_kwargs)
        return JSONResponse({
            "id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": result.output},
                         "finish_reason": "stop"}],
        })

    async def stream() -> AsyncIterator[str]:
        yield _chunk(cid, model, {"role": "assistant", "content": ""})
        try:
            async with agent.run_stream_events(prompt, **run_kwargs) as events:
                async for ev in events:
                    if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart) and ev.part.content:
                        yield _chunk(cid, model, {"content": ev.part.content})
                    elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                        yield _chunk(cid, model, {"content": ev.delta.content_delta})
                    elif isinstance(ev, AgentRunResultEvent):
                        break
        except Exception:
            logger.exception("Error en /v1/chat/completions")
            yield _chunk(cid, model, {"content": " Perdona, he tenido un problema técnico. ¿Puedes repetirlo?"})
        yield _chunk(cid, model, {}, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
