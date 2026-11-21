"""Endpoints de la sección de noticias personalizadas (para el frontend)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import FallbackExceptionGroup, ModelHTTPError, UnexpectedModelBehavior

from sherpy.agent.agent import NoModelConfigured
from sherpy.agent.news import NewsFeed, NewsUnavailable, generar_noticias

router = APIRouter()


class NewsRequest(BaseModel):
    case_id: str
    language: str = "en"  # se acepta por compatibilidad; las noticias siempre van en inglés
    max_items: int = Field(6, ge=1, le=10)


async def _news(request: Request, body: NewsRequest) -> NewsFeed:
    source = request.app.state.source
    if body.case_id not in {c["id"] for c in await source.list_cases()}:
        raise HTTPException(404, f"Case '{body.case_id}' not found.")
    try:
        return await generar_noticias(source, body.case_id, getattr(request.app.state, "news_model", None),
                                      body.language, body.max_items)
    except NewsUnavailable as e:
        raise HTTPException(503, "News are not available right now (market data provider not configured).") from e
    except NoModelConfigured as e:
        raise HTTPException(503, "No AI model configured.") from e
    except (FallbackExceptionGroup, ModelHTTPError, UnexpectedModelBehavior) as e:
        status = 429 if "429" in str(e) else 502
        raise HTTPException(status, "The AI model could not prepare the news; please try again in a few minutes.") from e


@router.post("/api/news", response_model=NewsFeed)
async def news(request: Request, body: NewsRequest) -> NewsFeed:
    """Noticias relevantes para la familia, con por qué les afectan. Body: {case_id, language?, max_items?}."""
    return await _news(request, body)


@router.get("/api/news/{case_id}", response_model=NewsFeed)
async def news_get(request: Request, case_id: str, language: str = "en",
                   max_items: int = Query(6, ge=1, le=10)) -> NewsFeed:
    return await _news(request, NewsRequest(case_id=case_id, language=language, max_items=max_items))
