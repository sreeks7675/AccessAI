from pathlib import Path
import sys

# Locate repository root by searching upward for a 'backend' folder
FILE_PATH = Path(__file__).resolve()
REPO_ROOT = None
for candidate in FILE_PATH.parents:
    if (candidate / "backend").exists():
        REPO_ROOT = candidate
        break

if REPO_ROOT is None:
    # Fallback to three levels up from test_integration_branch/conftest.py
    REPO_ROOT = FILE_PATH.parents[3]

# Insert repo root so tests and direct python runs can `import backend`
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
