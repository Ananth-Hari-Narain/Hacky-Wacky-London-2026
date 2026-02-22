"""
Upload endpoints for processing problem PDFs, solution PDFs, and lecture notes.
Processing happens synchronously.
"""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from config.settings import SOLUTIONS_DIR
from services.pipeline import process_problem_pdf
from services.ingest_notes import ingest_pdf

router = APIRouter(prefix="/upload", tags=["Upload"])


@router.post("/problems")
async def upload_problems(
    file: UploadFile = File(..., description="Problem sheet PDF"),
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        sol_dir = Path(SOLUTIONS_DIR)
        docs = process_problem_pdf(tmp_path, sol_dir if sol_dir.is_dir() else None)

        dest = Path("./pdfs") / file.filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_path, dest)
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "filename": file.filename,
        "questions_created": len(docs),
        "questions": [
            {
                "label": d["question"],
                "has_solution": bool(d.get("solution_text")),
                "hints": len(d.get("hints", [])),
            }
            for d in docs
        ],
    }


@router.post("/solutions")
async def upload_solutions(
    file: UploadFile = File(..., description="Solution PDF"),
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")

    contents = await file.read()

    dest = Path(SOLUTIONS_DIR) / file.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(contents)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        chunk_count = ingest_pdf(tmp_path, "solution_chunks")
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "filename": file.filename,
        "chunks_created": chunk_count,
        "collection": "solution_chunks",
    }


@router.post("/notes")
async def upload_notes(
    file: UploadFile = File(..., description="Lecture notes PDF"),
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")

    contents = await file.read()

    dest = Path("./lectures") / file.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(contents)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        chunk_count = ingest_pdf(tmp_path, "notes_chunks")
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "filename": file.filename,
        "chunks_created": chunk_count,
        "collection": "notes_chunks",
    }
