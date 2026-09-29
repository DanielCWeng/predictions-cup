import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_r25_ets_math_graph_reproducibility() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/research/verify_r25_ets_math_graph.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"status": "PASS"' in result.stdout
