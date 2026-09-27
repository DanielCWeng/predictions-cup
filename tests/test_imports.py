from __future__ import annotations


def test_package_imports() -> None:
    import predictions_cup

    assert predictions_cup.__version__ == "0.1.0"
