"""Explicit read-only SIG Realtime capture command."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from predictions_cup.config import AppSettings, load_settings
from predictions_cup.sig.errors import SigApiError
from predictions_cup.sig.governed_client import GovernedSigRestClient
from predictions_cup.sig.realtime_state import SigRealtimeStateEngine, SubscriptionReason
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder
from predictions_cup.sig.realtime_subscriber import SubscriberExit, SupabaseTournamentSubscriber

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run read-only SIG Realtime capture.")
    parser.add_argument("--tournament-id", help="Explicit tournament UUID.")
    parser.add_argument(
        "--list-tournaments",
        action="store_true",
        help="List accessible tournaments and exit without subscribing.",
    )
    parser.add_argument("--storage-path", type=Path, help="Override SQLite recorder path.")
    parser.add_argument("--book-depth", type=int, help="Override authoritative REST depth.")
    parser.add_argument(
        "--tracked-exchange-id",
        action="append",
        default=[],
        help=(
            "Exchange ID whose authoritative full depth should be maintained. "
            "Repeat for multiple exchanges. Defaults to none."
        ),
    )
    parser.add_argument(
        "--print-health",
        action="store_true",
        help="Print a final JSON health snapshot for credentialed smoke tests.",
    )
    parser.add_argument(
        "--run-seconds",
        type=float,
        help="Optional finite runtime for a manual credentialed smoke test.",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace, settings: AppSettings) -> int:
    async with GovernedSigRestClient(settings) as rest:
        if args.list_tournaments:
            page = await rest.list_tournaments(status="any", limit=100, offset=0)
            for tournament in page.data:
                print(
                    json.dumps(
                        {
                            "id": tournament.id,
                            "slug": tournament.slug,
                            "name": tournament.name,
                            "status": tournament.status,
                        },
                        sort_keys=True,
                    )
                )
            return 0

        tournament_id = args.tournament_id or settings.tournament_id
        if tournament_id is None or not tournament_id.strip():
            raise ValueError(
                "SIG capture requires explicit --tournament-id or PREDICTIONS_CUP_TOURNAMENT_ID"
            )
        book_depth = args.book_depth or settings.sig_realtime_book_depth
        storage_path = args.storage_path or settings.sig_realtime_storage_path
        recorder = SigRealtimeRecorder(storage_path)
        stop_event = asyncio.Event()
        _install_signal_handlers(stop_event)
        if args.run_seconds is not None:
            if args.run_seconds <= 0:
                raise ValueError("--run-seconds must be positive")
            asyncio.create_task(_stop_after(args.run_seconds, stop_event))

        tracked_exchange_ids = _tracked_exchange_ids(args, settings)
        if tracked_exchange_ids:
            logger.info(
                "SIG full-depth maintenance enabled tracked_exchange_count=%s",
                len(tracked_exchange_ids),
            )
        else:
            logger.warning(
                "SIG capture has no tracked exchange IDs; tournament-wide Realtime "
                "and scalar/BBO capture remain enabled, but no resident full depth "
                "will be trusted"
            )

        engine = SigRealtimeStateEngine(
            rest=rest,
            recorder=recorder,
            tournament_id=tournament_id,
            tracked_depth_exchange_ids=tracked_exchange_ids,
            book_depth=book_depth,
            open_book_max_trusted_age_seconds=(
                settings.sig_realtime_open_book_refresh_seconds
            ),
            bulk_price_refresh_seconds=(
                settings.sig_realtime_bulk_price_refresh_seconds
            ),
            governed_rate_per_second=settings.sig_rest_governor_rate_per_second,
            governor_snapshot=rest.governor_snapshot,
        )
        try:
            cutoff = datetime.now(UTC) - timedelta(days=settings.sig_realtime_retention_days)
            recorder.prune_before(cutoff)
            reason = SubscriptionReason.INITIAL_SUBSCRIBE
            while not stop_event.is_set():
                try:
                    # The documented lifecycle is token -> authoritative REST resync
                    # -> private subscription. Initial seeding happens exactly once.
                    token = await rest.mint_realtime_token()
                    if reason == SubscriptionReason.INITIAL_SUBSCRIBE:
                        await engine.initialize()
                    else:
                        await engine.prepare_subscription(reason)
                    subscriber = SupabaseTournamentSubscriber(
                        topic=engine.topic,
                        token=token,
                        token_refresh_margin_seconds=(
                            settings.sig_realtime_token_refresh_margin_seconds
                        ),
                    )
                    outcome = await subscriber.run(
                        on_batch=engine.handle_raw_batch,
                        on_connected=engine.mark_connected,
                        stop_event=stop_event,
                        on_maintenance=engine.maintenance,
                    )
                except SigApiError as exc:
                    engine.mark_disconnected()
                    logger.warning(
                        "SIG authoritative recovery failed error=%s",
                        type(exc).__name__,
                    )
                    await asyncio.sleep(1.0)
                    continue

                engine.mark_disconnected()
                logger.info("SIG Realtime subscriber exited reason=%s", outcome.value)
                if outcome == SubscriberExit.STOPPED:
                    break
                if outcome == SubscriberExit.TOKEN_REFRESH:
                    reason = SubscriptionReason.TOKEN_REFRESH
                elif outcome == SubscriberExit.SOCKET_ERROR:
                    reason = SubscriptionReason.SOCKET_ERROR
                else:
                    reason = SubscriptionReason.RECONNECT
                await asyncio.sleep(1.0)
        finally:
            await engine.aclose()
            if args.print_health:
                print(
                    json.dumps(
                        _json_health_snapshot(engine.health_snapshot()),
                        sort_keys=True,
                    )
                )
            recorder.close()
    return 0


def _tracked_exchange_ids(
    args: argparse.Namespace,
    settings: AppSettings,
) -> tuple[str, ...]:
    configured = (
        value.strip()
        for value in settings.sig_realtime_tracked_exchange_ids.split(",")
        if value.strip()
    )
    cli_values = (
        value.strip()
        for value in args.tracked_exchange_id
        if isinstance(value, str) and value.strip()
    )
    return tuple(dict.fromkeys((*configured, *cli_values)))


def _json_health_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in snapshot.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


async def _stop_after(seconds: float, stop_event: asyncio.Event) -> None:
    await asyncio.sleep(seconds)
    stop_event.set()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)


def main() -> int:
    args = parse_args()
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    try:
        return asyncio.run(_run(args, settings))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
