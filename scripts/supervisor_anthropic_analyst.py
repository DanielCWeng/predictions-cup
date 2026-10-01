#!/usr/bin/env python3
"""Minimal Anthropic Messages API adapter for the SUPERVISOR-001 analyst spool."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 6000
DEFAULT_TIMEOUT_SECONDS = 180.0


def main() -> int:
    prompt = sys.stdin.read()
    if not prompt.strip():
        raise SystemExit("analyst prompt is empty")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is required")

    model = os.environ.get(
        "PREDICTIONS_CUP_SUPERVISOR_ANTHROPIC_MODEL",
        DEFAULT_MODEL,
    ).strip()
    max_tokens = int(
        os.environ.get(
            "PREDICTIONS_CUP_SUPERVISOR_ANTHROPIC_MAX_TOKENS",
            str(DEFAULT_MAX_TOKENS),
        )
    )
    timeout = float(
        os.environ.get(
            "PREDICTIONS_CUP_SUPERVISOR_ANTHROPIC_TIMEOUT_SECONDS",
            str(DEFAULT_TIMEOUT_SECONDS),
        )
    )

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
            "user-agent": "predictions-cup-supervisor/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-4000:]
        print(json.dumps({
            "assessment": f"Anthropic HTTP {exc.code}: {detail}",
            "novel_findings": [],
            "hypotheses": [],
            "evidence_refs": [],
            "recommended_operator_checks": [],
            "recommended_code_investigations": [],
            "requested_actions": [],
            "urgency": "NOW",
            "confidence": 0.0,
        }))
        return 2
    except urllib.error.URLError as exc:
        print(json.dumps({
            "assessment": f"Anthropic transport error: {exc.reason}",
            "novel_findings": [],
            "hypotheses": [],
            "evidence_refs": [],
            "recommended_operator_checks": [],
            "recommended_code_investigations": [],
            "requested_actions": [],
            "urgency": "NEXT_HOUR",
            "confidence": 0.0,
        }))
        return 3

    payload = json.loads(raw)
    text = _extract_text(payload)
    parsed = _parse_json_response(text)
    parsed["_anthropic"] = {
        "model": payload.get("model"),
        "stop_reason": payload.get("stop_reason"),
        "usage": payload.get("usage"),
    }
    print(json.dumps(parsed, separators=(",", ":"), sort_keys=True))
    return 0


def _extract_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Anthropic response must be an object")
    content = payload.get("content")
    if not isinstance(content, list):
        raise ValueError("Anthropic response content must be a list")
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    if not parts:
        raise ValueError("Anthropic response contained no text blocks")
    return "\n".join(parts)


def _parse_json_response(text: str) -> dict[str, object]:
    candidates = [text.strip()]
    fence = chr(96) * 3
    if fence in text:
        for chunk in text.split(fence):
            cleaned = chunk.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].lstrip()
            if cleaned.startswith("{") and cleaned.endswith("}"):
                candidates.append(cleaned)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return {str(key): value for key, value in parsed.items()}
    return {
        "assessment": text[-50000:],
        "novel_findings": [],
        "hypotheses": [],
        "evidence_refs": [],
        "recommended_operator_checks": [],
        "recommended_code_investigations": [],
        "requested_actions": [],
        "urgency": "LATER",
        "confidence": 0.0,
    }


if __name__ == "__main__":
    raise SystemExit(main())
