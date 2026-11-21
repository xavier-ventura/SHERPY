# src/tracker.py
import json
from pathlib import Path

from cases import list_case_ids, load_json
from profiling import build_profile

DATA = Path(__file__).resolve().parent.parent / "data"
EVENTS_FILE = DATA / "events.json"        # eventos manuales (mercado, legal, tranquilidad)
AUTO_FILE = DATA / "events_auto.json"     # generados por events_builder.py
MARKET_FILE = DATA / "events_market.json"   # generado por market_events.py
LEGAL_FILE = DATA / "events_legal.json"     # generado por events_legal.py

# ---------- configuración de prioridad (ajustad estos números) ----------
BASE_SCORE = {                 # solo para eventos manuales de events.json
    "market_drop": 70,
    "legal_change": 60,
    "plan_date": 50,
    "life_moment": 45,
    "reassurance": 10,
}
MANUAL_ADJUST = {"high": 20, "medium": 0, "low": -20}   # campo "priority" opcional
TECH_SEVERITY_BONUS = {"Significant": 40, "Elevated": 12}
LEVELS = [(80, "urgent"), (60, "high"), (40, "medium"), (0, "low")]

MAX_PER_FAMILY = 3     # píldoras por familia (de tipos distintos)
MAX_TOTAL = 30         # tope diario del asesor
MIN_SCORE = 40         # por debajo, no se envía nada (salvo tranquilidad)


# ---------- carga de eventos ----------

def load_events() -> list[dict]:
    events = []
    if EVENTS_FILE.exists():
        events += json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    if AUTO_FILE.exists():
        events += json.loads(AUTO_FILE.read_text(encoding="utf-8"))
    if MARKET_FILE.exists():
        events += json.loads(MARKET_FILE.read_text(encoding="utf-8"))
    if LEGAL_FILE.exists():
        events += json.loads(LEGAL_FILE.read_text(encoding="utf-8"))
    return events


# ---------- ¿a quién toca? ----------

def max_salary(fh: dict) -> float:
    incomes = fh.get("profile", {}).get("incomes", [])
    vals = [i.get("yearly", 0) for i in incomes if i.get("type") == "SALARY_AND_WAGES"]
    return max(vals, default=0)


def child_ages(fh: dict) -> list[int]:
    return [p["age"] for p in fh.get("case", {}).get("household", [])
            if p.get("age") is not None and str(p.get("role", "")).lower() == "child"]


def touches(event: dict, case_id: str) -> bool:
    if "family_ids" in event:                       # evento dirigido a familias concretas
        return case_id in event["family_ids"]
    fh = load_json(case_id, "financial-health")
    rule = event.get("applies_to", {})
    if "min_salary" in rule and max_salary(fh) < rule["min_salary"]:
        return False
    if "has_child_age" in rule and rule["has_child_age"] not in child_ages(fh):
        return False
    return True


# ---------- datos de cada familia usados en la prioridad ----------

_profiles: dict[str, dict] = {}

def _profile(case_id: str) -> dict:
    if case_id not in _profiles:
        _profiles[case_id] = build_profile(case_id)
    return _profiles[case_id]


def tech_exposure(case_id: str) -> dict | None:
    """Lee la fila 'Technology' de sectorConcentration (tabla con cabecera)."""
    ia = load_json(case_id, "investment-analysis")
    table = (ia.get("key_metrics", {}).get("concentrationAndDiversification", {})
               .get("sectorConcentration"))
    if not table or len(table) < 2:
        return None
    header = table[0]
    for row in table[1:]:
        if str(row[0]).lower().startswith("tech"):
            r = dict(zip(header, row))
            w = r.get("Weight (% of equity)")
            if isinstance(w, str):
                w = float(w.strip("%")) / 100
            return {"weight": w, "severity": r.get("Severity"), "driver": r.get("Driver")}
    return None


# ---------- prioridad ----------

def level_for(score: int) -> str:
    return next(name for threshold, name in LEVELS if score >= threshold)


def concern_bonus(event: dict, case_id: str) -> tuple[int, str | None]:
    """Suma puntos si las keywords del evento aparecen en las preocupaciones (O1, O2...)."""
    keywords = [k.lower() for k in event.get("keywords", [])]
    concerns = " ".join(_profile(case_id).get("concerns", [])).lower()
    hits = [k for k in keywords if k in concerns]
    if not hits:
        return 0, None
    bonus = min(20, 10 * len(hits))
    return bonus, f"conecta con sus preocupaciones: {', '.join(hits)} (+{bonus})"


