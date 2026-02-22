"""
Grading services: text judging, vision grading, scoring, classification, rubrics.
"""
import json

from config.settings import (
    JUDGE_MODEL,
    VISION_MODEL,
    GRADER_MODEL,
    LEARNING_RATE,
    HINT_PENALTY,
    ATTEMPT_PENALTY,
)
from config.prompts import (
    JUDGE_SYSTEM_PROMPT,
    VISION_GRADING_SYSTEM_PROMPT,
    CLASSIFIER_SYSTEM_PROMPT,
    CLASSIFIER_USER_TEMPLATE,
)
from services.openai_client import client as oai, call_model


# --- Rubrics ---

RUBRICS = {
    "Algorithm Description": {
        "core_idea": 4,
        "key_mechanism": 3,
        "complexity": 2,
        "clarity": 1,
    },
    "Algorithm Execution": {
        "step_sequence": 5,
        "intermediate_states": 3,
        "final_answer": 2,
    },
    "Proof of Correctness": {
        "correct_invariant": 4,
        "logical_steps": 3,
        "edge_cases": 2,
        "clarity": 1,
    },
}

ALLOWED_TYPES = ["Algorithm Description", "Algorithm Execution", "Proof of Correctness"]


# --- Classification ---

def classify_question(question: str) -> dict:
    messages = [
        {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": CLASSIFIER_USER_TEMPLATE.format(question=question)},
    ]
    raw = call_model(messages, temperature=0)
    data = json.loads(raw)
    if data.get("question_type") not in ALLOWED_TYPES:
        data["question_type"] = "Algorithm Description"
        data["confidence"] = 0.0
    return data


# --- Rubric-based grading ---

def grade_answer(question: str, model_solution: str, student_answer: str, rubric: dict) -> dict:
    rubric_text = "\n".join([f"{k} ({v} points)" for k, v in rubric.items()])
    messages = [
        {"role": "system", "content": "You are a strict computer science professor grading a Graphs & Algorithms exam."},
        {"role": "user", "content": f"""
You MUST grade only according to the rubric.
Deduct conservatively.
Return STRICT JSON ONLY (no markdown).

RUBRIC:
{rubric_text}

QUESTION:
{question}

MODEL SOLUTION:
{model_solution}

STUDENT ANSWER:
{student_answer}

Return this JSON shape exactly:
{{
  "breakdown": {{
    {", ".join([f'"{k}": number' for k in rubric.keys()])}
  }},
  "total_score": number,
  "feedback": "concise but precise feedback"
}}
"""},
    ]
    raw = call_model(messages, temperature=0.1)
    return json.loads(raw)


def validate_score(response: dict, rubric: dict) -> dict:
    breakdown = response.get("breakdown", {})
    fixed = {}
    for k, max_pts in rubric.items():
        v = breakdown.get(k, 0)
        try:
            v = float(v)
        except Exception:
            v = 0
        fixed[k] = max(0, min(max_pts, v))

    response["breakdown"] = fixed
    response["total_score"] = sum(fixed.values())
    if "feedback" not in response:
        response["feedback"] = "No feedback provided."
    return response


# --- Text judging ---

def judge_answer(problem_text: str, solution_text: str, student_answer: str) -> dict:
    try:
        response = oai.chat.completions.create(
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
        result = json.loads(response.choices[0].message.content)
    except json.JSONDecodeError:
        return {
            "correctness": 0.0,
            "feedback": "Unable to parse grading response. Please try again.",
            "is_complete": False,
        }
    except Exception as e:
        return {
            "correctness": 0.0,
            "feedback": f"Grading error: {e}. Please try again.",
            "is_complete": False,
        }

    result["correctness"] = max(0.0, min(1.0, float(result.get("correctness", 0.0))))
    return result


# --- Vision grading ---

def grade_image_answer(
    image_base64: str,
    question_text: str,
    solution_text: str,
    rag_context: str = "",
) -> dict:
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
                f"MODEL SOLUTION (this is the CORRECT answer \u2014 grade against this):\n"
                f"{solution_text}"
                f"{context_section}\n\n"
                "IMPORTANT: Grade the student's answer image below STRICTLY against "
                "the model solution above. Every claim of correct/incorrect MUST be "
                "verifiable from the model solution or lecture notes. Do NOT guess."
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

    response = oai.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {"role": "system", "content": VISION_GRADING_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
        max_tokens=6000,
    )

    raw = response.choices[0].message.content
    return json.loads(raw)


# --- Scoring ---

def compute_final_score(correctness: float, hints_used: int, attempts: int) -> float:
    penalty = HINT_PENALTY * hints_used + ATTEMPT_PENALTY * max(0, attempts - 1)
    return max(0.0, correctness - penalty)


def update_user_vector(
    user_vector: list[float],
    problem_vector: list[float],
    final_score: float,
) -> list[float]:
    new_vector = list(user_vector)
    for i in range(len(new_vector)):
        if problem_vector[i] > 0.1:
            delta = LEARNING_RATE * final_score * problem_vector[i]
            new_vector[i] = min(1.0, new_vector[i] + delta)
    return [round(v, 4) for v in new_vector]
