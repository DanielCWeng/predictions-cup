#!/usr/bin/env python3
"""Small, time-bounded Israel election-night RESEARCH rehearsal. No orders.

Gamma discovers 6 fixed public Polymarket contracts. Public CLOB WS provides
quote changes without REST polling. Google News RSS supplies discovery only,
NEVER independently verified results. All signals are observation-only.
"""
from __future__ import annotations
import argparse
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import time
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import websockets

ROOT = Path(__file__).resolve().parent
TAPE = ROOT / "israel_rehearsal_events.jsonl"
STATUS = ROOT / "israel_rehearsal_status.json"
MAX_FILE_BYTES = 6_000_000
END_EPOCH = datetime.fromisoformat("2026-10-28T08:00:00+00:00").timestamp()
EXIT_POLLS_EPOCH = datetime.fromisoformat("2026-10-27T20:00:00+00:00").timestamp()
SLOTS = {
    "israeli-legislative-election-winner": ("Likud", "Yashar", "Together"),
    "who-will-be-the-next-prime-minister-of-israel-after-the-next-election":
        ("Benjamin Netanyahu", "Gadi Eizenkot", "Naftali Bennett"),
}
RSS_URL = ("https://news.google.com/rss/search?q="
           "Israel%202026%20Knesset%20election%20results%20exit%20polls"
           "&hl=en-GB&gl=GB&ceid=GB:en")


def phase(epoch: float) -> str:
    if epoch < EXIT_POLLS_EPOCH - 600:
        return "WATCH"
    if epoch < EXIT_POLLS_EPOCH:
        return "APPROACH"
    if epoch < EXIT_POLLS_EPOCH + 7200:
        return "GO_WATCH"
    return "POST_CLOSE"


def fetch(url: str, max_bytes: int = 1_200_000) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "SIG-Cup-Election-Rehearsal/1.0"}),
                 timeout=8) as response:
        return response.read(max_bytes)


def discover() -> dict[str, dict]:
    result = {}
    for slug, needles in SLOTS.items():
        endpoint = "https://gamma-api.polymarket.com/events/slug/" + slug
        event = json.loads(fetch(endpoint))
        for needle in needles:
            found = [m for m in event.get("markets", []) if needle.lower() in
                     str(m.get("question", "")).lower()]
            if len(found) != 1:
                raise ValueError(f"ambiguous or missing market mapping {slug} {needle}")
            m = found[0]
            tokens = json.loads(m["clobTokenIds"])
            if not isinstance(tokens, list) or not tokens:
                raise ValueError("no CLOB token ids")
            result[str(tokens[0])] = {
                "event": slug, "outcome": needle, "market_id": m["id"],
                "question": m["question"],
                "gamma_bid": m.get("bestBid"), "gamma_ask": m.get("bestAsk"),
            }
    if len(result) != 6:
        raise ValueError("expected exactly 6 unique asset ids")
    return result


def emit(kind: str, payload: dict) -> None:
    now = time.time()
    record = dict(kind=kind, received_utc=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                  received_epoch_ms=int(now * 1000), phase=phase(now), **payload)
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    if TAPE.exists() and TAPE.stat().st_size + len(encoded.encode()) > MAX_FILE_BYTES:
        TAPE.replace(TAPE.with_suffix(".jsonl.1"))
    with TAPE.open("a", encoding="utf-8") as out:
        out.write(encoded)


def state_save(**values) -> None:
    status = dict(updated_utc=datetime.now(timezone.utc).isoformat(),
                  election_closure_utc="2026-10-27T20:00:00Z",
                  mode=phase(time.time()), production=False,
                  trading_authority=False, authenticated_result_feed=False, **values)
    STATUS.write_text(json.dumps(status, indent=2), encoding="utf-8")


class SwingDetector:
    def __init__(self) -> None:
        self.history = defaultdict(lambda: deque(maxlen=1400))
        self.last_quote = {}
        self.last_logged = {}
        self.alert_at = {}

    def handle(self, token: str, bid: float, ask: float, provider_ms: int | None) -> None:
        if not (0.0 <= bid <= ask <= 1.0):
            return
        now = time.monotonic()
        mid = (bid + ask) / 2
        hist = self.history[token]
        hist.append((now, mid))
        while hist and now - hist[0][0] > 300:
            hist.popleft()
        first60 = next((value for at, value in hist if at >= now - 60), mid)
        first300 = hist[0][1]
        move60, move300 = mid - first60, mid - first300
        prior = self.last_logged.get(token)
        if prior is None or now - prior[0] >= 45 or abs(mid - prior[1]) >= .003:
            emit("market_quote", dict(asset_id=token, bid=bid, ask=ask, midpoint=mid,
                                     provider_epoch_ms=provider_ms, move_60s=round(move60, 6),
                                     move_300s=round(move300, 6)))
            self.last_logged[token] = (now, mid)
        self.last_quote[token] = (bid, ask)
        if (abs(move60) >= .03 or abs(move300) >= .06) and (
                now - self.alert_at.get(token, -100000) >= 30):
            emit("price_acceleration_observed", dict(
                asset_id=token, bid=bid, ask=ask,
                move_60s=round(move60, 5), move_300s=round(move300, 5),
                verified_catalyst=False, actionable=False,
                note="Market movement is NOT independent election information"))
            self.alert_at[token] = now


