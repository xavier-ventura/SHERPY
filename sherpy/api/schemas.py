from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    case_id: str = Field(description="caseId de Sherpas, p. ej. 'michael-carter'")
    message: str = Field(min_length=1)
    history: list[dict[str, Any]] | None = Field(
        default=None, description="Historial devuelto en el evento `done` del turno anterior (opaco para el cliente)")


class HealthResponse(BaseModel):
    status: str
    data_source: str
    llm_configured: bool
