from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.chat import get_ai_reply, get_chat_history

router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    session_id: str
    message: str


@router.post("/chat")
def chat(payload: ChatRequest):
    try:
        reply = get_ai_reply(payload.session_id, payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat error: {exc}")
    return {"session_id": payload.session_id, "reply": reply}


@router.get("/chat/{session_id}/history")
def chat_history(session_id: str):
    history = get_chat_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"session_id": session_id, "messages": history}
