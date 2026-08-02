import sys
from pathlib import Path

# Ensure repository root is on sys.path so imports like `import infra...` work
ROOT = Path(__file__).resolve().parents[1]
ROOT_STR = str(ROOT)
if ROOT_STR not in sys.path:
    sys.path.insert(0, ROOT_STR)
