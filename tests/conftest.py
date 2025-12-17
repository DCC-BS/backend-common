import sys
from pathlib import Path

# Ensure src/ is on the path for tests without requiring installation.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
