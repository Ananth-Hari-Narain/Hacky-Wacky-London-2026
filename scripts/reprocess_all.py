"""
Wipe and reprocess everything from scratch.

Clears questions, solution_chunks, notes_chunks, problem_vectors, and topics,
then re-runs the full pipeline: PDF chunking, embedding, image rendering,
solution ingestion, notes ingestion, topic extraction, scoring, and vector building.

Usage:
    python -m scripts.reprocess_all                 # full reprocess
    python -m scripts.reprocess_all --skip-notes     # skip lecture notes ingestion
    python -m scripts.reprocess_all --skip-topics    # skip topic extraction + scoring
    python -m scripts.reprocess_all --dry-run        # show what would be done without touching DB
"""
import argparse
from pathlib import Path

from config.settings import (
    PDF_DIR,
    SOLUTIONS_DIR,
    LECTURES_DIR,
    MONGODB_DB,
)
from models.db import get_client


COLLECTIONS_TO_CLEAR = [
    "questions",
    "solution_chunks",
    "notes_chunks",
    "problem_vectors",
    "topics",
]


def clear_collections(dry_run: bool = False):
    db = get_client()[MONGODB_DB]
    for name in COLLECTIONS_TO_CLEAR:
        coll = db[name]
        count = coll.count_documents({})
        if dry_run:
            print(f"  [dry-run] Would delete {count} doc(s) from {name}")
        else:
            if count > 0:
                coll.delete_many({})
                print(f"  Cleared {name} ({count} docs deleted)")
            else:
                print(f"  {name} already empty")


def main():
    parser = argparse.ArgumentParser(description="Wipe and reprocess everything from scratch")
    parser.add_argument("--skip-notes", action="store_true", help="Skip lecture notes ingestion")
    parser.add_argument("--skip-topics", action="store_true", help="Skip topic extraction, scoring, and vector building")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without touching the DB")
    parser.add_argument("--pdfs-dir", default=PDF_DIR, help="Problem PDFs directory")
    parser.add_argument("--solutions-dir", default=SOLUTIONS_DIR, help="Solution PDFs directory")
    parser.add_argument("--lectures-dir", default=LECTURES_DIR, help="Lecture notes directory")
    args = parser.parse_args()

    pdfs_dir = Path(args.pdfs_dir).resolve()
    solutions_dir = Path(args.solutions_dir).resolve()
    lectures_dir = Path(args.lectures_dir).resolve()

    # Show what we're working with
    pdf_files = sorted(pdfs_dir.glob("*.pdf")) if pdfs_dir.is_dir() else []
    sol_files = sorted(solutions_dir.glob("*.pdf")) if solutions_dir.is_dir() else []
    lec_files = sorted(lectures_dir.glob("*.pdf")) if lectures_dir.is_dir() else []

    print(f"PDFs dir:      {pdfs_dir} ({len(pdf_files)} files)")
    print(f"Solutions dir: {solutions_dir} ({len(sol_files)} files)")
    print(f"Lectures dir:  {lectures_dir} ({len(lec_files)} files)")
    print()

    if not pdf_files:
        print("No problem PDFs found — nothing to do.")
        return

    if args.dry_run:
        print("[DRY RUN] No changes will be made.\n")

    # Step 1: Clear collections
    print("Step 1: Clearing collections...")
    clear_collections(dry_run=args.dry_run)
    print()

    if args.dry_run:
        print(f"Step 2: Would process {len(pdf_files)} problem PDF(s)")
        if sol_files:
            print(f"         with solutions from {solutions_dir}")
        if not args.skip_notes and lec_files:
            print(f"Step 3: Would ingest {len(lec_files)} lecture note(s)")
        if not args.skip_topics:
            print("Step 4: Would extract topics, score problems, and build vectors")
        print("\nDry run complete.")
        return

    # Step 2: Process problem PDFs (chunk, embed, render images, match solutions, generate hints)
    print(f"Step 2: Processing {len(pdf_files)} problem PDF(s)...")
    from services.pipeline import process_problem_pdf

    total_questions = 0
    for pdf_path in pdf_files:
        print(f"  Processing {pdf_path.name}...")
        sol_dir = solutions_dir if solutions_dir.is_dir() else None
        docs = process_problem_pdf(pdf_path, sol_dir)
        total_questions += len(docs)
        print(f"    → {len(docs)} question(s)")
    print(f"  Total: {total_questions} question(s)\n")

    # Step 3: Ingest lecture notes (for RAG)
    if not args.skip_notes and lec_files:
        print(f"Step 3: Ingesting {len(lec_files)} lecture note(s)...")
        from services.ingest_notes import ingest_pdf

        total_chunks = 0
        for pdf_path in lec_files:
            print(f"  Ingesting {pdf_path.name}...")
            n = ingest_pdf(pdf_path, "notes_chunks")
            total_chunks += n
            print(f"    → {n} chunk(s)")
        print(f"  Total: {total_chunks} chunk(s)\n")
    elif args.skip_notes:
        print("Step 3: Skipped (--skip-notes)\n")
    else:
        print("Step 3: No lecture notes found, skipping.\n")

    # Step 4: Extract topics, score problems, build problem vectors
    if not args.skip_topics:
        if lectures_dir.is_dir() and lec_files:
            print("Step 4a: Extracting topics from lectures...")
            from services.recommendations import extract_topics_from_dir
            tags = extract_topics_from_dir(lectures_dir)
            print(f"  → {len(tags)} unique topic tag(s)\n")

            print("Step 4b: Scoring problems against topics...")
            from services.recommendations import score_all_problems
            scored = score_all_problems()
            print(f"  → {scored} problem(s) scored\n")

            print("Step 4c: Building problem vectors...")
            from services.recommendations import build_problem_vectors
            built = build_problem_vectors()
            print(f"  → {built} vector(s) built\n")
        else:
            print("Step 4: No lectures found, skipping topic extraction.\n")
    else:
        print("Step 4: Skipped (--skip-topics)\n")

    print("Done! All data reprocessed from scratch.")


if __name__ == "__main__":
    main()
