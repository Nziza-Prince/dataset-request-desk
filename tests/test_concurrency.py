import os
import subprocess
import sys


def test_real_database_concurrency(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "tests.concurrency_scenario"],
        env={**os.environ, "DATABASE_PATH": str(tmp_path / "race.sqlite3")},
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: concurrent assignment" in result.stdout
