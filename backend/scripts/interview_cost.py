"""Per-interview usage and cost estimate, from the command line.

    python scripts/interview_cost.py                  # the most recent 50 interviews
    python scripts/interview_cost.py --interview int_abc123
    python scripts/interview_cost.py --no-network     # use the cached price feed only

The calculation lives in shared/cost.py, the same code the admin panel's cost card uses.
Prices and their sources are described in docs/OPERATIONS.md (Costs).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

# Run as a file, Python puts scripts/ on the path, not backend/, so the app is not importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.cost import (  # noqa: E402
    LLM_ROLES,
    MODEL_KEYS,
    LlmPrice,
    Rates,
    estimate,
    llm_cost,
    llm_pricing_notes,
    units_line,
)
from shared.pricing import PriceFeed, _fetch_json, default_cache_path, load_feed  # noqa: E402

__all__ = [
    "LLM_ROLES", "MODEL_KEYS", "LlmPrice", "Rates", "estimate", "llm_cost",
    "llm_pricing_notes", "units_line",
]


def money(value: float | None) -> str:
    return "not priced" if value is None else f"${value:.4f}"


def describe_feed(feed: PriceFeed | None) -> str:
    if feed is None:
        return "LLM prices: unavailable (feed not reachable and no cached copy). LLM lines are not priced."
    return (f"LLM prices: {feed.source} copy of llm-prices.com, feed dated {feed.updated_at}, "
            f"retrieved {feed.fetched_at}")


def _no_fetch(url, timeout):
    raise RuntimeError("--no-network: not fetching the feed")


async def main() -> int:
    from app.db import Interview, get_sessionmaker

    parser = argparse.ArgumentParser(description="Usage and cost per interview.")
    parser.add_argument("--interview", help="one interview id")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--no-network", action="store_true", help="use the cached price feed only")
    args = parser.parse_args()

    feed = load_feed(cache_path=default_cache_path(), fetch=_no_fetch if args.no_network else _fetch_json)
    rates = Rates.from_env(feed=feed)

    async with get_sessionmaker()() as session:
        query = select(Interview)
        if args.interview:
            query = query.where(Interview.id == args.interview)
        else:
            query = query.order_by(Interview.created_at.desc()).limit(args.limit)
        interviews = list((await session.scalars(query)).all())

    print(describe_feed(feed))
    if not interviews:
        print("no interviews found")
        return 0

    totals: dict[str, float] = {}
    for interview in interviews:
        usage = ((interview.session_metrics or {}).get("usage")) or None
        print(f"{interview.id}  {interview.status:<12} {interview.created_at:%Y-%m-%d}")
        if usage is None:
            print("    no usage record (written by sessions that ran after this change)")
            continue
        print("    " + units_line(usage))
        for note in llm_pricing_notes(usage, rates):
            print("    priced: " + note)
        lines = estimate(usage, rates)
        for key, value in lines.items():
            if value is not None:
                totals[key] = totals.get(key, 0.0) + value
        priced = {k: v for k, v in lines.items() if v is not None}
        if priced:
            print("    estimate: " + ", ".join(f"{k} {money(v)}" for k, v in priced.items())
                  + f"  = {money(sum(priced.values()))}")
        else:
            print("    estimate: not priced (no prices available for this interview)")

    if totals:
        print(f"\nTOTAL estimate over {len(interviews)} interview(s): {money(sum(totals.values()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
