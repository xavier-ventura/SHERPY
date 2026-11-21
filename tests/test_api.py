import json

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from sherpy.agent.agent import build_agent
from sherpy.api.main import app
from sherpy.data.source import CaseSource, LocalBackend


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def client():
    # Solo herramientas de cálculo con args por defecto válidos (TestModel inventa args)
    app.state.agent = build_agent(TestModel(call_tools=["calc_emergency_fund", "calc_fica", "calc_federal_tax"]))
    with TestClient(app) as c:
        yield c
    del app.state.agent


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["llm_configured"] is True


def test_cases(client):
    ids = {c["id"] for c in client.get("/cases").json()}
    assert "michael-carter" in ids and len(ids) == 4


def test_chat_streams_and_returns_history(client):
    r = client.post("/chat", json={"case_id": "michael-carter", "message": "¿Cuántos meses me cubre mi efectivo?"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert "tool" in kinds and "delta" in kinds and kinds[-1] == "done"
    tool_names = {d["name"] for e, d in events if e == "tool"}
    assert "calc_emergency_fund" in tool_names
    history = events[-1][1]["history"]

    r2 = client.post("/chat", json={"case_id": "michael-carter", "message": "¿Y ahora?", "history": history})
    assert parse_sse(r2.text)[-1][0] == "done"


def test_chat_unknown_case(client):
    assert client.post("/chat", json={"case_id": "nope", "message": "hola"}).status_code == 404


async def test_local_source_sections():
    src = CaseSource(primary=None, fallback=LocalBackend())
    header = await src.get_case("michael-carter")
    assert "score" in header["parts"]["financial-health"]["sections"]
    d5 = await src.get_case("michael-carter", "persona", "D5")
    assert d5.startswith("## D5: Decision-making style")
    score = await src.get_case("michael-carter", "financial-health", "score")
    assert score["published_score"] == 26
    assert await src.get_case("michael-carter", "financial-health", "score") is score  # caché
