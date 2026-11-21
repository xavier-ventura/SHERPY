# src/market_sessions.py
"""
Open/close market alerts. At the US market open and close, builds one draft per family
in the advisor inbox (nothing reaches the family until the advisor approves it).

    python src/market_sessions.py --watch            # runs all day, fires at open and close
    python src/market_sessions.py --now open         # force an open alert (demo)
    python src/market_sessions.py --now close --shock 20   # simulated 20% drop (demo)
"""
import argparse
import json
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import inbox
from cases import list_case_ids
from market_events import family_exposure
from market_tools import _quote
from tracker import level_for

NY = ZoneInfo("America/New_York")
OPEN_AT = dtime(9, 35)      # 5 min after the 9:30 open, so the quote has the open price
CLOSE_AT = dtime(16, 5)     # 5 min after the 16:00 close, so the quote is final
WATCHED = ("VTI", "QQQ", "NVDA")
CALM_PCT = 1.0              # broad market inside +-1% = calm day
STATE = Path(__file__).resolve().parent.parent / "data" / "session_state.json"


def market_moves(kind: str, shock: float | None) -> dict | None:
    """{'VTI': {...}, ...} computed in code; None if the market did not trade today."""
    if shock is not None:
        return {"VTI": {"vs_prev_close_pct": -shock},
                "QQQ": {"vs_prev_close_pct": round(-shock * 1.25, 2)},
                "NVDA": {"vs_prev_close_pct": round(-shock * 1.6, 2)}}
    out = {}
    for sym in WATCHED:
        q = _quote(sym)
        if not (q.get("pc") and q.get("c") and q.get("o") and q.get("t")):
            continue
        if datetime.fromtimestamp(q["t"], NY).date() != datetime.now(NY).date():
            return None                          # weekend or holiday: no trading today
        if kind == "open":
            out[sym] = {"open_vs_prev_close_pct": round((q["o"] / q["pc"] - 1) * 100, 2)}
        else:
            out[sym] = {"vs_prev_close_pct": round((q["c"] / q["pc"] - 1) * 100, 2),
                        "open_to_close_pct": round((q["c"] / q["o"] - 1) * 100, 2),
                        "day_low_pct": round((q["l"] / q["pc"] - 1) * 100, 2),
                        "day_high_pct": round((q["h"] / q["pc"] - 1) * 100, 2)}
    return out or None


def broad_pct(moves: dict, kind: str) -> float:
    m = moves.get("VTI", {})
    return m.get("open_vs_prev_close_pct", m.get("vs_prev_close_pct", 0.0))


def build_session_alerts(kind: str, shock: float | None = None) -> int:
    moves = market_moves(kind, shock)
    if not moves:
        print(f"No trading today: no {kind} alert.")
        return 0
    pct = broad_pct(moves, kind)
    calm = abs(pct) < CALM_PCT
    score = 30 if calm else min(100, int(40 + abs(pct) * 8))
    today = datetime.now(NY).date().isoformat()
    when = "just opened" if kind == "open" else "just closed"
    what = ("how it opened compared with yesterday's close" if kind == "open"
            else "how the whole session went compared with yesterday's close")

    created = 0
    with inbox._lock:
        items = inbox._load()
        have = {i["id"] for i in items}
        for fid in list_case_ids():
            iid = f"{fid}:session-{kind}-{today}"
            if iid in have:
                continue
            exp = family_exposure(fid)
            facts = {"session": kind, "date": today, "moves_pct": moves,
                     "est_change_usd_on_invested": round(exp["invested"] * pct / 100),
                     "est_scope": "rough estimate: whole invested portfolio moving like the US total market (VTI), cash excluded"}
            brief = (f"The US stock market {when}. Tell the client {what}.\n"
                     f"Facts (use only these): {json.dumps(facts)}\n"
                     + ("The day is calm: say nothing needs doing and the advisor is watching."
                        if calm else "The move is notable: normalise it first, then what it means for this family."))
            items.append({
                "id": iid, "family_id": fid, "source": "market_session", "type": f"market_{kind}",
                "headline": f"US market {kind}: " + ("calm" if calm else f"VTI {pct:+.1f}%"),
                "priority": level_for(score), "priority_score": score,
                "reasons": [f"VTI {pct:+.1f}% at the {kind}", "scheduled market alert"],
                "simulated": shock is not None,
                "draft": inbox._draft(fid, brief), "status": "pending", "final_text": None,
                "created_at": inbox._now(), "decided_at": None,
            })
            created += 1
            print("drafted", iid)
        inbox._save(items)
    return created


# ---------- scheduler ----------

def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def due(now: datetime, state: dict) -> list[str]:
    if now.weekday() >= 5:
        return []
    day, t = now.date().isoformat(), now.time()
    kinds = []
    if OPEN_AT <= t < dtime(16, 0) and f"{day}:open" not in state:
        kinds.append("open")                     # an open alert after 16:00 would be stale
    if t >= CLOSE_AT and f"{day}:close" not in state:
        kinds.append("close")
    return kinds


def watch() -> None:
    print("Watching the market clock (New York time). Ctrl+C to stop.")
    while True:
        now = datetime.now(NY)
        state = _state()
        for kind in due(now, state):
            build_session_alerts(kind)
            state[f"{now.date().isoformat()}:{kind}"] = inbox._now()   # also when closed: no retry loop
            STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        time.sleep(30)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="fire automatically at open and close")
    ap.add_argument("--now", choices=["open", "close"], help="force one alert now (demo)")
    ap.add_argument("--shock", type=float, help="simulate a market drop of X%% (demo)")
    args = ap.parse_args()
    if args.now:
        print(f"{build_session_alerts(args.now, args.shock)} drafts created")
    elif args.watch:
        watch()
    else:
        ap.print_help()