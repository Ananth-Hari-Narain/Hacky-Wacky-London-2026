import json
from marking.openai_client import call_model

ALLOWED_TYPES = [
    "Algorithm Description",
    "Algorithm Execution",
    "Proof of Correctness",
]

def classify_question(question: str) -> dict:
    messages = [
        {"role": "system", "content": "You are a strict CS exam classifier."},
        {"role": "user", "content": f"""
Classify the question into EXACTLY ONE of these:
- Algorithm Description
- Algorithm Execution
- Proof of Correctness

Return STRICT JSON:
{{
  "question_type": "Algorithm Description|Algorithm Execution|Proof of Correctness",
  "confidence": 0-1
}}

Question:
{question}
"""}
    ]

    raw = call_model(messages, temperature=0)
    data = json.loads(raw)

    # Hard guardrails
    if data.get("question_type") not in ALLOWED_TYPES:
        data["question_type"] = "Algorithm Description"
        data["confidence"] = 0.0

    return data