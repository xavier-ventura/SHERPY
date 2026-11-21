import json
from pathlib import Path

import pytest

CASES_DIR = Path(__file__).resolve().parents[1] / "cases"
CASE_IDS = ["daniel-emily-johnson", "michael-carter", "samantha-lewis", "timothy-melanie-weah"]


def load(case_id: str, part: str):
    return json.loads((CASES_DIR / case_id / f"{part}.json").read_text())


@pytest.fixture(scope="session")
def legal():
    return load(CASE_IDS[0], "legal-values")


@pytest.fixture(scope="session")
def fh():
    cache: dict = {}

    def _get(case_id: str):
        if case_id not in cache:
            cache[case_id] = load(case_id, "financial-health")
        return cache[case_id]

    return _get
