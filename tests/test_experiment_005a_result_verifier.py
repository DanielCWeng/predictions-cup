from pathlib import Path

import pytest
from scripts.experiment_005a.verify_result_package import audit_bh, parse_bool


def test_parse_bool() -> None:
    assert parse_bool("true") is True
    assert parse_bool("False") is False
    with pytest.raises(RuntimeError):
        parse_bool("maybe")


def test_audit_bh_rejects_invalid_q(tmp_path: Path) -> None:
    path = tmp_path / "summary.csv"
    path.write_text("bh_q,bh_reject_5pct\n1.2,false\n")
    with pytest.raises(RuntimeError, match="invalid BH"):
        audit_bh(path)


def test_audit_bh_counts_rejections(tmp_path: Path) -> None:
    path = tmp_path / "summary.csv"
    path.write_text(
        "intersection_p,bh_q,bh_reject_5pct\n"
        "0.01,0.02,true\n"
        "0.4,0.4,false\n"
    )
    result = audit_bh(path)
    assert result["rows"] == 2
    assert result["bh_rejections"] == 1
