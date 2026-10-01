from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "supervisor_anthropic_analyst.py"
SPEC = importlib.util.spec_from_file_location("supervisor_anthropic_analyst", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_extract_text_concatenates_text_blocks() -> None:
    payload = {
        "content": [
            {"type": "text", "text": '{"assessment":"ok"}'},
            {"type": "tool_use", "id": "ignored"},
        ]
    }
    assert MODULE._extract_text(payload) == '{"assessment":"ok"}'


def test_parse_json_response_accepts_fenced_json() -> None:
    fence = chr(96) * 3
    parsed = MODULE._parse_json_response(
        fence + 'json\n{"assessment":"ok","requested_actions":[]}\n' + fence
    )
    assert parsed["assessment"] == "ok"
    assert parsed["requested_actions"] == []


def test_parse_json_response_falls_back_to_advisory_only() -> None:
    parsed = MODULE._parse_json_response("plain text")
    assert parsed["assessment"] == "plain text"
    assert parsed["requested_actions"] == []
    assert parsed["confidence"] == 0.0
