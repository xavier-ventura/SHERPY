# src/inbox.py
"""
Advisor inbox: the AI drafts, the advisor approves. Nothing reaches the family until approved.

Items come from two places:
  - tracker.daily_queue(): one draft per (family, event) in today's queue
  - the chat: when the client agrees to "flag it to my advisor" (flag_to_advisor tool)

    python src/inbox.py            # build today's drafts from the queue
    python src/inbox.py --reset    # clear the inbox first (e.g. after changing --shock)
"""
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from chat import MODEL, family_context, llm
from tracker import daily_queue

INBOX_FILE = Path(__file__).resolve().parent.parent / "data" / "inbox.json"
_lock = threading.Lock()

SYSTEM_DRAFT = """You write a short message from a US family's financial advisor to their client. The advisor will read and approve it before it is sent.
Rules:
- At most 80 words. Warm, calm, plain words, no jargon.
- Speak directly to the client(s): singular if one adult, both if two. If the two adults react to risk differently, calm wording for the more cautious one.
- Use ONLY the facts you are given. Never invent figures. An "est_loss" is an estimate on their holdings, not money already lost.
- Market drops: normalise first (the plan was built for it), then what it means for this family.
- If the event is a reassurance, say nothing needs doing and that the advisor is watching.
- Never recommend buying or selling specific securities, never promise outcomes.
- End with one clear next step, such as proposing a short call; never promise a specific day or time
- Write in English. Output only the message text."""


def _load() -> list[dict]:
    try:
        return json.loads(INBOX_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    INBOX_FILE.parent.mkdir(exist_ok=True)
    tmp = INBOX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(INBOX_FILE)


def _draft(family_id: str, brief: str) -> str:
    messages = [{"role": "system", "content": SYSTEM_DRAFT},
                {"role": "system", "content": f"Family context (JSON):\n{family_context(family_id)}"},
                {"role": "user", "content": brief}]
    return (llm.chat.completions.create(model=MODEL, messages=messages).choices[0].message.content or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_inbox(reset: bool = False) -> list[dict]:
    """Create a draft for every item in today's queue that is not in the inbox yet."""
    with _lock:
        items = [] if reset else _load()
        have = {i["id"] for i in items}
        for q in daily_queue():
            ev, fid = q["event"], q["family_id"]
            iid = f"{fid}:{ev['id']}"
            if iid in have:
                continue
            brief = ("Event: " + json.dumps({k: ev.get(k) for k in ("headline", "type", "facts", "risk_profiles")},
                                            ensure_ascii=False)
                     + f"\nWhy it matters: {q['reasons']}")
            items.append({
                "id": iid, "family_id": fid, "source": "queue", "type": ev.get("type"),
                "headline": ev["headline"], "priority": q["level"], "priority_score": q["score"],
                "reasons": q["reasons"], "simulated": bool(ev.get("simulated")),
                "draft": _draft(fid, brief), "status": "pending", "final_text": None,
                "created_at": _now(), "decided_at": None,
            })
            print("drafted", iid)
        _save(items)
        return items


def flag(family_id: str | None, reason: str) -> dict:
    """Called from the chat when the client agrees to flag something to the advisor."""
    if not family_id:
        return {"ok": False, "error": "no family"}
    with _lock:
        items = _load()
        iid = f"{family_id}:flag:{datetime.now(timezone.utc).strftime('%H%M%S')}"
        items.append({
            "id": iid, "family_id": family_id, "source": "chat_flag", "type": "client_request",
            "headline": f"Client asked for you: {reason}", "priority": "high", "priority_score": 75,
            "reasons": ["the client asked in the chat to talk to their advisor"], "simulated": False,
            "draft": _draft(family_id, f"The client asked their advisor to follow up. Reason: {reason}. "
                                       "Write a short reply that acknowledges it and proposes a call."),
            "status": "pending", "final_text": None, "created_at": _now(), "decided_at": None,
        })
        _save(items)
    return {"ok": True, "id": iid}


def list_items(status: str | None = None) -> list[dict]:
    items = [i for i in _load() if not status or i["status"] == status]
    return sorted(items, key=lambda i: i["priority_score"], reverse=True)


def decide(item_id: str, approve: bool, text: str | None = None) -> dict:
    with _lock:
        items = _load()
        for i in items:
            if i["id"] == item_id:
                i["status"] = "approved" if approve else "rejected"
                i["final_text"] = (text or i["draft"]) if approve else None
                i["decided_at"] = _now()
                _save(items)
                return i
    raise KeyError(item_id)


def family_messages(family_id: str) -> list[dict]:
    """What the family sees: only approved messages."""
    out = [{"headline": i["headline"], "text": i["final_text"], "approved_at": i["decided_at"]}
           for i in _load() if i["family_id"] == family_id and i["status"] == "approved"]
    return sorted(out, key=lambda m: m["approved_at"], reverse=True)


if __name__ == "__main__":
    items = build_inbox(reset="--reset" in sys.argv)
    pending = [i for i in items if i["status"] == "pending"]
    print(f"\n{len(pending)} messages waiting for approval")