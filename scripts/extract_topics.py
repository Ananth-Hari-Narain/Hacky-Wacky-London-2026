"""
Extract topic tags from lecture PDFs.

Usage:
    python -m scripts.extract_topics [--lectures-dir ./lectures]
"""
import argparse
from pathlib import Path

from services.recommendations import extract_topics_from_dir


def main():
    parser = argparse.ArgumentParser(description="Extract topic tags from lecture PDFs")
    parser.add_argument("--lectures-dir", default="./lectures")
    args = parser.parse_args()

    lectures_dir = Path(args.lectures_dir).expanduser().resolve()
    if not lectures_dir.is_dir():
        print(f"Error: directory not found: {lectures_dir}")
        return

    tags = extract_topics_from_dir(lectures_dir)
    print(f"Extracted {len(tags)} unique tags")
    for i, tag in enumerate(tags):
        print(f"  {i + 1:3d}. {tag}")


if __name__ == "__main__":
    main()
