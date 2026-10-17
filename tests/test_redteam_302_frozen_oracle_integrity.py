"""Independent reviewer verification that #302 retains EXACT frozen strict oracles.
Original RED #293/#298 and passing X1/X2 #292 file blobs must remain unmodified.
Review branch runs existing original assertions via normal CI; no rewrites.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import pytest

FROZEN = (
    ("test_alpha2_x1x2_consumer_falsification.py", "7f25b308b4a5ec7131aaf87ad719de97c3fa0f15"),
    ("test_alpha2_x1_loader_depth_only_strict.py", "ab667345a38a52d546f87cf717624b3fd4926c18"),
    ("test_redteam_x1_original_strict_acceptance.py", "b77e1da5f1823e4462362dfc24eb26c00805e2b0"),
    ("test_redteam_x2_original_strict_acceptance.py", "d7680140f646022af3555a7d603d53251f8449b6"),
)

@pytest.mark.parametrize("name,expected_blob", FROZEN)
def test_unchanged_frozen_strict_test_blob(name: str, expected_blob: str) -> None:
    data = (Path(__file__).resolve().parent / name).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\\x00" + data).hexdigest()
    assert actual == expected_blob
