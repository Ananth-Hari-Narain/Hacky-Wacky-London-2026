"""
Tutor chat session management.

Each session stores context (grading details) and a running conversation.
get_ai_reply() calls gpt-4o with the full history and returns the reply.
"""
from typing import Optional

from config.settings import CHAT_MODEL
from config.prompts import CHAT_SYSTEM_PROMPT
from models.db import get_chat_session, append_chat_message
from services.openai_client import client as openai_client


def _build_context_block(context: dict) -> str:
    annotations_text = "\n".join(
        f"  [{a.get('label')}] ({a.get('type', '')}) {a.get('feedback', '')}"
        for a in context.get("annotations", [])
    ) or "  None"

    went_well = "\n".join(
        f"  - {p}" for p in context.get("what_went_well", [])
    ) or "  None"

    improvements = "\n".join(
        f"  - {p}" for p in context.get("areas_for_improvement", [])
    ) or "  None"

    return (
        f"QUESTION:\n{context.get('question_text', 'N/A')}\n\n"
        f"MODEL SOLUTION:\n{context.get('solution_text', 'N/A')}\n\n"
        f"STUDENT'S ANSWER (transcribed by AI):\n{context.get('student_transcription', 'N/A')}\n\n"
        f"SCORE: {context.get('score', 'N/A')}/10\n\n"
        f"OVERALL FEEDBACK:\n{context.get('overall_feedback', 'N/A')}\n\n"
        f"ANNOTATIONS:\n{annotations_text}\n\n"
        f"WHAT WENT WELL:\n{went_well}\n\n"
        f"AREAS FOR IMPROVEMENT:\n{improvements}"
    )


def get_ai_reply(session_id: str, user_message: str) -> str:
    session = get_chat_session(session_id)
    if not session:
        raise ValueError(f"Chat session '{session_id}' not found.")

    context = session.get("context", {})
    system_content = CHAT_SYSTEM_PROMPT + "\n\n" + _build_context_block(context)

    messages = [{"role": "system", "content": system_content}]
    for msg in session.get("messages", []):
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    response = openai_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=800,
    )
    reply = response.choices[0].message.content

    append_chat_message(session_id, "user", user_message)
    append_chat_message(session_id, "assistant", reply)

    return reply


def get_chat_history(session_id: str) -> Optional[list]:
    session = get_chat_session(session_id)
    if not session:
        return None
    return session.get("messages", [])
