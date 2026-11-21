# src/market_tools.py
"""
On-demand market data for the chat. The LLM calls get_sector_performance only when the user
asks how a stock-market sector is doing; the backend then queries Finnhub and returns the
numbers, and the LLM explains them. The LLM never invents the figures.

Needs FINNHUB_KEY in .env (same key as market_poller.py).
"""
import json
import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

# sector -> (ETF used as the sector's proxy, label)
SECTORS = {
    "technology": ("XLK", "Technology"),
    "semiconductors": ("SMH", "Semiconductors"),
    "communication_services": ("XLC", "Communication Services"),
    "consumer_discretionary": ("XLY", "Consumer Discretionary"),
    "consumer_staples": ("XLP", "Consumer Staples"),
    "financials": ("XLF", "Financials"),
    "health_care": ("XLV", "Health Care"),
    "industrials": ("XLI", "Industrials"),
    "energy": ("XLE", "Energy"),
    "utilities": ("XLU", "Utilities"),
    "real_estate": ("XLRE", "Real Estate"),
    "materials": ("XLB", "Materials"),
    "us_market": ("VTI", "US total market"),
    "nasdaq_100": ("QQQ", "Nasdaq-100"),
}

CACHE_SECONDS = 30            # Finnhub free tier: 60 calls/min
_cache: dict[str, tuple[float, dict]] = {}


def _quote(symbol: str) -> dict:
    now = time.time()
    if symbol in _cache and now - _cache[symbol][0] < CACHE_SECONDS:
        return _cache[symbol][1]
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": symbol, "token": os.environ["FINNHUB_KEY"]}, timeout=10)
    r.raise_for_status()
    q = r.json()
    _cache[symbol] = (now, q)
    return q


def get_sector_performance(sector: str, case_id: str | None = None) -> dict:
    if sector not in SECTORS:
        return {"error": f"unknown sector '{sector}'", "valid_sectors": list(SECTORS)}
    ticker, label = SECTORS[sector]
    try:
        q = _quote(ticker)
    except Exception as e:          # network, key, rate limit...
        return {"error": f"market data unavailable: {type(e).__name__}"}
    if not q.get("pc") or not q.get("c"):
        return {"error": f"no quote available for {ticker}"}

    out = {
        "sector": label,
        "proxy_etf": ticker,
        "price": q["c"],
        "prev_close": q["pc"],
        "change_pct": round((q["c"] / q["pc"] - 1) * 100, 2),
        "quote_time_utc": datetime.fromtimestamp(q["t"], timezone.utc).isoformat() if q.get("t") else None,
        "source": "Finnhub",
    }
    if q.get("t"):
        qd = datetime.fromtimestamp(q["t"], timezone.utc).date()
        out["quote_date"] = qd.isoformat()
        out["is_today"] = qd == datetime.now(timezone.utc).date()
    out["note"] = ("change is vs the previous close. If is_today is false the market has not traded "
                   "since quote_date: call it 'the last close' and give the date, never 'today'")

    if sector == "technology" and case_id:      # how much of this family's equity is in tech
        try:
            from tracker import tech_exposure
            exp = tech_exposure(case_id)
            if exp and exp.get("weight") is not None:
                out["family_plan_exposure"] = {   # from the family's Sherpas plan, NOT from Finnhub
                
                    "technology_weight_of_equity_pct": round(exp["weight"] * 100, 1),
                    "severity": exp.get("severity"),
                }
        except Exception:
            pass
    return out


# ---------- OpenAI function-calling ----------
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_sector_performance",
        "description": ("Live performance of a US stock-market sector (change vs previous close). "
                        "Call it ONLY when the user asks how a sector or the market is doing right now "
                        "(e.g. tech, energy, banks, the whole market). Do not call it for other questions."),
        "parameters": {
            "type": "object",
            "properties": {"sector": {"type": "string", "enum": list(SECTORS),
                                      "description": "The sector that best matches the question"}},
            "required": ["sector"],
        },
    },
}]

TOOL_PROMPT = (
    "You can look up live sector performance with get_sector_performance. Use it only when the user "
    "asks about a market sector or the market today. Quote only the numbers it returns, say when the "
    "quote is from (say 'last close' with its date when is_today is false), and explain what it means for this family "
    "in plain language. family_plan_exposure comes from the family's plan, not from the market data. Never give buy or sell advice."
)


def chat_with_tools(client, model: str, messages: list[dict], case_id: str | None = None,
                    max_rounds: int = 3) -> str:
    """Drop-in replacement for the single completion call in chat.py. Appends to `messages`."""
    for _ in range(max_rounds):
        msg = client.chat.completions.create(model=model, messages=messages, tools=TOOLS).choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append({
            "role": "assistant", "content": msg.content,
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments}}
                           for c in msg.tool_calls],
        })
        for c in msg.tool_calls:
            args = json.loads(c.function.arguments or "{}")
            result = (get_sector_performance(args.get("sector", ""), case_id)
                      if c.function.name == "get_sector_performance" else {"error": "unknown tool"})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})
    return "Sorry, I could not get the market data right now."


if __name__ == "__main__":      # quick manual test: python src/market_tools.py energy
    import sys
    print(json.dumps(get_sector_performance(sys.argv[1] if len(sys.argv) > 1 else "technology",
                                            sys.argv[2] if len(sys.argv) > 2 else None), indent=2))