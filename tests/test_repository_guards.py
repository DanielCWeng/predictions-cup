from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_env_files_are_ignored_but_example_is_trackable() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in gitignore
    assert ".env.*" in gitignore
    assert "!.env.example" in gitignore


def test_env_example_contains_no_secret_values() -> None:
    contents = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "PREDICTIONS_CUP_LOG_LEVEL=INFO" in contents
    assert "PREDICTIONS_CUP_TRADING_ENABLED=false" in contents
    assert "TEST_READ_SECRET_DO_NOT_LEAK" not in contents
    assert "TEST_TRADE_SECRET_DO_NOT_LEAK" not in contents
    assert "PREDICTIONS_CUP_SIG_READ_CREDENTIAL=<" not in contents
    assert "PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL=<" not in contents
