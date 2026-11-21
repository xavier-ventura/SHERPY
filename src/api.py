"""
HTTP API for the advisor inbox and the family chat.

    pip install fastapi uvicorn
    uvicorn api:app --app-dir src --reload          # from the repo root
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import inbox
from cases import list_case_ids
from chat import chat

app = FastAPI(title="SHERPY: advisor inbox and family chat")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ChatRequest(BaseModel):
    family_id: str
    message: str


class ApproveRequest(BaseModel):
    text: str | None = None      # advisor's edited text; empty = approve the draft as is


@app.get("/families")
def families():
    return list_case_ids()


# ---- advisor side ----
@app.post("/inbox/build")
def build(reset: bool = False):
    items = inbox.build_inbox(reset=reset)
    return {"pending": sum(i["status"] == "pending" for i in items)}


@app.get("/inbox")
def get_inbox(status: str | None = "pending"):
    items = inbox.list_items(status)
    return {"count": len(items),
            "summary": f"{len(items)} message(s) {status or 'in total'}, most urgent first",
            "items": items}


@app.post("/inbox/{item_id:path}/approve")
def approve(item_id: str, body: ApproveRequest | None = None):
    try:
        return inbox.decide(item_id, True, body.text if body else None)
    except KeyError:
        raise HTTPException(404, "item not found")


@app.post("/inbox/{item_id:path}/reject")
def reject(item_id: str):
    try:
        return inbox.decide(item_id, False)
    except KeyError:
        raise HTTPException(404, "item not found")


# ---- family side ----
@app.get("/family/{family_id}/messages")
def messages(family_id: str):
    return inbox.family_messages(family_id)


@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    if req.family_id not in list_case_ids():
        raise HTTPException(404, "family not found")
    return {"reply": chat(req.family_id, req.message)}