def read_news(seen: set[str]) -> int:
    root = ET.fromstring(fetch(RSS_URL, 800_000))
    count = 0
    for item in root.findall("./channel/item")[:25]:
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        date = (item.findtext("pubDate") or "").strip()
        family = item.find("source")
        source = "" if family is None else (family.text or "")
        if not title or not url:
            continue
        ident = title + "|" + url
        if ident in seen:
            continue
        if len(seen) >= 3000:
            seen.clear()
        seen.add(ident)
        emit("headline_discovered", dict(title=title[:300], url=url[:800],
             reported_publish_time=date, underlying_provider=source[:100],
             feed="Google News RSS (aggregator)", independently_verified=False,
             actionable=False))
        count += 1
    return count


async def rss_loop(stop: asyncio.Event) -> None:
    seen = set()
    while not stop.is_set() and time.time() < END_EPOCH:
        try:
            n = await asyncio.to_thread(read_news, seen)
            state_save(news_headlines_new=n, last_news_check_utc=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            emit("source_error", dict(source="google-news-rss",
                 error=type(exc).__name__, actionable=False))
        period = 45 if phase(time.time()) in ("APPROACH", "GO_WATCH") else 150
        try:
            await asyncio.wait_for(stop.wait(), timeout=period)
        except TimeoutError:
            pass


async def market_loop(stop: asyncio.Event, metadata: dict) -> None:
    tokens = tuple(metadata)
    detector = SwingDetector()
    attempts = 0
    while not stop.is_set() and time.time() < END_EPOCH:
        try:
            async with websockets.connect(
                "wss://ws-subscriptions-clob.polymarket.com/ws/market",
                open_timeout=8, ping_interval=20, ping_timeout=20,
                max_size=1_000_000,
            ) as stream:
                attempts = 0
                await stream.send(json.dumps({
                    "type": "market", "assets_ids": list(tokens), "custom_feature_enabled": True
                }))
                emit("market_stream_connected", dict(token_count=len(tokens)))
                state_save(market_stream="connected", token_count=len(tokens))
                while not stop.is_set() and time.time() < END_EPOCH:
                    try:
                        raw = await asyncio.wait_for(stream.recv(), timeout=30)
                    except TimeoutError:
                        continue
                    try:
                        parsed = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    for event in (parsed if isinstance(parsed, list) else [parsed]):
                        if not isinstance(event, dict):
                            continue
                        event_type = event.get("event_type")
                        updates = event.get("price_changes", []) if event_type == "price_change" else [event]
                        for update in updates:
                            if not isinstance(update, dict):
                                continue
                            token = str(update.get("asset_id") or event.get("asset_id") or "")
                            if token not in metadata:
                                continue
                            bid, ask = None, None
                            if event_type == "book":
                                bids = update.get("bids") or []
                                asks = update.get("asks") or []
                                if bids and asks:
                                    bid = max(float(x["price"]) for x in bids)
                                    ask = min(float(x["price"]) for x in asks)
                            elif event_type in ("price_change", "best_bid_ask"):
                                try:
                                    bid = float(update["best_bid"])
                                    ask = float(update["best_ask"])
                                except (KeyError, TypeError, ValueError):
                                    pass
                            if bid is not None and ask is not None:
                                raw_ts = update.get("timestamp") or event.get("timestamp")
                                try:
                                    provider_ms = int(raw_ts) if raw_ts else None
                                except ValueError:
                                    provider_ms = None
                                detector.handle(token, bid, ask, provider_ms)
        except Exception as exc:
            emit("source_error", dict(source="polymarket-ws",
                error=type(exc).__name__, actionable=False))
        attempts += 1
        state_save(market_stream="disconnected", reconnect_attempt=attempts)
        try:
            await asyncio.wait_for(stop.wait(), timeout=min(60, 2 ** min(attempts, 5)))
        except TimeoutError:
            pass


async def run(once: bool) -> None:
    meta = await asyncio.to_thread(discover)
    emit("collector_started", dict(instruments=meta,
         modes=["WATCH", "APPROACH", "GO_WATCH", "POST_CLOSE"],
         warning="Neither headline aggregation nor price changes are verified vote results"))
    state_save(market_stream="connecting", token_count=len(meta))
    if once:
        await asyncio.to_thread(read_news, set())
        state_save(market_stream="not_started_once_mode")
        print(json.dumps({"status": "baseline_saved", "tokens": len(meta),
            "log": str(TAPE), "time_utc": datetime.now(timezone.utc).isoformat()}))
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await asyncio.gather(market_loop(stop, meta), rss_loop(stop))
    emit("collector_stopped", dict(reason="deadline_or_signal"))
    state_save(market_stream="stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.once))
