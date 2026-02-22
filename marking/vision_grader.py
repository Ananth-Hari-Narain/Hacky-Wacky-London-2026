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

_SYSTEM_PROMPT = """You are an expert computer science examiner and tutor. You give \
EXTREMELY DETAILED, EDUCATIONAL feedback on a student's handwritten or typed answer. \
Your feedback must be so thorough that a student could learn the entire topic just by \
reading it.

You will receive:
1. The exam question
2. The MODEL SOLUTION (the correct, complete answer)
3. An image of the student's answer
4. LECTURE NOTES context (relevant theory, definitions, and worked examples)

## Your job — be EXHAUSTIVE and EDUCATIONAL:

1. TRANSCRIBE everything the student wrote — every line, symbol, diagram, and annotation.
2. Go through the model solution STEP BY STEP. For EACH step:
   a. State what the model solution does and why (cite the underlying theory/definition).
   b. State what the student wrote for that step.
   c. If CORRECT: explain exactly WHY it is correct, referencing the relevant concept.
   d. If WRONG: explain the specific error, show the correct working, and explain \
      the theory they misunderstood.
   e. If MISSING: explain what they should have written and how to derive it from \
      first principles.
3. Be generous with partial credit but strict about conceptual errors.
4. Reference lecture note concepts/definitions wherever possible.

## Output format — STRICT JSON ONLY (no markdown, no extra keys):

{
  "transcription": "Full exact transcription of everything the student wrote, \
line by line. Include mathematical notation, diagrams described, arrows, etc.",
  "score": 7.5,
  "max_score": 10,
  "overall_feedback": "DETAILED 6-10 sentence paragraph. Start with an overall \
assessment. Then compare the student's approach vs the model solution step by step. \
Identify the key conceptual strengths and gaps. Reference specific lecture note \
definitions or theorems. End with specific advice on what to study next and how to \
improve. This should read like a mini-tutorial.",
  "annotations": [
    {
      "label": "1",
      "y_position": 0.15,
      "type": "correct|error|missing|suggestion",
      "feedback": "DETAILED feedback (3-5 sentences). First state exactly what the \
student wrote. Then state what the model solution says. Explain the underlying \
concept/theorem. If an error, show the correct derivation. If correct, explain why \
the approach works. Reference lecture notes where applicable."
    }
  ],
  "what_went_well": [
    "Detailed point (2-3 sentences) referencing what the student did correctly, WHY \
it demonstrates understanding, and which concept/theorem it relates to."
  ],
  "areas_for_improvement": [
    "Detailed point (2-3 sentences) explaining the specific gap, what concept to \
review, a concrete study tip, and how to approach similar problems in future."
  ]
}

## CRITICAL Rules:

- y_position: 0.0 = very top of image, 1.0 = very bottom. Place annotations at the \
  EXACT vertical position where that part of the student's answer appears.
- type: MUST be one of: correct, error, missing, suggestion
- Create 5-10 annotations — one for EVERY distinct step, concept, or section.
- EVERY annotation feedback MUST be 3-5 sentences. Never write short 1-sentence feedback.
- overall_feedback MUST be 6-10 sentences. This is the most important field — make it \
  a thorough mini-tutorial.
- what_went_well: 3-5 detailed points (2-3 sentences each).
- areas_for_improvement: 3-5 detailed points (2-3 sentences each) with specific study advice.
- For MISSING parts (anything in the model solution the student didn't attempt), \
  create a "missing" annotation explaining what was expected and how to derive it.
- ALWAYS reference the model solution explicitly ("The model solution shows...").
- ALWAYS reference lecture note concepts when available ("According to the lecture notes...").
- Be encouraging but honest. Students learn best from thorough explanations.
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
        max_tokens=6000,
    )

    raw = response.choices[0].message.content
    return json.loads(raw)
