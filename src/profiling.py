# src/profiling.py
"""
Construye un perfil COMPACTO de cada familia para pasárselo al LLM.
Entrada: data/cases/<family>/ + data/learning_history.json (simulado)
Salida: dict listo para json.dumps() en el prompt.
"""
import json
import re
from pathlib import Path

from cases import load_json, load_persona

LEARNING_FILE = Path(__file__).resolve().parent.parent / "data" / "learning_history.json"
MAX_SECTION_CHARS = 1500  # límite por sección de persona.md para no gastar crédito


# ---------- persona.md ----------

def split_persona_sections(text: str) -> dict[str, str]:
    """Divide persona.md en secciones D0..D6 según los encabezados."""
    pattern = re.compile(r"^#{1,4}\s*\**\s*(D[0-6])\b.*$", re.M)
    matches = list(pattern.finditer(text))
    sections = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[m.group(1)] = text[m.start():end].strip()
    return sections


def extract_tagged_lines(text: str, prefix: str) -> list[str]:
    """Saca las líneas de preocupaciones (O1, O2...) o tensiones (T1, T2...)."""
    pattern = re.compile(rf"^[\s\-\*\|#]*\**({prefix}\d+)\b.*$", re.M)
    return [m.group(0).strip(" -*|#") for m in pattern.finditer(text)]


# ---------- financial-health.json ----------

def extract_household(fh: dict) -> list[dict]:
    people = fh.get("case", {}).get("household", [])
    result = []
    for p in people:
        result.append({
            "name": p.get("name") or p.get("first_name"),
            "age": p.get("age"),
            "role": p.get("role") or p.get("relationship"),
            "risk_profile": p.get("risk_profile"),
        })
    return result


def extract_incomes(fh: dict) -> list[dict]:
    return fh.get("profile", {}).get("incomes", [])


def extract_goals(fh: dict) -> list[dict]:
    return fh.get("goals", {}).get("chartGoals", [])


def extract_top_moves(fh: dict) -> list[dict]:
    return fh.get("score", {}).get("top_moves", [])


# ---------- historial de aprendizaje (simulado) ----------

def load_learning(case_id: str) -> dict:
    """
    data/learning_history.json, formato esperado:
    {
      "samantha-lewis": {
        "seen": [{"topic": "ss_wage_base", "times": 2}],
        "understood": ["529_basics"],
        "needs_reinforcement": ["roth_conversion"],
        "streak_days": 3,
        "points": 120
      }
    }
    """
    if not LEARNING_FILE.exists():
        return {"seen": [], "understood": [], "needs_reinforcement": [],
                "streak_days": 0, "points": 0}
    data = json.loads(LEARNING_FILE.read_text(encoding="utf-8"))
    return data.get(case_id, {"seen": [], "understood": [], "needs_reinforcement": [],
                              "streak_days": 0, "points": 0})


# ---------- función principal ----------

def build_profile(case_id: str) -> dict:
    fh = load_json(case_id, "financial-health")
    persona_text = load_persona(case_id)
    sections = split_persona_sections(persona_text)

    # Secciones más útiles para redactar: riesgo, decisión, motivaciones (D4-D6).
    # Si no se detectan encabezados, se usa el principio del archivo como respaldo.
    if sections:
        persona_excerpt = {k: v[:MAX_SECTION_CHARS] for k, v in sections.items()
                           if k in ("D0", "D4", "D5", "D6")}
    else:
        persona_excerpt = {"raw": persona_text[:MAX_SECTION_CHARS * 3]}

    household = extract_household(fh)

    return {
        "case_id": case_id,
        "household": household,
        "risk_by_person": {p["name"]: p["risk_profile"] for p in household if p["name"]},
        "incomes": extract_incomes(fh),
        "goals": extract_goals(fh),
        "top_moves": extract_top_moves(fh),
        "concerns": extract_tagged_lines(persona_text, "O"),
        "tensions": extract_tagged_lines(persona_text, "T"),
        "persona_excerpt": persona_excerpt,
        "learning": load_learning(case_id),
    }


def profile_to_prompt(profile: dict) -> str:
    """Versión en texto lista para meter en el prompt."""
    return json.dumps(profile, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    import sys
    cid = sys.argv[1] if len(sys.argv) > 1 else "samantha-lewis"
    prof = build_profile(cid)
    print(json.dumps(prof, ensure_ascii=False, indent=2)[:4000])
    print("\nCaracteres totales:", len(profile_to_prompt(prof)))