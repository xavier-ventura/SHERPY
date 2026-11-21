# src/cases.py
import json
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parent.parent / "data" / "cases"


def list_case_ids() -> list[str]:
    """Devuelve los IDs de las familias (nombres de carpeta dentro de data/cases)."""
    if not CASES_DIR.exists():
        raise FileNotFoundError(f"No existe la carpeta de casos: {CASES_DIR}")
    return sorted(p.name for p in CASES_DIR.iterdir() if p.is_dir())


def load_json(case_id: str, part: str) -> dict:
    """
    Carga un JSON de una familia.
    part: "financial-health", "investment-analysis", "signals" o "legal-values"
    """
    path = CASES_DIR / case_id / f"{part}.json"
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_persona(case_id: str) -> str:
    """Carga persona.md como texto."""
    path = CASES_DIR / case_id / "persona.md"
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}")
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    ids = list_case_ids()
    print("Familias:", ids)
    if ids:
        fh = load_json(ids[0], "financial-health")
        print("Claves de financial-health.json:", list(fh.keys()))