# src/generate_all.py
"""
Genera las píldoras de la cola diaria y las guarda en data/pills.json.

Uso:
  python .\\src\\generate_all.py            # genera solo las que faltan
  python .\\src\\generate_all.py --force    # regenera todas
  python .\\src\\generate_all.py --only michael-carter   # solo una familia
"""
import argparse
import json
import time
from pathlib import Path

from cases import CASES_DIR, load_json
from pill_generator import generate_pill
from profiling import build_profile
from tracker import daily_queue, max_salary, tech_exposure

OUT = Path(__file__).resolve().parent.parent / "data" / "pills.json"

# Qué "moment" de signals.json corresponde a cada tipo de evento
MOMENT_FOR_TYPE = {
    "market_drop": "market",
    "legal_change": "rule",
    "life_moment": "life",
    "plan_date": "life",
}

# Topes del wage base de Seguridad Social (también están en legal-values.json, SSWageBaseByYear)
SS_WAGE_BASE_2026 = 184_500
SS_WAGE_BASE_2027 = 192_300
SS_RATE = 0.062


# ---------- cifras calculadas en código (el modelo solo las explica) ----------

def numbers_for(event: dict, family_id: str) -> dict:
    nums = dict(event.get("numbers", {}))

    if event["id"] == "ss-wage-base-2027":
        salary = max_salary(load_json(family_id, "financial-health"))
        extra_wages = max(0, min(salary, SS_WAGE_BASE_2027) - min(salary, SS_WAGE_BASE_2026))
        nums.update({
            "salary": salary,
            "wage_base_2026": SS_WAGE_BASE_2026,
            "wage_base_2027": SS_WAGE_BASE_2027,
            "extra_wages_taxed": extra_wages,
            "extra_social_security_tax_per_year": round(extra_wages * SS_RATE),
        })

    if event.get("type") == "market_drop" and event.get("sector") == "technology":
        exp = tech_exposure(family_id)
        if exp:
            nums.update({
                "technology_weight_of_equity": exp["weight"],
                "technology_severity": exp["severity"],
                "technology_driven_by": exp["driver"],
            })
    return nums


# ---------- hechos verificados de signals.json ----------

def verified_facts(event: dict, family_id: str) -> list[str]:
    path = CASES_DIR / family_id / "signals.json"
    if not path.exists():
        return []
    signals = json.loads(path.read_text(encoding="utf-8")).get("signals", [])
    moment = MOMENT_FOR_TYPE.get(event.get("type"))
    facts = []
    for s in signals:
        if s.get("moment") == moment:
            facts += s.get("touches_this_family", [])
    return facts


# ---------- main ----------

def load_existing() -> list[dict]:
    return json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []


def save(pills: list[dict]) -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(pills, ensure_ascii=False, indent=2), encoding="utf-8")


def main(force: bool = False, only: str | None = None) -> None:
    pills = [] if force else load_existing()
    done = {(p["family_id"], p["event_id"]) for p in pills}

    queue = daily_queue()
    if only:
        queue = [q for q in queue if q["family_id"] == only]
    print(f"{len(queue)} píldoras en la cola")

    for item in queue:
        fid, ev = item["family_id"], item["event"]
        if (fid, ev["id"]) in done:
            print("SKIP (ya generada):", fid, ev["id"])
            continue

        nums = numbers_for(ev, fid)
        nums["verified_facts"] = verified_facts(ev, fid)

        try:
            pill = generate_pill(build_profile(fid), ev, nums)
        except Exception as e:
            print("ERROR:", fid, ev["id"], "->", e)
            continue

        # Si se regenera una píldora, se sustituye la anterior
        pills = [p for p in pills if (p["family_id"], p["event_id"]) != (fid, ev["id"])]
        pills.append({
            "family_id": fid,
            "event_id": ev["id"],
            "event_type": ev["type"],
            "headline": ev["headline"],
            "simulated": ev.get("simulated", False),
            "priority_score": item["score"],
            "priority_level": item["level"],
            "priority_reasons": item["reasons"],
            "numbers_used": {k: v for k, v in nums.items() if k != "verified_facts"},
            "status": "pending",
            **pill.model_dump(),
        })
        save(pills)                      # guarda tras cada píldora: si falla, no se pierde nada
        print("OK:", fid, ev["id"])
        time.sleep(1)                    # el proxy admite 60 peticiones por minuto

    pills.sort(key=lambda p: p["priority_score"], reverse=True)
    save(pills)
    print(f"\nListo: {len(pills)} píldoras en {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenera todas las píldoras")
    ap.add_argument("--only", help="solo esta familia, por ejemplo michael-carter")
    args = ap.parse_args()
    main(force=args.force, only=args.only)