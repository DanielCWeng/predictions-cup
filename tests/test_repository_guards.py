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
    assert "TOKEN=" not in contents
    assert "SECRET=" not in contents
    assert "PASSWORD=" not in contents
