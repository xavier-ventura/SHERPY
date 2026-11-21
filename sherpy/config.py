"""Configuración leída del entorno (y de `.env` si existe)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM de la hackathon (compatible con OpenAI): principal
    team_key: str | None = None
    sherpas_llm_url: str = "https://llm-hackathon.sherpaswealth.com/v1"
    sherpas_llm_model: str = "gpt-5-mini"
    # Respaldos opcionales
    google_api_key: str | None = None
    groq_api_key: str | None = None
    mcp_url: str | None = None
    mcp_token: str | None = None
    # Copiar el ID exacto de AI Studio / Groq: cambian a menudo.
    gemini_model: str = "gemini-flash-latest"
    groq_model: str = "openai/gpt-oss-120b"
    # Orígenes permitidos para llamar desde el navegador (Lovable, localhost…), separados por comas. "*" = todos.
    cors_origins: str = "*"
    # Voz (ElevenLabs Custom LLM): clave que ElevenLabs debe enviar como Bearer (opcional) y familia por defecto
    voice_api_key: str | None = None
    default_case_id: str | None = None
    # Cotizaciones en tiempo real (Finnhub, plan gratuito). Sin clave, las herramientas de mercado avisan y siguen.
    finnhub_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
