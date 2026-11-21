# src/pill_generator.py
import json
import os
from typing import Literal

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

load_dotenv()


class MiniChallenge(BaseModel):
    question: str
    options: list[str] = Field(min_length=2, max_length=4)
    correct_index: int
    explanation: str = Field(description="One sentence explaining the right answer")


class Figure(BaseModel):
    label: str
    value: str
    source: str = Field(description="Where in the context it comes from, e.g. 'goals', 'numbers', 'persona_excerpt'")

class Pill(BaseModel):
    title: str = Field(description="Short, personal title (max 10 words)")
    body: str = Field(description="3-4 short sentences, plain language, warm tone, max 3 figures")
    next_step: str = Field(description="One sentence: what, if anything, the family needs to do. 'Nothing needed now' is valid")
    figures_cited: list[Figure] = Field(description="Every number used in the body, with its source")
    why_this_family: str
    mini_challenge: MiniChallenge
    knowledge_level: Literal["basic", "intermediate", "advanced"]


llm = ChatOpenAI(
    model="gpt-5-mini",
    base_url=os.environ["LLM_PROXY_URL"].rstrip("/") + "/v1",
    api_key=os.environ["TEAM_KEY"],
)
structured_llm = llm.with_structured_output(Pill)

SYSTEM = """You write short financial education pills for a US family, on behalf of their financial advisor.
The advisor will review and approve every pill before the family sees it.

Rules:
- Use ONLY the facts and numbers in the context. Never invent figures, dates or percentages.
- Speak to the family in the second person, using their first names when natural.
- Adapt to each person's risk profile and to what they have already seen. Do not repeat topics listed as understood; reinforce topics listed under needs_reinforcement when relevant.
- Calm, warm tone. If the event is a market drop, first normalise it (the plan was built for this), then explain what it means for them. Do not alarm.
- If something is uncertain in the data (for example, whether insurance exists), say the advisor will confirm it; do not assert.
- Never recommend buying or selling specific securities, and never promise outcomes.
- The mini challenge is a short check of understanding with ONE correct option, not a quiz about their private numbers.
- Write in English.
- Use at most 3 figures in the body, and explain what each means instead of listing them. List every figure in figures_cited with its source.
- End with a concrete next_step. If nothing is needed, say so.
- The mini challenge must teach a general concept related to the event (for example why an emergency reserve matters after a down payment), not restate the pill. All options must be plausible; never use unrelated details from the family's data as distractors.
"""


def generate_pill(profile: dict, event: dict, numbers: dict) -> Pill:
    context = {
        "family": {
            "household": profile["household"],
            "risk_by_person": profile["risk_by_person"],
            "goals": profile["goals"],
            "top_moves": profile["top_moves"],
            "concerns": profile["concerns"],
            "tensions": profile["tensions"],
            "persona_excerpt": profile["persona_excerpt"],
        },
        "event": {k: event[k] for k in ("type", "headline", "simulated") if k in event},
        "numbers": numbers,
        "learning_history": profile["learning"],
    }
    return structured_llm.invoke([
        ("system", SYSTEM),
        ("user", f"Context (JSON):\n{json.dumps(context, ensure_ascii=False)}\n\nWrite the pill."),
    ])