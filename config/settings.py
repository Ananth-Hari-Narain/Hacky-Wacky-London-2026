import os

from dotenv import load_dotenv

load_dotenv()

# --- MongoDB ---
MONGODB_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI (or MONGO_URI) not found in .env")

MONGODB_DB = os.getenv("MONGODB_DB", "question_bank")

# Collection names
QUESTIONS_COLLECTION = os.getenv("MONGODB_COLLECTION", "questions")
USERS_COLLECTION = "users"
TOPICS_COLLECTION = "topics"
PROBLEM_VECTORS_COLLECTION = "problem_vectors"
SOLUTION_CHUNKS_COLLECTION = "solution_chunks"
NOTES_CHUNKS_COLLECTION = "notes_chunks"
GRADER_DB = "grader"
SUBMISSIONS_COLLECTION = "submissions"
IMAGE_SUBMISSIONS_COLLECTION = "image_submissions"
CHAT_SESSIONS_COLLECTION = "chat_sessions"

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found in .env")

# Model names
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNKING_MODEL = "gpt-4o-mini"
HINTS_MODEL = "gpt-4o-mini"
JUDGE_MODEL = "gpt-4o-mini"
SCORING_MODEL = "gpt-4o-mini"
EXTRACT_TOPICS_MODEL = "gpt-4o-mini"
VISION_MODEL = "gpt-4o"
CHAT_MODEL = "gpt-4o"
GRADER_MODEL = "gpt-4o"

# --- Pipeline tuning ---
EMBED_BATCH_SIZE = 100
CROP_MARGIN_TOP = 15.0
CROP_MARGIN_BOTTOM = 20.0
MIN_TEXT_FOR_HINTS = 150
MAX_LECTURE_CHARS = 400_000

# --- Scoring ---
LEARNING_RATE = 0.15
HINT_PENALTY = 0.1
ATTEMPT_PENALTY = 0.15

# --- Directories ---
PDF_DIR = os.getenv("PDF_DIR", "./pdfs")
SOLUTIONS_DIR = os.getenv("SOLUTIONS_DIR", "./solutions")
LECTURES_DIR = os.getenv("LECTURES_DIR", "./lectures")
