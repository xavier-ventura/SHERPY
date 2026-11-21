# src/events_builder.py
import json
from datetime import date
from pathlib import Path

from cases import list_case_ids, load_json

TODAY = date(2026, 11, 21)
OUT = Path(__file__).resolve().parent.parent / "data" / "events_auto.json"

# ---------- priority settings (tune these numbers) ----------
DEFAULT_GOAL_BASE = 30


def goal_base(g: dict) -> int:
    gtype, sub = str(g.get("goalType", "")), str(g.get("subType", ""))
    if sub == "REAL_ESTATE_PURCHASE":
        return 75
    if gtype == "PURCHASE":          # car or other purchases
        return 40
    return {"COLLEGE": 65, "RETIREMENT": 55}.get(gtype, DEFAULT_GOAL_BASE)


SKIP_GOALS = ("BANKRUPTCY", "VACATION")   # not useful as events

CHILD_MILESTONES = {     # age: (text, base weight)
    11: ("seven years to college", 40),
    16: ("two years to college", 60),
    18: ("has turned 18: time to review insurance and who is responsible for what", 55),
    25: ("will lose family health cover at 26", 65),
}

LEVELS = [(80, "urgent"), (60, "high"), (40, "medium"), (0, "low")]


# ---------- helpers ----------

def level_for(score: int) -> str:
    return next(name for threshold, name in LEVELS if score >= threshold)


def days_until(d: date) -> int:
    return (d - TODAY).days


def friendly_time(days: int) -> str:
    if days < 14:
        return f"in {days} days"
    if days < 90:
        return f"in about {round(days / 7)} weeks"
    return f"in about {round(days / 30)} months"


def person_name(p: dict) -> str:
    return p.get("first_name") or p.get("name") or "child"


def with_priority(event: dict, score: int, reasons: list[str]) -> dict:
    score = max(0, min(100, score))
    event["priority_score"] = score
    event["priority"] = level_for(score)
    event["priority_reasons"] = reasons
    return event


# ---------- plan events (goals with a date) ----------

def goal_events(case_id: str, horizon_days: int = 730) -> list[dict]:
    fh = load_json(case_id, "financial-health")
    events = []
    for g in fh.get("goals", {}).get("chartGoals", []):
        gtype = str(g.get("goalType", ""))
        if gtype.startswith(SKIP_GOALS) or not g.get("date"):
            continue
        d = date.fromisoformat(g["date"][:10])
        n = days_until(d)
        if not 0 <= n <= horizon_days:
            continue

        base = goal_base(g)
        score, reasons = base, [f"goal: {gtype.lower()} (+{base})"]

        if n <= 30:
            bonus = 25
        elif n <= 90:
            bonus = 15
        elif n <= 365:
            bonus = 5
        else:
            bonus = 0
        if bonus:
            score += bonus
            reasons.append(f"happens in {n} days (+{bonus})")

        ev = {
            "id": f"plan-{case_id}-{gtype.lower()}-{d.year}",
            "type": "plan_date",
            "headline": f"{g.get('name', gtype)} {friendly_time(n)}",
            "days_away": n,
            "keywords": [gtype.lower()],   # matched against the persona concerns O1, O2...
            "simulated": False,
            "family_ids": [case_id],
        }
        events.append(with_priority(ev, score, reasons))
    return events


# ---------- life events (children) ----------

def child_events(case_id: str) -> list[dict]:
    fh = load_json(case_id, "financial-health")
    events = []
    for p in fh.get("case", {}).get("household", []):
        role = str(p.get("role") or "").lower()
        if role != "child" or p.get("age") not in CHILD_MILESTONES:
            continue
        name, age = person_name(p), p["age"]
        text, base = CHILD_MILESTONES[age]

        ev = {
            "id": f"life-{case_id}-{name.lower().replace(' ', '-')}-{age}",
            "type": "life_moment",
            "headline": f"{name} is {age}: {text}" if age != 18 else f"{name} {text}",
            "keywords": {11: ["college"], 16: ["college"], 18: ["insurance"], 25: ["insurance"]}[age],
            "simulated": False,
            "family_ids": [case_id],
        }
        events.append(with_priority(ev, base, [f"milestone at age {age} (+{base})"]))
    return events


# ---------- main ----------

if __name__ == "__main__":
    all_events = []
    for cid in list_case_ids():
        all_events += goal_events(cid) + child_events(cid)
    all_events.sort(key=lambda e: e["priority_score"], reverse=True)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(all_events, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{len(all_events)} events generated\n")
    for e in all_events:
        print(f"[{e['priority'].upper():6} {e['priority_score']:3}] {e['family_ids'][0]:22} {e['headline']}")
        print("          ", "; ".join(e["priority_reasons"]))