"""
Vision-based answer grader.

Sends the student's answer image to gpt-4o along with the question text,
model solution, and optional RAG context.  Returns structured JSON with:
  - transcription   : what the model read from the image
  - score           : 0-10
  - max_score       : 10
  - overall_feedback: summary paragraph
  - annotations     : list of located feedback items
  - what_went_well  : list of positive points
  - areas_for_improvement: list of improvement points
"""
import json

from marking.openai_client import client as openai_client

_SYSTEM_PROMPT = """You are an expert computer science examiner marking a student's handwritten or typed answer.

You will receive:
1. The exam question
2. The model solution (from lecture notes / official solutions)
3. An image of the student's answer
4. (Optional) Relevant context from lecture notes

Your job:
- Read and transcribe the student's answer from the image accurately.
- Compare it against the model solution, step by step.
- Identify specific errors, missing steps, misconceptions, and correct parts.
- Assign a score from 0 to 10.
- Provide located annotations: for each annotation give an approximate vertical
  position in the image (y_position: 0.0 = very top, 1.0 = very bottom).

Return STRICT JSON ONLY — no markdown, no extra keys:
{
  "transcription": "exact text / description of what the student wrote",
  "score": 7.5,
  "max_score": 10,
  "overall_feedback": "concise summary of performance",
  "annotations": [
    {
      "label": "1",
      "y_position": 0.15,
      "type": "correct|error|missing|suggestion",
      "feedback": "specific, actionable feedback for this part"
    }
  ],
  "what_went_well": ["point 1", "point 2"],
  "areas_for_improvement": ["point 1", "point 2"]
}

Rules:
- y_position must reflect the actual vertical location of the relevant part.
- type must be one of: correct, error, missing, suggestion.
- Keep each annotation feedback concise (max 20 words).
- overall_feedback should be 2-3 sentences.
- Aim for 2-6 annotations — more for detailed answers, fewer for short ones.
"""


def grade_image_answer(
    image_base64: str,
    question_text: str,
    solution_text: str,
    rag_context: str = "",
) -> dict:
    """
    Grade a student's answer image using gpt-4o vision.

    Args:
        image_base64 : base64-encoded PNG or JPEG of the student's answer.
        question_text: the exam question text.
        solution_text: the model/official solution text.
        rag_context  : optional extra context retrieved from lecture notes.

    Returns:
        Parsed JSON dict with score, annotations, and feedback.
    """
    context_section = (
        f"\n\nRELEVANT CONTEXT FROM LECTURE NOTES:\n{rag_context}"
        if rag_context
        else ""
    )

    user_content = [
        {
            "type": "text",
            "text": (
                f"QUESTION:\n{question_text}\n\n"
                f"MODEL SOLUTION:\n{solution_text}"
                f"{context_section}\n\n"
                "Please analyse the student's answer image below and grade it."
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{image_base64}",
                "detail": "high",
            },
        },
    ]

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
        max_tokens=2000,
    )

    raw = response.choices[0].message.content
    return json.loads(raw)
