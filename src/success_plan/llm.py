"""Model access, cost estimates, and usage logging.

Stages depend on the small `LLM` interface, not on the Anthropic SDK directly, so tests can pass a
fake model and never make API calls.
"""

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

import yaml
from pydantic import BaseModel

from .paths import PROJECT_ROOT

MODELS_FILE = PROJECT_ROOT / "config" / "models.yaml"

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class CallSettings:
    model: str
    effort: str
    max_tokens: int
    expected_output_tokens: int


@dataclass
class LLMResult:
    parsed: BaseModel
    model: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class LLM(Protocol):
    def complete(self, *, system: str, user: str, output_model: type[T], settings: CallSettings) -> LLMResult: ...


class TokenCounter(Protocol):
    def count_input_tokens(self, *, system: str, user: str, output_model: type[T], model: str) -> int: ...


# ---------------------------------------------------------------- config

@dataclass(frozen=True)
class ModelsConfig:
    max_cost_usd_per_run: float
    profiles: dict
    prices: dict
    prices_as_of: str
    chars_per_token: float = 2.35

    def settings(self, profile: str, stage: str) -> CallSettings:
        try:
            s = self.profiles[profile][stage]
        except KeyError as exc:
            raise LLMError(f"No settings for profile {profile!r}, stage {stage!r} in config/models.yaml") from exc
        return CallSettings(s["model"], s["effort"], int(s["max_tokens"]), int(s["expected_output_tokens"]))


def load_models_config(path: Path = MODELS_FILE) -> ModelsConfig:
    data = yaml.safe_load(path.read_text())
    return ModelsConfig(
        max_cost_usd_per_run=float(data["max_cost_usd_per_run"]),
        profiles=data["profiles"],
        prices=data["prices"],
        prices_as_of=str(data["prices_as_of"]),
        chars_per_token=float(data["token_estimate"]["chars_per_token"]),
    )


# ---------------------------------------------------------------- tokens and cost

def estimate_tokens(text: str, chars_per_token: float) -> int:
    """Local fallback estimate, calibrated in config/models.yaml (token_estimate).

    Used for --dry-run and the pre-run spending check only when exact counting is unavailable.
    """
    return math.ceil(len(text) / chars_per_token)


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _rates(prices: dict, model: str, prompt_tokens: int) -> dict:
    if model not in prices:
        raise LLMError(f"No price for model {model!r} in config/models.yaml")
    rates = prices[model]
    threshold = rates.get("long_context_threshold")
    if threshold and prompt_tokens > threshold:
        return rates["long_context"]
    return rates


def cost_usd(prices: dict, model: str, *, input_tokens: int, output_tokens: int,
             cache_read_input_tokens: int = 0, cache_creation_input_tokens: int = 0) -> float:
    prompt_tokens = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
    r = _rates(prices, model, prompt_tokens)
    total = (
        input_tokens * r["input"]
        + output_tokens * r["output"]
        + cache_read_input_tokens * r.get("cache_read", r["input"])
        + cache_creation_input_tokens * r.get("cache_write", r["input"])
    ) / 1_000_000
    return round(total, 6)


# ---------------------------------------------------------------- Anthropic implementation

class AnthropicLLM:
    """Streams the request (long thinking at high effort) and parses the output into a Pydantic model."""

    def __init__(self, client=None):
        import anthropic  # imported here so tests and --dry-run never need credentials

        self._anthropic = anthropic
        self._client = client or anthropic.Anthropic()

    def count_input_tokens(self, *, system: str, user: str, output_model: type[T], model: str) -> int:
        """Exact input token count from Anthropic's token-counting endpoint (no model call, no charge)."""
        a = self._anthropic
        try:
            result = self._client.messages.count_tokens(
                model=model,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_model,
            )
        except a.APIError as exc:
            raise LLMError(f"Token counting failed: {exc}") from exc
        return int(result.input_tokens)

    def complete(self, *, system: str, user: str, output_model: type[T], settings: CallSettings) -> LLMResult:
        a = self._anthropic
        try:
            with self._client.messages.stream(
                model=settings.model,
                max_tokens=settings.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_model,
                output_config={"effort": settings.effort},
            ) as stream:
                message = stream.get_final_message()
        except a.AuthenticationError as exc:
            raise LLMError("Anthropic authentication failed. Set ANTHROPIC_API_KEY in .env.") from exc
        except a.RateLimitError as exc:
            raise LLMError(f"Anthropic rate limit: {exc}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc}") from exc
        except a.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc

        if message.stop_reason == "refusal":
            raise LLMError(f"The model declined the request (refusal): {getattr(message, 'stop_details', None)}")
        if message.stop_reason == "max_tokens":
            raise LLMError(f"Output was cut off at max_tokens={settings.max_tokens}; nothing was saved.")
        parsed = getattr(message, "parsed_output", None)
        if parsed is None:
            raise LLMError("The response did not contain output in the expected format.")
        usage = message.usage
        return LLMResult(
            parsed=parsed,
            model=message.model,
            stop_reason=message.stop_reason,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        )
