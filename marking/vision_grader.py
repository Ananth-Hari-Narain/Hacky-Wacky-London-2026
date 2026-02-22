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

_SYSTEM_PROMPT = """You are an expert computer science examiner and tutor giving detailed, \
educational feedback on a student's handwritten or typed answer.

You will receive:
1. The exam question
2. The MODEL SOLUTION (the correct, complete answer)
3. An image of the student's answer
4. LECTURE NOTES context (relevant theory, definitions, and worked examples)

Your job — be THOROUGH and EDUCATIONAL:
- Read and transcribe EVERYTHING the student wrote from the image.
- Compare their answer against the model solution LINE BY LINE, STEP BY STEP.
- For every part of the question, explain what the correct approach is (citing \
  the model solution) and whether the student did it correctly.
- If they made an error, explain EXACTLY what they did wrong, what the correct \
  answer should be, and WHY (referencing the lecture notes / theory where relevant).
- If they missed a step, explain what was missing and how to derive it.
- Give credit for correct work — explain WHY it is correct.
- Assign a score from 0 to 10 that accurately reflects partial credit.

Return STRICT JSON ONLY — no markdown, no extra keys:
{
  "transcription": "full, exact transcription of everything the student wrote",
  "score": 7.5,
  "max_score": 10,
  "overall_feedback": "Detailed 4-6 sentence summary. Reference the model solution \
explicitly. Explain what the student understood well and what gaps remain. Mention \
relevant theory from lecture notes.",
  "annotations": [
    {
      "label": "1",
      "y_position": 0.15,
      "type": "correct|error|missing|suggestion",
      "feedback": "Detailed feedback (2-3 sentences). State what the student wrote, \
what the correct answer is per the model solution, and explain the underlying concept."
    }
  ],
  "what_went_well": [
    "Detailed point referencing what the student did correctly and why it matters"
  ],
  "areas_for_improvement": [
    "Detailed point explaining the gap, what to study, and how to fix it"
  ]
}

Rules:
- y_position must reflect the actual vertical location in the student's image.
- type must be one of: correct, error, missing, suggestion.
- Each annotation feedback MUST be 2-3 sentences minimum. Reference the model \
  solution and explain the correct approach.
- overall_feedback MUST be 4-6 sentences. Compare the student's approach vs the \
  model solution. Reference lecture note concepts if provided.
- what_went_well: 2-4 detailed points.
- areas_for_improvement: 2-4 detailed points with specific study advice.
- Aim for 4-8 annotations covering every distinct part of the answer.
- For MISSING parts (things in the model solution the student didn't attempt), \
  create an annotation with type "missing" explaining what was expected.
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
        max_tokens=4000,
    )

    raw = response.choices[0].message.content
    return json.loads(raw)
