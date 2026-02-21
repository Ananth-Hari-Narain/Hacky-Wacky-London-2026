from fastapi import FastAPI
from pydantic import BaseModel

from marking.rubrics import RUBRICS
from marking.classifier import classify_question
from marking.grader import grade_answer, validate_score
from marking.db import save_submission

app = FastAPI(title="AI Grader API")

class GradeRequest(BaseModel):
    question: str
    model_solution: str
    student_answer: str

@app.get("/")
def root():
    return {"message": "AI Grader API running!"}

@app.post("/grade")
def grade(payload: GradeRequest):
    question = payload.question
    model_solution = payload.model_solution
    student_answer = payload.student_answer

    # Stage 1
    cls = classify_question(question)
    question_type = cls["question_type"]

    rubric = RUBRICS.get(question_type)
    if rubric is None:
        return {"error": f"No rubric found for question type '{question_type}'", "classifier": cls}

    # Stage 2
    graded = grade_answer(question, model_solution, student_answer, rubric)

    # Validate
    validated = validate_score(graded, rubric)

    # Save
    save_submission({
        "question": question,
        "question_type": question_type,
        "classifier": cls,
        "student_answer": student_answer,
        "rubric_version": "v1.0",
        "model_version": "gpt-4o",
        "result": validated
    })

    return validated