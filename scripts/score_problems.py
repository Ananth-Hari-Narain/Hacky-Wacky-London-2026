"""
Score all problems against topic tags.

Usage:
    python -m scripts.score_problems
"""
from services.recommendations import score_all_problems


def main():
    count = score_all_problems()
    print(f"Scored {count} problem(s)")


if __name__ == "__main__":
    main()
