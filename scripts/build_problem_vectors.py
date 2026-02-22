"""
Copy topic_vectors from questions to problem_vectors collection.

Usage:
    python -m scripts.build_problem_vectors
"""
from services.recommendations import build_problem_vectors


def main():
    count = build_problem_vectors()
    print(f"Upserted {count} document(s) into problem_vectors")


if __name__ == "__main__":
    main()
