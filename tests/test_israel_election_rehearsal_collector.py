"""Deterministic tests for bounded, observation-only Israel rehearsal tape."""
import importlib.util
from datetime import datetime
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/research/israel_election_rehearsal_collector.py"
spec = importlib.util.spec_from_file_location("israel_rehearsal_collector", MODULE_PATH)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def unix(iso):
    return datetime.fromisoformat(iso).timestamp()


def test_phase_changes_are_clock_driven_not_trade_permission():
    assert collector.phase(unix("2026-10-27T19:40:00+00:00")) == "WATCH"
    assert collector.phase(unix("2026-10-27T19:50:00+00:00")) == "APPROACH"
    assert collector.phase(unix("2026-10-27T20:00:00+00:00")) == "GO_WATCH"
    assert collector.phase(unix("2026-10-27T22:00:00+00:00")) == "POST_CLOSE"


def test_repricing_alert_is_observation_only(monkeypatch):
    out = []
    monkeypatch.setattr(collector, "emit", lambda kind, detail: out.append((kind, detail)))
    detector = collector.SwingDetector()
    detector.handle("example", 0.10, 0.12, 123)
    detector.handle("example", 0.16, 0.18, 124)
    alerts = [record for kind, record in out if kind == "price_acceleration_observed"]
    assert len(alerts) == 1
    assert alerts[0]["actionable"] is False and alerts[0]["verified_catalyst"] is False
    previous = len(out)
    detector.handle("example", 0.99, 0.02, 125)  # invalid inverted book
    assert len(out) == previous


def test_news_feed_is_only_aggregated_discovery(monkeypatch):
    xml = b'<rss><channel><item><title>Update</title><link>https://news.google.com/rss/articles/x</link><pubDate>Mon, 26 Oct 2026 09:44:00 GMT</pubDate><source>Example</source></item></channel></rss>'
    out = []
    monkeypatch.setattr(collector, "fetch", lambda *a, **kw: xml)
    monkeypatch.setattr(collector, "emit", lambda kind, detail: out.append((kind, detail)))
    known = set()
    assert collector.read_news(known) == 1
    assert collector.read_news(known) == 0
    assert out[0][1]["independently_verified"] is False
    assert out[0][1]["actionable"] is False


def test_tape_rotates_and_stays_bounded(monkeypatch, tmp_path):
    dest = tmp_path / "events.jsonl"
    monkeypatch.setattr(collector, "TAPE", dest)
    monkeypatch.setattr(collector, "MAX_FILE_BYTES", 500)
    for i in range(30):
        collector.emit("synthetic", {"sequence": i, "payload": "a" * 110})
    assert dest.is_file()
    assert dest.with_suffix(".jsonl.1").is_file()
    assert dest.stat().st_size <= 500
    assert dest.with_suffix(".jsonl.1").stat().st_size <= 500
