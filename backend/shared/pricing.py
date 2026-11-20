"""Per-model LLM prices, fetched from a public price feed.

The feed is https://www.llm-prices.com/current-v1.json. It lists each model with its
price per million tokens: `input`, `output`, and `input_cached` (or null when the model
has no cached-input rate).

Used only to estimate cost. Nothing here changes what an interview does. Usage is still
recorded as counts (shared/usage.py), and prices are applied when a report is produced,
so a price change never rewrites history.

Failure is explicit. If the feed cannot be fetched and no cached copy exists, prices are
unavailable and the report says so. It never falls back to a guessed figure.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

PRICE_FEED_URL = "https://www.llm-prices.com/current-v1.json"

#: How long a cached copy is trusted before the feed is fetched again.
DEFAULT_TTL_SECONDS = 24 * 60 * 60

#: Identifies the client to the feed's host. A generic name, not a person's address.
USER_AGENT = "mitra-cost-report/1.0"


@dataclass(frozen=True)
class ModelPrice:
    id: str
    vendor: str
    name: str
    input: float  # dollars per million uncached input tokens
    output: float  # dollars per million output tokens
    input_cached: float | None  # dollars per million cached input tokens, if the model has one


@dataclass(frozen=True)
class PriceFeed:
    updated_at: str  # the feed's own date, the one to quote
    fetched_at: str  # when this copy was retrieved, UTC
    source: str  # "network" or "cache"
    models: dict[str, ModelPrice]

    def get(self, model_id: str) -> ModelPrice | None:
        return self.models.get(model_id)


def parse_feed(payload: dict) -> tuple[str, dict[str, ModelPrice], int]:
    """Read the feed. Returns (updated_at, models by id, count of malformed entries skipped)."""
    updated_at = str(payload.get("updated_at", "unknown"))
    models: dict[str, ModelPrice] = {}
    skipped = 0
    for entry in payload.get("prices", []):
        try:
            price = ModelPrice(
                id=str(entry["id"]),
                vendor=str(entry.get("vendor", "")),
                name=str(entry.get("name", entry["id"])),
                input=float(entry["input"]),
                output=float(entry["output"]),
                input_cached=None if entry.get("input_cached") is None else float(entry["input_cached"]),
            )
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        models[price.id] = price
    return updated_at, models, skipped


def _fetch_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read().decode("utf-8"))


def load_feed(
    *,
    cache_path: Path,
    url: str = PRICE_FEED_URL,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    fetch: Callable[[str, float], dict] = _fetch_json,
    now: float | None = None,
    timeout: float = 10.0,
) -> PriceFeed | None:
    """The current prices, from the cache if it is fresh, otherwise from the network.

    If the network fails, an older cached copy is used and marked as such. If there is no
    copy at all, returns None, and the caller must report the model as unpriced.
    """
    current = time.time() if now is None else now

    cached = _read_cache(cache_path)
    if cached is not None and current - cached["fetched_epoch"] < ttl_seconds:
        return _feed_from(cached, source="cache")

    try:
        payload = fetch(url, timeout)
    except Exception:  # noqa: BLE001 - any failure falls back to the cache, then to None
        if cached is not None:
            return _feed_from(cached, source="cache")
        return None

    updated_at, models, _skipped = parse_feed(payload)
    record = {
        "fetched_epoch": current,
        "fetched_at": datetime.fromtimestamp(current, UTC).isoformat(timespec="seconds"),
        "updated_at": updated_at,
        "payload": payload,
    }
    _write_cache(cache_path, record)
    return PriceFeed(
        updated_at=updated_at,
        fetched_at=record["fetched_at"],
        source="network",
        models=models,
    )


def _feed_from(record: dict, *, source: str) -> PriceFeed:
    _updated, models, _skipped = parse_feed(record["payload"])
    return PriceFeed(
        updated_at=record["updated_at"],
        fetched_at=record["fetched_at"],
        source=source,
        models=models,
    )


def _read_cache(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        float(record["fetched_epoch"])
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError:
        # A cache that cannot be written only means the next run fetches again.
        pass


def default_cache_path() -> Path:
    """Where the cached feed lives. Set PRICE_CACHE_PATH to move it."""
    configured = os.environ.get("PRICE_CACHE_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / ".cache" / "llm-prices.json"
