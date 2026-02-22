"""
Tutor chat session management.

Each session stores:
  - context  : grading details (question, solution, score, annotations)
  - messages : running conversation history (user + assistant turns)

get_ai_reply() appends the new user message, calls gpt-4o with the full
history, saves the assistant reply, and returns the reply text.
"""
from typing import Optional

from marking.db import get_chat_session, append_chat_message
from marking.openai_client import client as openai_client

_SYSTEM_PROMPT = """You are a knowledgeable and encouraging computer science tutor.
A student has just submitted an exam answer that has been automatically graded.
You have access to the question, the correct solution, the student's answer, their score, and
the specific feedback annotations.

Your role:
- Help the student understand exactly where they went wrong and why.
- Explain underlying concepts clearly, using examples where helpful.
- Guide them towards the right answer through questions rather than just stating it.
- Be encouraging but accurate — do not validate incorrect reasoning.
- If asked something unrelated to the current question, gently redirect.

Keep replies concise and readable (use bullet points or numbered lists when helpful).
"""


def _build_context_block(context: dict) -> str:
    """Format the grading context into a clear system-level description."""
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
    """
    Process a user chat message for the given session.

    1. Loads the session from MongoDB.
    2. Builds a full message list (system context + history + new message).
    3. Calls gpt-4o.
    4. Persists both the user message and assistant reply.
    5. Returns the reply text.

    Raises ValueError if the session is not found.
    """
    session = get_chat_session(session_id)
    if not session:
        raise ValueError(f"Chat session '{session_id}' not found.")

    context = session.get("context", {})
    system_content = _SYSTEM_PROMPT + "\n\n" + _build_context_block(context)

    # Build message history for the API call
    messages = [{"role": "system", "content": system_content}]
    for msg in session.get("messages", []):
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.7,
        max_tokens=800,
    )
    reply = response.choices[0].message.content

    # Persist both turns
    append_chat_message(session_id, "user", user_message)
    append_chat_message(session_id, "assistant", reply)

    return reply


def get_chat_history(session_id: str) -> Optional[list]:
    """Return the message history for a session, or None if not found."""
    session = get_chat_session(session_id)
    if not session:
        return None
    return session.get("messages", [])
