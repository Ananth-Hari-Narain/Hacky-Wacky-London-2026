import json
from marking.openai_client import call_model

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
"""}
    ]

    raw = call_model(messages, temperature=0.1)
    return json.loads(raw)

def validate_score(response: dict, rubric: dict) -> dict:
    # Ensure breakdown has all keys and scores are in range
    breakdown = response.get("breakdown", {})
    fixed = {}
    for k, max_pts in rubric.items():
        v = breakdown.get(k, 0)
        try:
            v = float(v)
        except Exception:
            v = 0
        if v < 0:
            v = 0
        if v > max_pts:
            v = max_pts
        fixed[k] = v

    total = sum(fixed.values())
    response["breakdown"] = fixed
    response["total_score"] = total
    if "feedback" not in response:
        response["feedback"] = "No feedback provided."
    return response