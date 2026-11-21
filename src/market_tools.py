# src/market_tools.py
"""
On-demand market data for the chat. The LLM calls a tool only when the user asks about the market:
  - get_sector_performance(sector): a sector or the whole market (via a proxy ETF)
  - get_stock_quote(query): ANY US-listed company or ETF the user names ("Apple", "NVDA", "Tesla")
The backend queries Finnhub and returns the numbers; the LLM only explains them.

Needs FINNHUB_API_KEY in .env (same key as market_poller.py).
"""
import json
import os
import re
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

API = "https://finnhub.io/api/v1"

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
_quotes: dict[str, tuple[float, dict]] = {}
_searches: dict[str, dict | None] = {}


def _get(path: str, **params) -> dict:
    r = requests.get(f"{API}/{path}", params={**params, "token": os.environ["FINNHUB_API_KEY"]}, timeout=10)
    r.raise_for_status()
    return r.json()


def _quote(symbol: str) -> dict:
    now = time.time()
    if symbol in _quotes and now - _quotes[symbol][0] < CACHE_SECONDS:
        return _quotes[symbol][1]
    q = _get("quote", symbol=symbol)
    _quotes[symbol] = (now, q)
    return q


def _quote_fields(q: dict) -> dict | None:
    """Numbers computed in code from a Finnhub quote; None if there is no usable quote."""
    if not q.get("pc") or not q.get("c"):
        return None
    out = {"price": q["c"], "prev_close": q["pc"], "change_pct": round((q["c"] / q["pc"] - 1) * 100, 2),
           "source": "Finnhub"}
    if q.get("t"):
        qd = datetime.fromtimestamp(q["t"], timezone.utc)
        out["quote_time_utc"] = qd.isoformat()
        out["quote_date"] = qd.date().isoformat()
        out["is_today"] = qd.date() == datetime.now(timezone.utc).date()
    out["note"] = ("change is vs the previous close. If is_today is false the market has not traded "
                   "since quote_date: call it 'the last close' and give the date, never 'today'")
    return out


def _search(q: str) -> list[dict]:
    res = _get("search", q=q.strip()).get("result", [])
    return [x for x in res if x.get("symbol") and "." not in x["symbol"] and re.fullmatch(r"[A-Z\-]{1,6}", x["symbol"])]


def find_symbol(query: str, ticker: str | None = None) -> dict | None:
    """Company name or ticker -> {'symbol', 'description'} (US listings only), via Finnhub search.
    `ticker` is the LLM's guess for well-known companies: used only if the search confirms it exactly."""
    key = f"{query.strip().lower()}|{(ticker or '').upper()}"
    if key in _searches:
        return _searches[key]
    hit = None
    try:
        if ticker:
            hit = next((x for x in _search(ticker) if x["symbol"] == ticker.strip().upper()), None)
        if not hit:
            us = _search(query)
            up = query.strip().upper()
            named = sorted((x for x in us if str(x.get("description", "")).lower().startswith(query.strip().lower())),
                           key=lambda x: len(x["description"]))     # "APPLE INC" before "APPLE HOSPITALITY REIT"
            hit = next((x for x in us if x["symbol"] == up), None) or (named[0] if named else (us[0] if us else None))
    except Exception:
        return None            # do not cache failures
    _searches[key] = hit
    return hit


def _family_holding(symbol: str, case_id: str | None) -> dict | None:
    """Share of the family's invested money held directly in this ticker (funds are not unpacked)."""
    if not case_id:
        return None
    try:
        from market_events import family_exposure
        exp = family_exposure(case_id)
        held = exp["holdings"].get(symbol, 0.0)
        if exp["invested"]:
            return {"holds_directly": held > 0, "pct_of_invested": round(held / exp["invested"] * 100, 1),
                    "note": "direct holdings only; funds (VTI, QQQ...) that own this company are not counted"}
    except Exception:
        pass
    return None


def get_stock_quote(query: str, case_id: str | None = None, ticker: str | None = None) -> dict:
    if not query or not query.strip():
        return {"error": "no company given"}
    hit = find_symbol(query, ticker)
    if not hit:
        return {"error": f"no US-listed company or ETF found for '{query}'. Say your advisor will confirm."}
    try:
        fields = _quote_fields(_quote(hit["symbol"]))
    except Exception as e:
        return {"error": f"market data unavailable: {type(e).__name__}"}
    if not fields:
        return {"error": f"no quote available for {hit['symbol']}"}
    out = {"company": hit.get("description"), "ticker": hit["symbol"], **fields}
    held = _family_holding(hit["symbol"], case_id)
    if held:
        out["family_plan_holding"] = held       # from the family's plan, NOT from Finnhub
    return out


