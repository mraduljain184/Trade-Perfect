"""Thin wrapper: regenerate the automated test cases (run from backend/).

    python scripts/generate_test_cases.py && python -m pytest tests/generated -q --confcutdir=tests/generated
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.testgen import generate_all  # noqa: E402

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "tests/generated"
    for path in generate_all(out, "../data"):
        print(f"wrote {path}")
