"""
FastAPI application entry point.

Run with:
    uvicorn api.main:app --reload --port 8000
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api.routers import chat, grading, problems, questions, upload, users

app = FastAPI(title="Adaptive Learning Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(problems.router)
app.include_router(grading.router)
app.include_router(upload.router)
app.include_router(users.router)
app.include_router(chat.router)
app.include_router(questions.router)

_static_dir = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(_static_dir / "index.html")
