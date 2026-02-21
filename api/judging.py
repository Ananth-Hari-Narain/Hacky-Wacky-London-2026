"""
judging.py

Core logic for comparing a user's answer against an exemplar solution
using GPT-4o.
"""

import json

from openai import OpenAI

JUDGE_MODEL = "gpt-4o-mini"

JUDGE_SYSTEM_PROMPT = """\
You are an academic grader. You will receive:
1. The problem text.
2. The exemplar solution.
3. The student's answer.

Your job is to evaluate the student's answer and return a JSON object with:
- "correctness": a float from 0.0 to 1.0 indicating how correct the answer is
  (0.0 = completely wrong, 0.5 = partially correct, 1.0 = fully correct)
- "feedback": a short paragraph explaining what the student got right and wrong,
  and what they should focus on. Be constructive and specific.
- "is_complete": boolean, true if the answer is substantially complete enough
  to move on (correctness >= 0.7)

Use the full 0.0–1.0 range. Be fair but rigorous.\
"""


def judge_answer(
    client: OpenAI,
    problem_text: str,
    solution_text: str,
    student_answer: str,
) -> dict:
    """
    Compare student answer to the exemplar solution.

    Returns {"correctness": float, "feedback": str, "is_complete": bool}
    """
    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Problem:\n{problem_text}\n\n"
                    f"Exemplar solution:\n{solution_text}\n\n"
                    f"Student's answer:\n{student_answer}"
                ),
            },
        ],
    )

    return json.loads(response.choices[0].message.content)