def score_event(event: dict, case_id: str) -> dict:
    """Devuelve {score, level, reasons[]} para este evento y esta familia."""

    # A) Eventos de events_builder: ya traen prioridad base; el tracker solo suma lo de la familia
    if "priority_score" in event:
        score = event["priority_score"]
        reasons = list(event.get("priority_reasons", []))
        bonus, why = concern_bonus(event, case_id)
        if why:
            score = min(100, score + bonus)
            reasons.append(why)
        return {"score": score, "level": level_for(score), "reasons": reasons}

        # B) Eventos manuales de events.json
    score = BASE_SCORE.get(event.get("type"), 30)
    reasons = [f"tipo {event.get('type')} (+{score})"]

    days = event.get("days_away")
    if days is not None:
        b = 25 if days <= 30 else 15 if days <= 90 else 5 if days <= 365 else 0
        if b:
            score += b
            reasons.append(f"ocurre en {days} días (+{b})")

    # Caída de mercado tecnológica: la severidad medida en la cartera ya cubre la relevancia
    is_tech_drop = event.get("type") == "market_drop" and event.get("sector") == "technology"
    exp = tech_exposure(case_id) if is_tech_drop else None

    if exp:
        b = TECH_SEVERITY_BONUS.get(exp["severity"], 0)
        score += b
        reasons.append(f"tecnología = {exp['weight']:.1%} de sus acciones, "
                       f"severidad {exp['severity']} (+{b})")
    else:
        bonus, why = concern_bonus(event, case_id)
        if why:
            score += bonus
            reasons.append(why)
        if event.get("type") == "market_drop":
            risks = _profile(case_id).get("risk_by_person", {}).values()
            if any(str(r).lower() in ("growth", "aggressive") for r in risks):
                score += 10
                reasons.append("perfil de riesgo alto: más expuesto (+10)")

    manual = MANUAL_ADJUST.get(event.get("priority", "medium"), 0)
    if manual:
        score += manual
        reasons.append(f"prioridad manual {event['priority']} ({manual:+d})")

    if event.get("simulated"):
        score -= 5
        reasons.append("evento simulado (-5)")

    score = max(0, min(100, score))
    return {"score": score, "level": level_for(score), "reasons": reasons}


# ---------- consultas ----------

def families_for_event(event: dict) -> list[str]:
    return [cid for cid in list_case_ids() if touches(event, cid)]


def ranked_events_for_family(case_id: str) -> list[dict]:
    items = [{"family_id": case_id, "event": ev, **score_event(ev, case_id)}
             for ev in load_events() if touches(ev, case_id)]
    return sorted(items, key=lambda x: x["score"], reverse=True)


def daily_queue() -> list[dict]:
    """
    Cola priorizada de píldoras a generar hoy:
    - máx. MAX_PER_FAMILY por familia, sin repetir tipo de evento
    - máx. MAX_TOTAL en total
    - si una familia no tiene nada por encima de MIN_SCORE, recibe un mensaje de tranquilidad
    """
    queue = []
    for cid in list_case_ids():
        ranked = [r for r in ranked_events_for_family(cid)
                  if r["score"] >= MIN_SCORE and r["event"]["type"] != "reassurance"]

        picked, seen_types = [], set()
        for r in ranked:
            t = r["event"]["type"]
            if t in seen_types:
                continue
            picked.append(r)
            seen_types.add(t)
            if len(picked) == MAX_PER_FAMILY:
                break

        if not picked:
            calm = {"id": f"all-clear-{cid}", "type": "reassurance",
                    "headline": "Nothing needs your attention this month",
                    "simulated": False, "family_ids": [cid]}
            picked = [{"family_id": cid, "event": calm, **score_event(calm, cid)}]
        queue += picked

    return sorted(queue, key=lambda x: x["score"], reverse=True)[:MAX_TOTAL]


if __name__ == "__main__":
    for item in daily_queue():
        e = item["event"]
        print(f"[{item['level'].upper():6} {item['score']:3}] {item['family_id']:22} {e['headline']}")
        print("          ", "; ".join(item["reasons"]))