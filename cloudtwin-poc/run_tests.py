"""Test runner for all CloudTwin test suites (H2 - H8).

Usage (from cloudtwin-poc directory):
    python run_tests.py

If 'python' resolves to a bare system interpreter that lacks the project
dependencies, the script re-executes itself through the local .venv
automatically, so the correct packages are always used.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Self-healing: if key packages are missing, re-invoke through the local .venv
# ---------------------------------------------------------------------------
def _venv_python() -> Path | None:
    for candidate in [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ]:
        if candidate.exists():
            return candidate
    return None


def _has_required_packages() -> bool:
    try:
        import botocore  # noqa: F401
        import fastapi   # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    if not _has_required_packages():
        venv_py = _venv_python()
        if venv_py and str(venv_py) != sys.executable:
            result = subprocess.run([str(venv_py), __file__] + sys.argv[1:])
            sys.exit(result.returncode)
        else:
            print(
                "[WARNING] Required packages (boto3, fastapi) not found.\n"
                "Activate the virtual environment or run:\n"
                f"  .venv\\Scripts\\python.exe -m pip install -r requirements.txt",
                file=sys.stderr,
            )

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(ROOT / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
