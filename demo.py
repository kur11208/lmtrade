"""Offline interview demo. Run from any working directory with Python 3.10+."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "etf_mobile"))

from interview_demo import main


if __name__ == "__main__":
    raise SystemExit(main())
