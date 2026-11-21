"""API HTTP de SHERPY."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic_ai import (
    AgentRunResultEvent,
    FallbackExceptionGroup,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelAPIError,
    ModelMessagesTypeAdapter,
    ModelRateLimitError,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from sherpy.agent.agent import NoModelConfigured, build_agent
from sherpy.agent.deps import AgentDeps
from sherpy.api.market import router as market_router
from sherpy.api.news import router as news_router
from sherpy.api.send import router as send_router
from sherpy.api.schemas import ChatRequest, HealthResponse
from sherpy.api.voice import router as voice_router
from sherpy.config import get_settings
from sherpy.data.source import CaseSource, MCPBackend

logger = logging.getLogger("sherpy.api")
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    async with AsyncExitStack() as stack:
        primary = None
        if settings.mcp_url and settings.mcp_token:
            try:
                primary = await stack.enter_async_context(MCPBackend(settings.mcp_url, settings.mcp_token))
                logger.info("Conectado al MCP de Sherpas")
            except Exception as e:
                logger.warning("No se pudo conectar al MCP (%s); uso datos locales", e)
        app.state.source = CaseSource(primary=primary)
        if not hasattr(app.state, "agent"):
            try:
                app.state.agent = build_agent()
            except NoModelConfigured as e:
                logger.warning("%s", e)
                app.state.agent = None
        yield


app = FastAPI(title="SHERPY", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(voice_router)
app.include_router(market_router)
app.include_router(news_router)
app.include_router(send_router)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, FallbackExceptionGroup):
        rate_limited = all(isinstance(e, ModelRateLimitError) for e in exc.exceptions)
        return {"status": 429 if rate_limited else 503,
                "message": "Todos los proveedores de IA han fallado" + (" por límite de uso" if rate_limited else "") + ". Inténtalo en un momento."}
    if isinstance(exc, ModelRateLimitError):
        return {"status": 429, "message": "Límite de uso del proveedor de IA alcanzado. Inténtalo en un momento."}
    if isinstance(exc, ModelAPIError):
        return {"status": 502, "message": f"Error del proveedor de IA: {exc}"}
    if isinstance(exc, (UnexpectedModelBehavior, UsageLimitExceeded)):
        return {"status": 502, "message": f"El modelo no respondió correctamente: {exc}"}
    return {"status": 500, "message": "Error interno."}


@app.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", data_source=request.app.state.source.origin,
                          llm_configured=request.app.state.agent is not None)


@app.get("/cases")
async def cases(request: Request) -> list[dict[str, Any]]:
    return await request.app.state.source.list_cases()


@app.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    """Chat en streaming (Server-Sent Events).

    Eventos: `delta` {text} · `tool` {name, args} · `tool_result` {name, source} · `done` {output, history} · `error` {status, message}.
    El cliente reenvía `history` en el siguiente turno.
    """
    agent = request.app.state.agent
    source: CaseSource = request.app.state.source
    if agent is None:
        raise HTTPException(503, "No hay LLM configurado (TEAM_KEY / GOOGLE_API_KEY / GROQ_API_KEY).")
    if req.case_id not in {c["id"] for c in await source.list_cases()}:
        raise HTTPException(404, f"Caso '{req.case_id}' no existe.")
    try:
        history = ModelMessagesTypeAdapter.validate_python(req.history) if req.history else None
    except Exception as e:
        raise HTTPException(422, f"history inválido: {e}") from e

    deps = AgentDeps(case_id=req.case_id, source=source)

    async def stream() -> AsyncIterator[str]:
        try:
            async with agent.run_stream_events(req.message, deps=deps, message_history=history) as events:
                async for ev in events:
                    if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart) and ev.part.content:
                        yield _sse("delta", {"text": ev.part.content})
                    elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                        yield _sse("delta", {"text": ev.delta.content_delta})
                    elif isinstance(ev, FunctionToolCallEvent):
                        yield _sse("tool", {"name": ev.part.tool_name, "args": ev.part.args_as_dict()})
                    elif isinstance(ev, FunctionToolResultEvent):
                        content = getattr(ev.part, "content", None)
                        origin = content.get("source") if isinstance(content, dict) else None
                        yield _sse("tool_result", {"name": getattr(ev.part, "tool_name", None), "source": origin})
                    elif isinstance(ev, AgentRunResultEvent):
                        hist = ModelMessagesTypeAdapter.dump_python(ev.result.all_messages(), mode="json")
                        yield _sse("done", {"output": ev.result.output, "history": hist})
        except Exception as exc:  # el stream ya empezó: el error va como evento
            logger.exception("Error en /chat")
            yield _sse("error", _error_payload(exc))

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
