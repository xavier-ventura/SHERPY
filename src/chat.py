# src/chat.py
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from market_tools import TOOL_PROMPT, chat_with_tools
from profiling import build_profile
from tracker import ranked_events_for_family

load_dotenv()

llm = OpenAI(
    base_url=os.environ["LLM_PROXY_URL"].rstrip("/") + "/v1",
    api_key=os.environ["TEAM_KEY"],
)
MODEL = "gpt-5-mini"

# ESTE es el prompt: aquí se configura cómo responde el chatbot
SYSTEM_CHAT = """You are the assistant of a US family's financial advisor, inside the Sherpas platform.
You talk directly to the client, on behalf of the advisor.

Rules:
- Use ONLY the facts and numbers in the context and in tool results. If something is not there, say your advisor will confirm it. Never invent figures.
- Short sentences, plain words, warm and calm tone. No jargon; if a term is needed, explain it in a few words.
- Normalise market moves first (the plan was built for them), then explain what they mean for this family.
- Never recommend buying or selling specific securities and never promise outcomes.
- For decisions (selling, buying a home, changing contributions) say the advisor will go through it together, and offer to flag it to them.
- Answer in the same language the client writes in.
- Keep it brief because the audience age is around 60 or more years old: at most about 90 words, no headings, at most 3 short bullets. If more is needed, offer to continue in a second message.
- Speak to the client directly (you / your, or their first name), never about them in the third person. One adult in the household: address one person (singular). Two adults: address both.
- A figure called "est_loss" is an estimate of what the invested portfolio would lose at that move. It is not money already lost, and nobody has sold anything. Say where a figure comes from: "your plan" for plan data, "an estimate on your current holdings" for est_loss, "live market data" for quotes.
- Answer only what was asked. Open with reassurance only if the client sounds worried; do not repeat figures from your previous answer unless they are needed.
- Never compare a portfolio weight (a % of their stocks) with a market move (a % change): they are different things. Connect them in words instead, for example: tech is a third of your stocks, so a drop in tech matters, but the plan was built for it.
- Never repeat field names or tool wording ("last close", "is_today", "est_loss"): say it naturally.
- When a recent event has "facts" (estimated loss, moves), use those exact numbers. "risk_profiles" tells you how each person reacts to risk: if they differ, speak to both.

"""

HISTORY: dict[str, list[dict]] = {}     # historial en memoria, por familia


def family_context(family_id: str) -> str:
    profile = build_profile(family_id)
    events = []
    for r in ranked_events_for_family(family_id)[:5]:
        ev = r["event"]
        item = {"headline": ev["headline"], "priority": r["level"], "why": r["reasons"]}
        if ev.get("facts"):                      # cifras calculadas en código (market_events.py)
            item["facts"] = ev["facts"]
        if ev.get("risk_profiles"):
            item["risk_profiles"] = ev["risk_profiles"]
        events.append(item)
    ctx = {
        "household": profile["household"],
        "risk_by_person": profile["risk_by_person"],
        "goals": profile["goals"],
        "concerns": profile["concerns"],
        "recent_events": events,
    }
    return json.dumps(ctx, ensure_ascii=False)


def chat(family_id: str, user_message: str) -> str:
    history = HISTORY.setdefault(family_id, [])
    messages = [
        {"role": "system", "content": SYSTEM_CHAT + TOOL_PROMPT},
        {"role": "system", "content": f"Family context (JSON):\n{family_context(family_id)}"},
        *history[-10:],                       # últimos 10 mensajes
        {"role": "user", "content": user_message},
    ]
    # puede llamar a Finnhub si el usuario pregunta por un sector; messages recibe esas llamadas
    text = chat_with_tools(llm, MODEL, messages, case_id=family_id)
    history += [{"role": "user", "content": user_message},     # el historial guarda solo texto
                {"role": "assistant", "content": text}]
    return text


if __name__ == "__main__":
    print(chat("michael-carter", "¿Qué pasa si el mercado cae justo cuando compro la casa?"))
    print(chat("michael-carter", "¿Y cómo va hoy el sector tecnológico?"))