def get_sector_performance(sector: str, case_id: str | None = None) -> dict:
    if sector not in SECTORS:
        return {"error": f"unknown sector '{sector}'", "valid_sectors": list(SECTORS)}
    ticker, label = SECTORS[sector]
    try:
        fields = _quote_fields(_quote(ticker))
    except Exception as e:          # network, key, rate limit...
        return {"error": f"market data unavailable: {type(e).__name__}"}
    if not fields:
        return {"error": f"no quote available for {ticker}"}
    out = {"sector": label, "proxy_etf": ticker, **fields}

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
TOOLS = [
    {"type": "function", "function": {
        "name": "get_sector_performance",
        "description": ("Live performance of a US stock-market SECTOR or the whole market (change vs previous close). "
                        "Call it ONLY when the user asks how a sector or the market is doing (tech, energy, banks, "
                        "the whole market). For a specific company use get_stock_quote."),
        "parameters": {"type": "object",
                       "properties": {"sector": {"type": "string", "enum": list(SECTORS),
                                                 "description": "The sector that best matches the question"}},
                       "required": ["sector"]}}},
    {"type": "function", "function": {
        "name": "get_stock_quote",
        "description": ("Live quote of ONE specific US-listed company or ETF the user names (Apple, Tesla, NVDA, "
                        "an ETF). Call it ONLY when the user asks about that company's price or move. "
                        "Pass the name or ticker exactly as the user wrote it."),
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string", "description": "Company name or ticker, e.g. 'Apple' or 'AAPL'"},
                                      "ticker": {"type": "string", "description": "The US ticker, only if you are certain of it (Apple -> AAPL). Omit if unsure."}},
                       "required": ["query"]}}},
]

TOOL_PROMPT = (
    "You can look up live market data: get_sector_performance for a sector or the whole market, and "
    "get_stock_quote for a specific company or ETF the user names. Use them only when the user asks about the "
    "market. Quote only the numbers they return, say when the quote is from (say 'last close' with its date "
    "when is_today is false), and explain what it means for this family in plain language. family_plan_exposure "
    "and family_plan_holding come from the family's plan, not from the market data. For a single company give only "
    "the quote and what it means for the family; never say whether to buy or sell it. Check that the returned "
    "company is the one the user meant; if not sure, say so and that your advisor will confirm. If a lookup returns an "
    "error, say your advisor will confirm. Never give buy or sell advice."
)

_BUILTIN = {
    "get_sector_performance": lambda a, cid: get_sector_performance(a.get("sector", ""), cid),
    "get_stock_quote": lambda a, cid: get_stock_quote(a.get("query", ""), cid, a.get("ticker")),
}


def chat_with_tools(client, model: str, messages: list[dict], case_id: str | None = None,
                    max_rounds: int = 3, extra_tools: list | None = None, handlers: dict | None = None) -> str:
    """Drop-in replacement for the single completion call in chat.py. Appends to `messages`."""
    table = {**_BUILTIN, **(handlers or {})}
    for _ in range(max_rounds):
        msg = client.chat.completions.create(model=model, messages=messages,
                                             tools=TOOLS + (extra_tools or [])).choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append({
            "role": "assistant", "content": msg.content,
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments}}
                           for c in msg.tool_calls],
        })
        for c in msg.tool_calls:
            try:
                args = json.loads(c.function.arguments or "{}")
                result = table[c.function.name](args, case_id) if c.function.name in table else {"error": "unknown tool"}
            except Exception as e:
                result = {"error": f"tool failed: {type(e).__name__}"}
            print(f"[tool] {c.function.name}({c.function.arguments}) -> {result.get('error') or 'ok'}")   # visible in the server console
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})
    return "Sorry, I could not get the market data right now."


if __name__ == "__main__":      # python src/market_tools.py Apple [family_id]   |   python src/market_tools.py --sector energy
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--sector":
        print(json.dumps(get_sector_performance(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None), indent=2))
    else:
        print(json.dumps(get_stock_quote(sys.argv[1] if len(sys.argv) > 1 else "Apple",
                                         sys.argv[2] if len(sys.argv) > 2 else None), indent=2))