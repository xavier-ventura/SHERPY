# src/pill_generator.py
import json, os
from typing import Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

load_dotenv()

class MiniChallenge(BaseModel):
    question: str
    options: list[str] = Field(min_length=2, max_length=4)
    correct_index: int

class Pill(BaseModel):
    title: str = Field(description="Short, personal title")
    body: str = Field(description="3-5 sentences, plain language, no jargon")
    why_this_family: str = Field(description="One sentence for the advisor: why this family gets this")
    mini_challenge: MiniChallenge
    knowledge_level: Literal["basic", "intermediate", "advanced"]

llm = ChatOpenAI(
    model="gpt-5-mini",
    base_url=os.environ["LLM_PROXY_URL"] + "/v1",   # el .env no incluye /v1
    api_key=os.environ["TEAM_KEY"],
)
structured_llm = llm.with_structured_output(Pill)

SYSTEM = """You write short financial education pills for a family, on behalf of their advisor.
Rules:
- Use ONLY the facts and numbers given in the context. Never invent figures.
- Match the person's risk profile and what they already know. Do not repeat what they have seen.
- Calm, warm tone. If nothing needs action, say so.
- Never recommend buying or selling specific securities.
"""

def generate_pill(profile: dict, plan: dict, event: dict, numbers: dict, history: dict) -> Pill:
    context = json.dumps(
        {"profile": profile, "plan": plan, "event": event,
         "numbers": numbers, "learning_history": history},
        ensure_ascii=False,
    )
    return structured_llm.invoke([
        ("system", SYSTEM),
        ("user", f"Context:\n{context}\n\nWrite the pill."),
    ])