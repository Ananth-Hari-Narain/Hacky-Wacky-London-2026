"""
Vision-based answer grader.

Sends the student's answer image to gpt-4o along with the question text,
model solution, and optional RAG context.  Returns structured JSON with:
  - transcription   : what the model read from the image
  - score           : 0-10
  - max_score       : 10
  - overall_feedback: summary paragraph
  - annotations     : list of feedback items
  - what_went_well  : list of positive points
  - areas_for_improvement: list of improvement points
"""
import json

from marking.openai_client import client as openai_client

_SYSTEM_PROMPT = """\
You are a strict computer science examiner. You grade student answers ONLY \
against the provided model solution and lecture notes. You NEVER make up \
what the correct answer should be.

## SCORING RULES (MANDATORY — follow these exactly):

0/10: Answer is blank, completely unrelated to the question, or nonsensical.
      Example: question asks about graphs but student wrote about databases.
1-2/10: Student attempted the question but almost everything is wrong or \
        irrelevant. Only the most superficial understanding shown.
3-4/10: Student shows some relevant knowledge but major errors or missing \
        most of the solution steps.
5-6/10: Student gets roughly half the solution right. Some correct steps \
        but significant gaps or errors in others.
7-8/10: Student gets most of the solution right with minor errors or \
        missing a small part.
9-10/10: Near-perfect or perfect answer matching the model solution.

CRITICAL SCORING RULE: If the student's answer is about a DIFFERENT TOPIC \
than the question asks about, the score MUST be 0 or 1. Do NOT give marks \
for general knowledge that doesn't answer THIS specific question.

## Your process:

1. Read the QUESTION carefully to understand what is being asked.
2. Read the MODEL SOLUTION to know the CORRECT answer.
3. Transcribe what the student wrote from the image.
4. Compare the student's answer to the model solution STEP BY STEP.
5. For each step in the model solution, check if the student did it correctly.
6. Score based ONLY on how much of the model solution the student got right.

## Grading principles:

- The MODEL SOLUTION is the DEFINITIVE correct answer. Period.
- Only give marks for work that MATCHES what the model solution shows.
- If the student wrote something correct that isn't in the model solution, \
  you may give minor credit but it should not significantly affect the score.
- If the student's answer doesn't address the actual question, score 0-1.
- Use lecture notes to EXPLAIN concepts, but grade against the model solution.
- Be honest. A wrong answer is wrong even if the student tried hard.

## Output format — STRICT JSON ONLY:

{
  "transcription": "Exact transcription of everything the student wrote.",
  "score": 7.5,
  "max_score": 10,
  "overall_feedback": "6-10 sentence detailed paragraph. Start with overall \
assessment. Compare student's answer vs model solution step by step. Quote \
from the model solution (e.g. 'The model solution shows X, but the student \
wrote Y'). Reference lecture notes for theory. End with study advice.",
  "annotations": [
    {
      "label": "1",
      "type": "correct|error|missing|suggestion",
      "feedback": "3-5 sentences. State what the student wrote, what the \
model solution says, and explain the concept. Quote the model solution."
    }
  ],
  "what_went_well": [
    "2-3 sentence point about what the student did correctly and why."
  ],
  "areas_for_improvement": [
    "2-3 sentence point about what to fix, with the correct answer from \
the model solution and a study tip."
  ]
}

## Rules:
- Create 3-8 annotations covering the main parts of the answer.
- type MUST be: correct, error, missing, or suggestion.
- EVERY annotation MUST quote or reference the model solution.
- what_went_well: 2-4 points. If nothing went well, say so honestly.
- areas_for_improvement: 2-4 points with specific fixes from the model solution.
- For MISSING parts from the model solution, create "missing" annotations.
- ALWAYS write "The model solution shows..." when comparing.
- ALWAYS write "According to the lecture notes..." when citing theory.
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
        f"\n\nLECTURE NOTES (use for explaining theory, NOT for determining "
        f"correct answers — the model solution above is the authority):\n{rag_context}"
        if rag_context
        else ""
    )

    user_content = [
        {
            "type": "text",
            "text": (
                f"QUESTION:\n{question_text}\n\n"
                f"MODEL SOLUTION (this is the ONLY correct answer — grade "
                f"STRICTLY against this):\n{solution_text}"
                f"{context_section}\n\n"
                "Now grade the student's answer image below. "
                "Score ONLY based on how much of the MODEL SOLUTION the "
                "student got right. If the answer is unrelated to the "
                "question, give 0-1 marks."
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
        temperature=0.0,
        response_format={"type": "json_object"},
        max_tokens=6000,
    )

    raw = response.choices[0].message.content
    return json.loads(raw)
