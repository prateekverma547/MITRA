"""The cost estimate for interviews: one calculation, used by the CLI report and the admin panel.

Units come from the database (shared/usage.py). Prices come from the LLM price feed
(shared/pricing.py) and from environment variables for the services the feed does not cover.
Nothing here is stored: every figure is computed when it is asked for.

Precedence for an LLM role: its own env override, then the feed entry for the model it
used, then the shared env rate. A model in none of those is `NOT PRICED`, never estimated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from shared.pricing import PriceFeed

#: The three OpenAI roles. Key: the usage record's role name. Value: the suffix of that
#: role's override variables.
LLM_ROLES = {"llm_live": "LIVE", "llm_judgment": "JUDGMENT", "llm_feedback": "FEEDBACK"}
#: The key each role's model is recorded under in usage["models"].
MODEL_KEYS = {"llm_live": "live", "llm_judgment": "judgment", "llm_feedback": "feedback"}

#: Services with no public price feed. Their rates come from the environment only.
NON_LLM_RATES = {
    "tts_kchar": "PRICE_TTS_PER_KCHAR",
    "stt_min": "PRICE_STT_PER_MIN",
    "call_min": "PRICE_CALL_PER_MIN",
}


@dataclass(frozen=True)
class LlmPrice:
    input: float  # dollars per million uncached input tokens
    output: float  # dollars per million output tokens
    input_cached: float | None = None  # dollars per million cached input tokens, if known


@dataclass
class Rates:
    llm_in: float | None = None
    llm_out: float | None = None
    tts_kchar: float | None = None
    stt_min: float | None = None
    call_min: float | None = None
    role_llm: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    feed: PriceFeed | None = None

    @classmethod
    def from_env(cls, feed: PriceFeed | None = None) -> "Rates":
        def read(name: str) -> float | None:
            raw = os.environ.get(name, "").strip()
            return float(raw) if raw else None

        role_llm = {}
        for role, suffix in LLM_ROLES.items():
            pair = (read(f"PRICE_{suffix}_INPUT_PER_MTOK"), read(f"PRICE_{suffix}_OUTPUT_PER_MTOK"))
            if pair != (None, None):
                role_llm[role] = pair
        return cls(
            llm_in=read("PRICE_LLM_INPUT_PER_MTOK"),
            llm_out=read("PRICE_LLM_OUTPUT_PER_MTOK"),
            tts_kchar=read(NON_LLM_RATES["tts_kchar"]),
            stt_min=read(NON_LLM_RATES["stt_min"]),
            call_min=read(NON_LLM_RATES["call_min"]),
            role_llm=role_llm,
            feed=feed,
        )

    def llm_price(self, role: str, model: str | None = None) -> tuple[LlmPrice | None, str]:
        """The price for one LLM role, and where it came from: "role", "feed", "shared" or "none"."""
        own = self.role_llm.get(role)
        if own and own[0] is not None and own[1] is not None:
            return LlmPrice(own[0], own[1]), "role"
        if self.feed is not None and model:
            listed = self.feed.get(model)
            if listed is not None:
                return LlmPrice(listed.input, listed.output, listed.input_cached), "feed"
        if self.llm_in is not None and self.llm_out is not None:
            return LlmPrice(self.llm_in, self.llm_out), "shared"
        return None, "none"

    def llm_rate(self, role: str) -> tuple[float | None, float | None, str]:
        """(input, output, source) without a model, for callers that only have a role."""
        price, source = self.llm_price(role)
        if price is None:
            return None, None, source
        return price.input, price.output, source

    def missing_non_llm(self) -> list[str]:
        """Names of the env-only rates that are not set."""
        return [name for attr, name in NON_LLM_RATES.items() if getattr(self, attr) is None]


def _models(usage: dict) -> dict:
    return usage.get("models") or {}


def llm_cost(block: dict, price: LlmPrice) -> float:
    """Dollars for one role's tokens. Cached input is billed at the cached rate where the
    model has one; otherwise every input token is billed at the normal rate."""
    prompt = block.get("prompt_tokens", 0)
    completion = block.get("completion_tokens", 0)
    cached = min(block.get("cached_tokens", 0) or 0, prompt)
    if price.input_cached is not None:
        uncached_in = (prompt - cached) / 1_000_000 * price.input
        cached_in = cached / 1_000_000 * price.input_cached
    else:
        uncached_in = prompt / 1_000_000 * price.input
        cached_in = 0.0
    return uncached_in + cached_in + completion / 1_000_000 * price.output


def llm_pricing_notes(usage: dict, rates: Rates) -> list[str]:
    """Which model each LLM role was priced at, and where the price came from."""
    models = _models(usage)
    live_model = models.get("live", "unknown")
    notes = []
    for role, key in MODEL_KEYS.items():
        block = usage.get(role) or {}
        if not block.get("calls"):
            continue
        model = models.get(key, "unknown")
        _price, source = rates.llm_price(role, models.get(key))
        if source == "feed":
            label = f"feed, updated {rates.feed.updated_at}"
        elif source == "role":
            label = "own rate"
        elif source == "shared":
            label = "shared rate"
        else:
            label = "NOT PRICED: not in the price feed and no rate set"
        line = f"{key} model {model} ({label})"
        if source == "shared" and model != live_model:
            line += (f"  WARNING: {model} differs from the live model {live_model} "
                     f"but is priced with the shared rate; set PRICE_{LLM_ROLES[role]}_* for it")
        notes.append(line)
    return notes


def estimate(usage: dict, rates: Rates) -> dict[str, float | None]:
    """Each line of the estimate, or None where its price is unavailable."""
    models = _models(usage)

    def llm(role: str):
        block = usage.get(role) or {}
        price, _source = rates.llm_price(role, models.get(MODEL_KEYS[role]))
        if price is None:
            return None
        return llm_cost(block, price)

    def per(value, rate, unit):
        return None if rate is None else (value / unit) * rate

    call_minutes = (usage.get("call_seconds", 0) or 0) / 60
    return {
        "llm_live": llm("llm_live"),
        "llm_judgment": llm("llm_judgment"),
        "llm_feedback": llm("llm_feedback"),
        "tts": per(usage.get("tts_characters", 0), rates.tts_kchar, 1000),
        "stt": per((usage.get("stt_stream_seconds", 0) or 0) / 60, rates.stt_min, 1),
        "call": per(call_minutes, rates.call_min, 1),
    }


def units_line(usage: dict) -> str:
    live = usage.get("llm_live") or {}
    judge = usage.get("llm_judgment") or {}
    feedback = usage.get("llm_feedback") or {}
    return (
        f"call {usage.get('call_seconds', 0) / 60:5.1f} min | "
        f"live {live.get('prompt_tokens', 0)}+{live.get('completion_tokens', 0)} tok "
        f"({live.get('calls', 0)} calls) | "
        f"judge {judge.get('prompt_tokens', 0)}+{judge.get('completion_tokens', 0)} tok "
        f"({judge.get('calls', 0)} calls) | "
        f"feedback {feedback.get('prompt_tokens', 0)}+{feedback.get('completion_tokens', 0)} tok | "
        f"tts {usage.get('tts_characters', 0)} chars | "
        f"stt {usage.get('stt_stream_seconds', 0) / 60:.1f} min"
    )


def build_report(interviews: list[dict], rates: Rates) -> dict:
    """The report as plain data, for the admin panel and the CLI alike.

    `interviews` items carry: id, status, created_at (datetime or ISO string), usage (dict
    or None). Nothing about candidates is included: an ID and the numbers, nothing else.
    """
    rows = []
    totals = {"estimate_usd": 0.0, "complete": 0, "partial": 0, "no_usage": 0}
    for item in interviews:
        usage = item.get("usage")
        created = item["created_at"]
        row = {
            "id": item["id"],
            "status": item["status"],
            "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
        }
        if not usage:
            row.update({"has_usage": False, "estimate_usd": None, "complete": False, "lines": {}, "notes": []})
            totals["no_usage"] += 1
            rows.append(row)
            continue

        lines = estimate(usage, rates)
        priced = {k: v for k, v in lines.items() if v is not None}
        # A line needs a price only if it has usage behind it. An unused role costs nothing,
        # so a model that never ran (the feedback model, for a call that failed) must not
        # make the whole interview look unpriced.
        used = {
            "llm_live": (usage.get("llm_live") or {}).get("calls", 0) > 0,
            "llm_judgment": (usage.get("llm_judgment") or {}).get("calls", 0) > 0,
            "llm_feedback": (usage.get("llm_feedback") or {}).get("calls", 0) > 0,
            "tts": (usage.get("tts_characters", 0) or 0) > 0,
            "stt": (usage.get("stt_stream_seconds", 0) or 0) > 0,
            "call": (usage.get("call_seconds", 0) or 0) > 0,
        }
        complete = all(lines[k] is not None for k, needed in used.items() if needed)
        subtotal = sum(priced.values()) if priced else None
        row.update({
            "has_usage": True,
            "call_minutes": round((usage.get("call_seconds", 0) or 0) / 60, 2),
            "llm_calls": sum((usage.get(r) or {}).get("calls", 0) for r in LLM_ROLES),
            "tts_characters": usage.get("tts_characters", 0),
            "stt_minutes": round((usage.get("stt_stream_seconds", 0) or 0) / 60, 2),
            "estimate_usd": None if subtotal is None else round(subtotal, 4),
            "complete": complete,
            "lines": {k: (None if v is None else round(v, 4)) for k, v in lines.items()},
            "notes": llm_pricing_notes(usage, rates),
        })
        if subtotal is not None:
            totals["estimate_usd"] += subtotal
        totals["complete" if complete else "partial"] += 1
        rows.append(row)

    totals["estimate_usd"] = round(totals["estimate_usd"], 4)
    feed = rates.feed
    return {
        "prices": None if feed is None else {
            "source": feed.source,
            "updated_at": feed.updated_at,
            "fetched_at": feed.fetched_at,
        },
        "missing_rates": rates.missing_non_llm() + ([] if feed is not None else ["llm_feed"]),
        "totals": totals,
        "interviews": rows,
    }
