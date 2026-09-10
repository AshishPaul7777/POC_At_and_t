"""Provider-agnostic LLM access.

One class, one entry point, provider and model chosen by config. Built on
LangChain's ``init_chat_model`` so swapping Anthropic for OpenAI, Azure or Gemini
is an env change rather than a code change.

Custom gateways are supported: set ``ANTHROPIC_BASE_URL`` (or
``OPENAI_BASE_URL``, or ``LLM_BASE_URL`` for any provider) and requests route
through it. Corporate deployments almost always sit behind such a proxy, and a
client that only speaks to the vendor's public endpoint is unusable there.

The hard boundary, enforced by construction rather than by prompt wording: **this
module never invents UNUSED.** Narration may only nudge UNUSED toward
NEEDS_REVIEW, or confirm NEEDS_REVIEW → USED from already-listed evidence.
See ``narrate.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from app.config import Settings, get_settings

log = structlog.get_logger()


class LLMUnavailable(RuntimeError):
    """Raised when no usable provider is configured. Never fatal to a run.

    Narration is an enrichment. A run that cannot produce prose is still a
    complete, correct analysis, so callers degrade rather than abort.
    """


@dataclass
class LLMResult:
    text: str
    parsed: dict[str, Any] | None
    model: str
    provider: str
    prompt_sha256: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    refused: bool = False
    error: str | None = None


class LLMClient:
    """Thin wrapper over ``init_chat_model``.

    Deliberately thin: the value is in the prompt and the evidence handed to it,
    not in orchestration cleverness here.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._model = None
        self._provider = self._s.llm_provider.lower()
        self._model_name = self._s.llm_model
        self._temperature_ok = True
        self._repair_lock = asyncio.Lock()

    # -- construction ---------------------------------------------------------

    def _api_key(self) -> str | None:
        s = self._s
        key = {
            "anthropic": s.anthropic_api_key,
            "openai": s.openai_api_key,
            "azure_openai": s.openai_api_key,
            "google_genai": s.google_api_key,
        }.get(self._provider)
        return key.get_secret_value() if key else None

    def _base_url(self) -> str | None:
        """Explicit override wins, then the provider's standard env var."""
        s = self._s
        return s.llm_base_url or {
            "anthropic": s.anthropic_base_url,
            "openai": s.openai_base_url,
            "azure_openai": s.openai_base_url,
        }.get(self._provider)

    def _build(self):
        if self._model is not None:
            return self._model
        try:
            from langchain.chat_models import init_chat_model
        except ImportError:  # pragma: no cover
            try:
                from langchain_classic.chat_models import init_chat_model  # type: ignore
            except ImportError as e:
                raise LLMUnavailable(
                    "langchain is not installed. Install the provider extra, "
                    "e.g. `uv pip install -e \".[anthropic]\"`"
                ) from e

        kwargs: dict[str, Any] = {
            "model_provider": self._provider,
            "max_tokens": self._s.llm_max_tokens,
            "timeout": self._s.llm_timeout_seconds,
        }
        # Newer models reject `temperature` outright ("deprecated for this
        # model"), so it is only sent while we believe it is accepted. Discovered
        # at call time rather than kept as a hardcoded model list, which would
        # rot the moment a new model ships.
        if self._temperature_ok:
            kwargs["temperature"] = self._s.llm_temperature
        if (key := self._api_key()):
            kwargs["api_key"] = key
        if (base := self._base_url()):
            # Providers disagree on the parameter name. langchain-anthropic
            # accepts base_url; langchain-openai accepts base_url too, but the
            # underlying SDKs differ, so pass it and let a TypeError tell us.
            kwargs["base_url"] = base
            log.info("llm_gateway", provider=self._provider, base_url=base)

        try:
            self._model = init_chat_model(self._model_name, **kwargs)
        except TypeError as e:
            # A provider that rejects base_url: retry without it rather than
            # failing outright, and say so — silently dropping the gateway would
            # send traffic to the public endpoint, which may be blocked or
            # non-compliant.
            if "base_url" in str(e) and "base_url" in kwargs:
                log.warning("llm_base_url_unsupported", provider=self._provider,
                            detail=str(e)[:200])
                kwargs.pop("base_url")
                self._model = init_chat_model(self._model_name, **kwargs)
            else:
                raise LLMUnavailable(f"could not construct model: {e}") from e
        except Exception as e:
            raise LLMUnavailable(
                f"could not construct {self._provider}/{self._model_name}: {e}"
            ) from e
        return self._model

    # -- invocation -----------------------------------------------------------

    @property
    def describe(self) -> dict[str, Any]:
        return {
            "provider": self._provider,
            "model": self._model_name,
            "base_url": self._base_url() or "(provider default)",
            "temperature": self._s.llm_temperature,
            "has_key": bool(self._api_key()),
        }

    async def json_call(self, system: str, user: str, *,
                        expect: tuple[str, ...] = ()) -> LLMResult:
        """Invoke and parse a JSON object out of the reply.

        Structured output is requested by prompt and then parsed defensively.
        Behind a gateway, tool-calling and native structured-output support are
        not guaranteed, and a hard dependency on them would break exactly the
        deployment this needs to work in.

        Rate limits (HTTP 429 / "rate limit") are retried with exponential
        backoff up to ``llm_rate_limit_retries`` times.
        """
        import time

        digest = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()
        started = time.monotonic()
        max_attempts = 1 + max(0, int(self._s.llm_rate_limit_retries))
        base_sleep = max(0.5, float(self._s.llm_rate_limit_sleep_seconds))
        last_err: Exception | None = None
        resp = None

        for attempt in range(max_attempts):
            try:
                resp = await self._ainvoke_once(system, user)
                last_err = None
                break
            except Exception as e:
                last_err = e
                if self.is_rate_limit(e) and attempt + 1 < max_attempts:
                    delay = base_sleep * (2 ** attempt)
                    log.warning(
                        "llm_rate_limited_retry",
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                        sleep_s=round(delay, 1),
                        detail=str(e)[:200],
                    )
                    await asyncio.sleep(delay)
                    continue
                # Temperature unsupported: repair once, then one more invoke
                # (without counting against rate-limit budget).
                if "temperature" in str(e).lower() \
                        and ("deprecat" in str(e).lower()
                             or "unsupported" in str(e).lower()):
                    async with self._repair_lock:
                        if self._temperature_ok:
                            log.info("llm_temperature_unsupported",
                                     model=self._model_name)
                            self._temperature_ok = False
                            self._model = None
                            self._build()
                    try:
                        resp = await self._ainvoke_once(system, user)
                        last_err = None
                        break
                    except Exception as e2:
                        last_err = e2
                        if self.is_rate_limit(e2) and attempt + 1 < max_attempts:
                            delay = base_sleep * (2 ** attempt)
                            log.warning(
                                "llm_rate_limited_retry",
                                attempt=attempt + 1,
                                max_attempts=max_attempts,
                                sleep_s=round(delay, 1),
                                detail=str(e2)[:200],
                            )
                            await asyncio.sleep(delay)
                            continue
                        log.warning("llm_call_failed", error=str(e2)[:300])
                        return LLMResult(
                            text="", parsed=None, model=self._model_name,
                            provider=self._provider, prompt_sha256=digest,
                            error=str(e2)[:400])
                log.warning("llm_call_failed", error=str(e)[:300])
                return LLMResult(text="", parsed=None, model=self._model_name,
                                 provider=self._provider, prompt_sha256=digest,
                                 error=str(e)[:400])

        if resp is None:
            err = str(last_err)[:400] if last_err else "no response"
            log.warning("llm_call_failed", error=err)
            return LLMResult(text="", parsed=None, model=self._model_name,
                             provider=self._provider, prompt_sha256=digest,
                             error=err)

        latency = int((time.monotonic() - started) * 1000)
        text = _as_text(resp)
        meta = getattr(resp, "usage_metadata", None) or {}

        # A refusal is a legitimate outcome, not a failure: this pipeline feeds
        # arbitrary org source code to a model, and permission or crypto helpers
        # can trip safety classifiers. It must never alter a verdict.
        refused = getattr(resp, "response_metadata", {}).get("stop_reason") == "refusal"

        parsed = _extract_json(text)
        if parsed and expect:
            missing = [k for k in expect if k not in parsed]
            if missing:
                log.info("llm_missing_fields", missing=missing)

        return LLMResult(
            text=text, parsed=parsed, model=self._model_name,
            provider=self._provider, prompt_sha256=digest,
            input_tokens=meta.get("input_tokens"),
            output_tokens=meta.get("output_tokens"),
            latency_ms=latency, refused=refused,
        )

    async def _ainvoke_once(self, system: str, user: str):
        model = self._build()
        return await model.ainvoke([("system", system), ("human", user)])


    def bind(self, tools: list[dict[str, Any]]):
        """A tool-bound model, with the temperature repair already applied.

        ``json_call`` discovers the temperature rejection lazily and retries.
        The agent loop cannot afford that: a failure mid-conversation would
        surface as a dead turn rather than a retry, so the repair is applied up
        front and the model rebuilt once if needed.
        """
        try:
            return self._build().bind_tools(tools)
        except Exception as e:
            if "temperature" not in str(e).lower():
                raise
            self._temperature_ok = False
            self._model = None
            return self._build().bind_tools(tools)

    def is_rate_limit(self, e: Exception) -> bool:
        msg = str(e)
        return "429" in msg or "rate limit" in msg.lower()

    def describe_rate_limit(self, e: Exception) -> str:
        """Turn a gateway 429 blob into something a user can act on.

        The raw error is a nested JSON string containing the API key hash and a
        reset timestamp. Surfacing it verbatim leaks the key fingerprint into
        the UI and tells the reader nothing useful, so pull out the one fact
        that matters -- when the quota comes back.
        """
        import re

        raw = str(e)
        reset = re.search(r"resets at:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[^'\"}]*)", raw)
        limit = re.search(r"[Cc]urrent limit:\s*(\d+)", raw)
        parts = ["Rate limit reached on the LLM gateway."]
        if limit:
            parts.append(f"The key allows {int(limit.group(1)):,} tokens per window.")
        if reset:
            when = reset.group(1).strip().removesuffix("UTC").strip()
            parts.append(f"Quota resets at {when} UTC.")
        parts.append("Anything the agent already found has been saved.")
        return " ".join(parts)

    def is_temperature_error(self, e: Exception) -> bool:
        msg = str(e).lower()
        return "temperature" in msg and ("deprecat" in msg or "unsupported" in msg)

    async def repair_temperature(self) -> bool:
        """Drop `temperature` and rebuild. True if a repair was actually applied.

        The gateway accepts the parameter at construction and rejects it at
        invoke, so this cannot be probed up front -- it is only ever discovered
        from a failed call. Serialised because concurrent turns would otherwise
        each rebuild and only the first would win.
        """
        async with self._repair_lock:
            if not self._temperature_ok:
                return False           # already repaired by another caller
            log.info("llm_temperature_unsupported", model=self._model_name)
            self._temperature_ok = False
            self._model = None
            self._build()
            return True


def _as_text(resp: Any) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a reply, tolerating code fences."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.removeprefix("json").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    blob = cleaned[start : end + 1]
    try:
        obj = json.loads(blob)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # A trailing comma before a closing brace or bracket is the single most
    # common way an otherwise complete reply fails strict parsing. Throwing the
    # whole answer away over one character loses real signal -- including
    # `still_might_matter`, which is the only field that can push a verdict
    # toward caution. Retry once with those commas removed, and only then fail.
    repaired = re.sub(r",(\s*[}\]])", lambda m: m.group(1), blob)
    if repaired != blob:
        try:
            obj = json.loads(repaired)
            if isinstance(obj, dict):
                log.info("llm_json_repaired", fix="trailing_comma")
                return obj
        except json.JSONDecodeError:
            pass
    return None
