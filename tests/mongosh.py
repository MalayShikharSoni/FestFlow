"""Run the queries/*.js scripts with mongosh from tests (skipped when mongosh is not installed)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

MONGOSH = shutil.which("mongosh")
QUERIES = Path(settings.BASE_DIR) / "queries"

needs_mongosh = pytest.mark.skipif(MONGOSH is None, reason="mongosh is not on PATH")


def run_script(db_name: str, script: str, env: dict[str, str] | None = None) -> str:
    """Run queries/<script> against `db_name`; returns stdout, fails the test on a non-zero exit."""
    result = subprocess.run(
        [MONGOSH, "--quiet", f"{settings.MONGO_URI.rstrip('/')}/{db_name}", str(QUERIES / script)],
        capture_output=True, text=True, encoding="utf-8", timeout=180, env={**os.environ, **(env or {})},
    )
    assert result.returncode == 0, f"{script} failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout
