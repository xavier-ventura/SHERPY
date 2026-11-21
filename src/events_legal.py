# src/events_legal.py
"""
Legal tracker: turns a rule change into per-family events for tracker.py.
Writes data/events_legal.json. Only families the rule actually touches get an event.

    python src/events_legal.py
"""
import json
from datetime import date
from pathlib import Path

from cases import CASES_DIR, list_case_ids, load_json
from tracker import max_salary

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "events_legal.json"

TODAY = date(2026, 11, 21)
EFFECTIVE = date(2027, 1, 1)

# Check these against legal-values.json (SSWageBaseByYear)
SS_WAGE_BASE_2026 = 184_500
SS_WAGE_BASE_2027 = 192_300
SS_RATE = 0.062

LEVELS = [(80, "urgent"), (60, "high"), (40, "medium"), (0, "low")]


def level_for(score: int) -> str:
    return next(name for threshold, name in LEVELS if score >= threshold)


def verified_facts(case_id: str) -> list[str]:
    """Facts already written in the family's signals.json for rule changes."""
    path = CASES_DIR / case_id / "signals.json"
    if not path.exists():
        return []
    signals = json.loads(path.read_text(encoding="utf-8")).get("signals", [])
    facts = []
    for s in signals:
        if s.get("moment") == "rule":
            facts += s.get("touches_this_family", [])
    return facts


def wage_base_event(case_id: str) -> dict | None:
    salary = max_salary(load_json(case_id, "financial-health"))
    extra_wages = max(0, min(salary, SS_WAGE_BASE_2027) - min(salary, SS_WAGE_BASE_2026))
    if extra_wages == 0:
        return None                      # the rule does not touch this family

    extra_tax = round(extra_wages * SS_RATE)
    days = (EFFECTIVE - TODAY).days

    score = 60                           # base for a legal change
    reasons = ["legal change that touches this family (+60)"]
    if days <= 90:
        score += 15
        reasons.append(f"takes effect in {days} days (+15)")
    bonus = min(15, extra_tax // 50)     # bigger impact, higher priority
    if bonus:
        score += bonus
        reasons.append(f"about ${extra_tax:,} more Social Security tax per year (+{bonus})")

    return {
        "id": f"ss-wage-base-2027-{case_id}",
        "type": "legal_change",
        "headline": "Social Security wage base rises in 2027: what it means for you",
        "keywords": ["tax", "income"],
        "facts": {
            "rule": "Social Security wage base",
            "wage_base_2026": SS_WAGE_BASE_2026,
            "wage_base_2027": SS_WAGE_BASE_2027,
            "salary": salary,
            "extra_wages_taxed": extra_wages,
            "extra_social_security_tax_per_year": extra_tax,
            "effective_date": EFFECTIVE.isoformat(),
            "verified_facts": verified_facts(case_id),
        },
        "simulated": False,
        "family_ids": [case_id],
        "priority_score": score,
        "priority": level_for(score),
        "priority_reasons": reasons,
    }


def run() -> list[dict]:
    events = [ev for cid in list_case_ids() if (ev := wage_base_event(cid))]
    events.sort(key=lambda e: e["priority_score"], reverse=True)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    return events


if __name__ == "__main__":
    evs = run()
    print(f"{len(evs)} legal events\n")
    for e in evs:
        print(f"[{e['priority'].upper():6} {e['priority_score']:3}] {e['family_ids'][0]:22} {e['headline']}")
        print("          ", "; ".join(e["priority_reasons"]))