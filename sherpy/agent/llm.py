"""Conexión al LLM de la hackathon de Sherpas (endpoint compatible con OpenAI)."""

from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from sherpy.config import Settings, get_settings


def build_sherpas_model(settings: Settings | None = None) -> OpenAIChatModel:
    """Modelo `gpt-5-mini` (configurable) servido por Sherpas, autenticado con TEAM_KEY."""
    s = settings or get_settings()
    if not s.team_key:
        raise ValueError("Falta TEAM_KEY en el entorno.")
    return OpenAIChatModel(s.sherpas_llm_model, provider=OpenAIProvider(base_url=s.sherpas_llm_url, api_key=s.team_key))


if __name__ == "__main__":
    # Prueba rápida: .venv/bin/python -m sherpy.agent.llm
    from pydantic_ai import Agent

    agent = Agent(build_sherpas_model())
    print(agent.run_sync("Explain a 401(k) in two sentences.").output)
