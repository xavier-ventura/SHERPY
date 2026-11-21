# src/market_events.py
"""
Market tracker: turns NVDA/VTI/QQQ moves (data/market_snapshot.json, written by
finance.py) into per-family events for tracker.py. Writes data/events_market.json.

Same shape as events_builder.py (priority_score, priority, priority_reasons), type
"market_drop" so tracker.daily_queue keeps one market event per family. There is no
all-clear event here: tracker.daily_queue already creates the "reassurance" message
when a family has nothing above MIN_SCORE.

    python src/market_events.py                  # real snapshot
    python src/market_events.py --shock 20       # simulated 20% market drop (demo)
    python src/market_events.py --only michael-carter
    python src/market_events.py --watch 30       # recompute every 30 s

All numbers are computed in code. Cash and money market are left out of the invested
total, like calc_market_shock in SHERPY.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from cases import list_case_ids, load_json
from profiling import build_profile
from tracker import tech_exposure

DATA = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT = DATA / "market_snapshot.json"
OUT = DATA / "events_market.json"

WATCHED = ("NVDA", "VTI", "QQQ")
CASH_WORDS = ("cash", "money market", "money_market", "checking", "savings")
TECH_WEIGHT = {"NVDA": 1.0, "QQQ": 0.6, "VTI": 0.3}   # rough tech content of each ticker
THRESHOLD = 2.0                                        # minimum single-day drop, in %

LEVELS = [(80, "urgent"), (60, "high"), (40, "medium"), (0, "low")]


def level_for(score: int) -> str:
    return next(name for threshold, name in LEVELS if score >= threshold)


def with_priority(event: dict, score: int, reasons: list[str]) -> dict:
    score = max(0, min(100, score))
    event["priority_score"] = score
    event["priority"] = level_for(score)
    event["priority_reasons"] = reasons
    return event


# ---------- reading data (tolerant: adjust the key names if yours differ) ----------

def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def first(d: dict, keys, default=None):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default


def load_changes() -> tuple[dict, str | None]:
    """{'NVDA': -3.1, ...} in % vs previous close, and the snapshot date."""
    try:
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, None
    quotes = first(snap, ("quotes", "symbols", "data"), snap)
    changes = {}
    for sym in WATCHED:
        q = quotes.get(sym) if isinstance(quotes, dict) else None
        if isinstance(q, dict):
            pct = first(q, ("change_pct", "percent_change", "pct_change", "dp", "pct"))
            if pct is not None:
                changes[sym] = float(pct)
    return changes, first(snap, ("updated_at", "timestamp", "date_utc", "date"))


def family_exposure(case_id: str) -> dict:
    inv = load_json(case_id, "investment-analysis")
    fh = load_json(case_id, "financial-health")

    holdings = {}
    for d in walk(inv):
        sym = first(d, ("symbol", "ticker"))
        val = first(d, ("value", "marketValue", "market_value", "amount"))
        if isinstance(sym, str) and isinstance(val, (int, float)):
            if not any(w in sym.lower() for w in CASH_WORDS):
                holdings[sym.upper()] = holdings.get(sym.upper(), 0.0) + float(val)

    tech = tech_exposure(case_id) or {}            # reuses tracker's sectorConcentration reader
    monthly_out = next((float(v) for d in walk(fh)
                        if isinstance(v := first(d, ("monthly_outflows", "monthlyOutflows")), (int, float))), None)

    return {
        "invested": sum(holdings.values()),
        "holdings": holdings,
        "tech_pct": (tech.get("weight") or 0.0) * 100,
        "tech_severity": tech.get("severity"),
        "risk": build_profile(case_id).get("risk_by_person", {}),   # same source as tracker.py
        "monthly_out": monthly_out,
    }


# ---------- events ----------

def market_event(case_id: str, exp: dict, changes: dict, ts, shock: float | None) -> dict | None:
    moves = {s: p for s, p in changes.items() if p <= -THRESHOLD}
    if not moves or not exp["invested"]:
        return None
    worst_sym = min(moves, key=moves.get)
    worst = moves[worst_sym]

    # a simulated shock hits the whole invested total (as calc_market_shock); a real move hits
    # what the family holds in that ticker, or the tech content of its portfolio if it holds none
    if shock is not None:
        loss = exp["invested"] * shock / 100
    else:
        loss = 0.0
        for sym, pct in moves.items():
            held = exp["holdings"].get(sym, 0.0)
            base = held or exp["invested"] * exp["tech_pct"] / 100 * TECH_WEIGHT[sym]
            loss += base * abs(pct) / 100

    weight = exp["holdings"].get(worst_sym, 0.0) / exp["invested"]
    severity = min(60, abs(worst) * 6)
    exposure = min(25, weight * 50 + exp["tech_pct"] * TECH_WEIGHT[worst_sym] * 0.5)
    score = int(severity + exposure)

    reasons = [f"{worst_sym} {worst:+.1f}% vs previous close (+{int(severity)})"]
    if weight:
        reasons.append(f"{weight * 100:.0f}% of what they invest is in {worst_sym}")
    if exp["tech_pct"]:
        sev = f", severity {exp['tech_severity']}" if exp["tech_severity"] else ""
        reasons.append(f"technology = {exp['tech_pct']:.0f}% of their stocks{sev}")
    if len(moves) > 1:
        reasons.append("also down: " + ", ".join(f"{s} {p:+.1f}%" for s, p in moves.items() if s != worst_sym))

    facts = {"moves_pct": {s: round(p, 2) for s, p in moves.items()}, "est_loss_usd": round(loss),
             "est_loss_scope": ("whole invested portfolio, cash excluded" if shock is not None
                                else "positions in the tickers that moved")}
    if exp["monthly_out"]:
        facts["est_loss_months_of_spending"] = round(loss / exp["monthly_out"], 1)

    headline = (f"Markets drop {abs(shock):.0f}%: what it means for you" if shock is not None
                else f"{worst_sym} {worst:+.1f}% today: what it means for you")
    ev = {
        "id": f"market-{case_id}-{'shock' if shock is not None else worst_sym.lower()}",
        "type": "market_drop",
        "sector": "technology" if worst_sym in ("NVDA", "QQQ") else "broad",
        "headline": headline,
        "keywords": ["market", "invest"],     # matched against persona concerns by tracker.concern_bonus
        "facts": facts,                       # numbers computed in code, for the message writer
        "risk_profiles": exp["risk"],         # per person, so the tone can differ in a couple
        "snapshot_at": ts,
        "simulated": shock is not None,
        "family_ids": [case_id],
    }
    return with_priority(ev, score, reasons)


def run(shock: float | None, only: str | None) -> list[dict]:
    changes, ts = load_changes()
    if shock is not None:  # tech falls harder than the market
        changes = {"VTI": -shock, "QQQ": -shock * 1.25, "NVDA": -shock * 1.6}
        ts = datetime.now(timezone.utc).isoformat()

    events = []
    for cid in list_case_ids():
        if only and cid != only:
            continue
        ev = market_event(cid, family_exposure(cid), changes, ts, shock)
        if ev:
            events.append(ev)
    events.sort(key=lambda e: e["priority_score"], reverse=True)

    OUT.parent.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT)  # atomic write; an empty list clears yesterday's events
    return events


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shock", type=float, help="simulate a market drop of X%% (demo)")
    ap.add_argument("--only", help="only this family")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="minimum single-day drop in %%")
    ap.add_argument("--watch", type=int, help="recompute every N seconds")
    args = ap.parse_args()
    THRESHOLD = args.threshold

    while True:
        evs = run(args.shock, args.only)
        print(f"{len(evs)} market events\n")
        for e in evs:
            print(f"[{e['priority'].upper():6} {e['priority_score']:3}] {e['family_ids'][0]:22} {e['headline']}")
            print("          ", "; ".join(e["priority_reasons"]))
        if not args.watch:
            break
        time.sleep(args.watch)