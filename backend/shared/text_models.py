"""One request helper for every off-call text-model call. Shared (Track I and H).

Live calls are not here: the realtime engine, and the pipeline fallback's
interviewer and judge (bot/brain/drivers.py), keep their own clients. They are
on, or beside, the reply path and are tuned for that. drivers.py reads the
reasoning-model table below, so there is one table, not two.

What a call gets:
  - the job's model, resolved from the settings (bot/config.model_for), unless
    a model is passed explicitly (Track H's jobs pass their own);
  - request arguments for the model family: a reasoning model gets no
    temperature and a reasoning effort instead (REASONING_MODELS);
  - Structured Outputs: a strict JSON schema generated from a Pydantic model
    whose fields are picked from the contract types (`pick`), never json_object;
  - one retry on a transient failure (timeout, connection, 429, 5xx), and a
    clear TextModelError otherwise; a reply that does not validate raises
    TextModelSchemaError;
  - a usage record: job, model, input / cached / output / reasoning tokens,
    latency and cost from PRICES (None, never a guess, when unpriced).
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from loguru import logger
from pydantic import BaseModel, ValidationError, create_model

# -- reasoning models -----------------------------------------------------------

#: Exact model names (and their dated snapshots) that reason, with the LOWEST
#: effort each accepts. Probed or documented, never assumed (drivers.py's rule).
#: drivers.LOWEST_EFFORT is this table; its first five rows are unchanged from
#: bot/brain/drivers.py before task 2.
#:   gpt-5-mini / gpt-5-nano: take "minimal", reject "none" (probed, drivers.py).
#:   gpt-5.4-* / gpt-5.6-luna: take "none", reject "minimal" (probed, drivers.py).
#:   gpt-6-sol / gpt-6.1-sol / gpt-6.2-sol: efforts low, medium (default), high,
#:   xhigh, max; "If you currently use none or minimal, use low instead"
#:   (developers.openai.com/api/docs/models/gpt-6.1-sol and the GPT-6 guide,
#:   read 2026-11-18).
REASONING_MODELS: dict[str, str] = {
    "gpt-5-mini": "minimal",
    "gpt-5-nano": "minimal",
    "gpt-5.4-mini": "none",
    "gpt-5.4-nano": "none",
    "gpt-5.6-luna": "none",
    "gpt-6-sol": "low",
    "gpt-6.1-sol": "low",
    "gpt-6.2-sol": "low",
}

#: Effort for off-call jobs on a reasoning model. "medium" is the documented
#: default of the gpt-6 Sol models, so sending it changes nothing there; it is
#: explicit so the record says what was asked for.
OFF_CALL_EFFORT = "medium"


def _base_name(model: str) -> str:
    for name in REASONING_MODELS:
        if model == name or model.startswith(name + "-20"):
            return name
    return model


def is_reasoning_model(model: str) -> bool:
    return _base_name(model) in REASONING_MODELS


def request_args(model: str, *, temperature: float | None = None,
                 effort: str | None = None) -> dict:
    """Family-specific arguments. Reasoning models reject `temperature`."""
    if is_reasoning_model(model):
        return {"reasoning_effort": effort or OFF_CALL_EFFORT}
    return {} if temperature is None else {"temperature": temperature}


# -- prices ----------------------------------------------------------------------

#: USD per 1M tokens, Standard tier, short context. The only price table for
#: text models. Source: https://developers.openai.com/api/docs/pricing ("Flagship
#: models > Standard pricing data"), read 2026-11-18. A model missing here is
#: recorded with cost None and price_known False, never a guess. The pricing
#: page does not say how reasoning tokens are billed; they are included in
#: completion_tokens, which is what is priced as output here.
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing (read 2026-11-18)"
PRICES: dict[str, dict[str, float]] = {
    "gpt-6.1-sol": {"input": 2.00, "cached_input": 0.10, "output": 10.00},
    "gpt-6.2-sol": {"input": 2.00, "cached_input": 0.10, "output": 10.00},
    "gpt-6-sol": {"input": 2.00, "cached_input": 0.20, "output": 10.00},
    "gpt-4.1": {"input": 2.00, "cached_input": 0.50, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "cached_input": 0.10, "output": 1.60},
}


@dataclass
class UsageRecord:
    job: str
    model: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float | None = None
    price_known: bool = False
    price_source: str = PRICE_SOURCE
    attempts: int = 1
    outcome: str = "ok"  # ok | schema_error | error

    def to_dict(self) -> dict:
        return asdict(self)


def price_usd(model: str, *, input_tokens: int, cached_input_tokens: int,
              output_tokens: int) -> float | None:
    price = PRICES.get(model) or PRICES.get(_dated_base(model))
    if price is None:
        return None
    uncached = max(input_tokens - cached_input_tokens, 0)
    return round((uncached * price["input"] + cached_input_tokens * price["cached_input"]
                  + output_tokens * price["output"]) / 1_000_000, 6)


def _dated_base(model: str) -> str:
    # "gpt-4.1-2025-04-14" -> "gpt-4.1": dated snapshots share the family price.
    parts = model.split("-")
    return "-".join(parts[:-3]) if len(parts) > 3 and parts[-3].isdigit() else model


def usage_from_response(job: str, model: str, response, latency_s: float) -> UsageRecord:
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", 0) or 0
    cost = price_usd(model, input_tokens=prompt, cached_input_tokens=cached, output_tokens=completion)
    return UsageRecord(job=job, model=model, input_tokens=prompt, cached_input_tokens=cached,
                       output_tokens=completion, reasoning_tokens=reasoning,
                       latency_s=round(latency_s, 3), cost_usd=cost, price_known=cost is not None)


# -- strict JSON schema from Pydantic ---------------------------------------------

#: Keywords strict Structured Outputs does not accept. Removed from the schema;
#: Pydantic still enforces them when the reply is validated.
_UNSUPPORTED = {"default", "title", "minLength", "maxLength", "examples"}


def strict_json_schema(model_cls: type[BaseModel]) -> dict:
    """Pydantic's schema, adapted for `strict: true` (the contract is untouched):
    $refs inlined (no siblings next to $ref), every property required, optional
    fields kept as nullable (anyOf with null), additionalProperties false, and
    unsupported keywords removed."""
    raw = model_cls.model_json_schema(mode="validation")
    defs = raw.pop("$defs", {})

    def resolve(node):
        if isinstance(node, list):
            return [resolve(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = copy.deepcopy(defs[node["$ref"].split("/")[-1]])
            # A class docstring is engineering prose, not an instruction to the
            # model; the field's own description (below) is kept.
            target.pop("description", None)
            merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
            return resolve(merged)
        out = {k: resolve(v) for k, v in node.items() if k not in _UNSUPPORTED}
        if out.get("type") == "object" or "properties" in out:
            out["type"] = "object"
            out.setdefault("properties", {})
            out["required"] = list(out["properties"].keys())
            out["additionalProperties"] = False
        return out

    return resolve(raw)


def pick(source: type[BaseModel], *names: str, model_name: str, **extra: Any) -> type[BaseModel]:
    """A response model made of fields copied from a contract type, plus extras.

    Copies the annotation and FieldInfo (description, constraints) so a response
    schema cannot drift from the contract it feeds. `extra` adds or overrides
    fields: name=(annotation, FieldInfo or default).
    """
    fields: dict[str, Any] = {}
    for name in names:
        info = source.model_fields[name]
        fields[name] = (info.annotation, info)
    fields.update(extra)
    return create_model(model_name, **fields)


# -- the request -------------------------------------------------------------------


class TextModelError(RuntimeError):
    """A text-model call failed after its retry, or was not retryable."""

    def __init__(self, message: str, record: UsageRecord | None = None):
        super().__init__(message)
        self.record = record


class TextModelSchemaError(TextModelError):
    """The reply did not validate against the response model."""


def _is_transient(exc: Exception) -> bool:
    import openai

    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500


@dataclass
class StructuredResult:
    payload: dict
    record: UsageRecord
    parsed: BaseModel = field(repr=False, default=None)


async def request_structured(
    *,
    job: str,
    messages: list[dict],
    response_model: type[BaseModel],
    model: str | None = None,
    api_key: str | None = None,
    client=None,
    temperature: float | None = None,
    effort: str | None = None,
    retry_delay_s: float = 1.0,
) -> StructuredResult:
    """One structured-output call for an off-call job. See the module docstring."""
    if model is None:
        from bot.config import model_for  # settings live with Track I's config

        model = model_for(job)
    if client is None:
        from openai import AsyncOpenAI

        # Retries are ours (exactly one), not the SDK's default two.
        client = AsyncOpenAI(api_key=api_key, max_retries=0)

    kwargs = dict(
        model=model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": f"{job}_response", "strict": True,
                            "schema": strict_json_schema(response_model)},
        },
        **request_args(model, temperature=temperature, effort=effort),
    )
    attempts = 0
    started = time.monotonic()
    while True:
        attempts += 1
        try:
            response = await client.chat.completions.create(**kwargs)
            break
        except Exception as exc:  # noqa: BLE001
            if attempts == 1 and _is_transient(exc):
                logger.warning(f"text model {job}/{model}: transient failure, retrying once: {exc}")
                await asyncio.sleep(retry_delay_s)
                continue
            record = UsageRecord(job=job, model=model, attempts=attempts, outcome="error",
                                 latency_s=round(time.monotonic() - started, 3))
            raise TextModelError(
                f"{job} call to {model} failed after {attempts} attempt(s): {exc}", record
            ) from exc

    record = usage_from_response(job, model, response, time.monotonic() - started)
    record.attempts = attempts
    message = response.choices[0].message
    try:
        if getattr(message, "refusal", None):
            raise ValueError(f"the model refused: {message.refusal}")
        payload = json.loads(message.content or "")
        parsed = response_model.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        record.outcome = "schema_error"
        logger.error(f"text model {job}/{model}: reply failed validation: {exc}")
        raise TextModelSchemaError(f"{job} reply from {model} failed validation: {exc}", record) from exc

    logger.info(
        f"text model {job}/{model}: in={record.input_tokens} cached={record.cached_input_tokens} "
        f"out={record.output_tokens} reasoning={record.reasoning_tokens} "
        f"{record.latency_s}s cost={'unknown' if record.cost_usd is None else f'${record.cost_usd:.4f}'}"
    )
    return StructuredResult(payload=payload, record=record, parsed=parsed)
