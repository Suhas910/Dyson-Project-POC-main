"""llm.py - Language-model provider for the interpretive layer.

The analysis pipeline is deterministic; this module supplies the one part that
is not. It talks to OpenRouter, which exposes an OpenAI-compatible API, so the
official OpenAI SDK is used as the transport with the base URL repointed.

Three properties matter more than the model choice:

Optional. If no API key is configured the client reports itself unavailable and
the pipeline falls back to deterministic behaviour. The tool must still run,
and still produce every geometric verdict, with no credentials at all -- a demo
machine without a key gets a complete report minus the commentary.

Non-fatal. Every call is wrapped so that a timeout, a rate limit, or a malformed
response degrades the report rather than failing the analysis. A DFM finding
that took real geometry to compute must not be lost because a text service was
slow.

Constrained. Responses are requested as JSON against an explicit schema, so the
caller gets a validated object rather than prose it has to parse. The model is
never asked for, and is never allowed to supply, a compliance verdict.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

# Load backend/.env if present. override=False so a variable already exported in
# the shell (a CI secret, for example) wins over the checked-out file.
load_dotenv(Path(__file__).parent / ".env", override=False)


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "~deepseek/deepseek-v4-flash-latest"


@dataclass
class LLMSettings:
    """Provider configuration, read from the environment."""

    api_key: Optional[str] = None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    # Reasoning models spend output tokens deliberating before they answer.
    # DeepSeek V4 Flash enables it by default, and on these tasks it costs
    # roughly 5x the latency (29s vs 5s on a measured summary call) for output
    # that is no more useful: the interpretive prompts are short, the schema is
    # fixed, and the engineering judgement asked for is shallow. Off by default;
    # set OPENROUTER_REASONING_EFFORT to low/high/max to turn it back on.
    reasoning_effort: str = "none"
    timeout_seconds: float = 120.0
    max_retries: int = 2
    # Generous, because the failure mode when a batch overruns is losing every
    # commentary in it, not just the overflow. Batch size is the real control
    # on response length (see orchestrator.BATCH_SIZE); this is the backstop.
    max_output_tokens: int = 8192
    # OpenRouter attributes traffic to an app via these optional headers.
    app_url: str = "https://github.com/Suhas910/Dyson-Project-POC-main"
    app_title: str = "Dyson DFM Analysis"

    def reasoning_body(self) -> dict:
        """The OpenRouter `reasoning` parameter for this configuration.

        Anything that reads as "off" disables reasoning outright rather than
        requesting the lowest tier, because on this model the lowest tier is
        still the dominant cost in a request.
        """
        if self.reasoning_effort.strip().lower() in ("none", "off", "disabled", "no", ""):
            return {"enabled": False}
        return {"effort": self.reasoning_effort.strip().lower()}

    @classmethod
    def from_env(cls) -> "LLMSettings":
        def _float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, default))
            except (TypeError, ValueError):
                logging.warning(f"{name} is not a number; using {default}")
                return default

        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, default))
            except (TypeError, ValueError):
                logging.warning(f"{name} is not an integer; using {default}")
                return default

        return cls(
            api_key=os.getenv("OPENROUTER_API_KEY") or None,
            base_url=os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
            model=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL),
            reasoning_effort=os.getenv("OPENROUTER_REASONING_EFFORT", "none"),
            timeout_seconds=_float("OPENROUTER_TIMEOUT_SECONDS", 120.0),
            max_retries=_int("OPENROUTER_MAX_RETRIES", 2),
            max_output_tokens=_int("OPENROUTER_MAX_OUTPUT_TOKENS", 8192),
            app_url=os.getenv("OPENROUTER_APP_URL", cls.app_url),
            app_title=os.getenv("OPENROUTER_APP_TITLE", cls.app_title),
        )


@dataclass
class LLMUsage:
    """What the interpretive layer cost and whether it worked.

    Surfaced in the API response so the report can state plainly whether its
    commentary came from a model or from the deterministic fallback -- an
    analysis should never leave the reader guessing which one they are reading.
    """

    provider: str = "openrouter"
    model: str = ""
    enabled: bool = False
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def as_metadata(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "enabled": self.enabled,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "errors": self.errors,
        }


def _extract_json(text: str) -> Any:
    """Parses a JSON object out of a model response.

    Structured output mode should return bare JSON, but a model can still wrap
    it in a markdown fence or add a sentence of preamble. Rather than fail the
    finding, fall back to the outermost brace-delimited span.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    if text.startswith("```"):
        fenced = text.split("```")
        for chunk in fenced:
            chunk = chunk.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("{") or chunk.startswith("["):
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    continue

    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"could not parse JSON from response: {text[:200]}")


def _is_mandatory_reasoning_error(exc: Exception) -> bool:
    """True when a model rejected the request because reasoning cannot be off.

    Matched on the message rather than the status code: a 400 covers many
    unrelated problems, and retrying all of them without the reasoning
    parameter would hide real errors behind a second identical failure.
    """
    text = str(exc).lower()
    return "reasoning" in text and (
        "mandatory" in text or "cannot be disabled" in text
    )


class LLMClient:
    """Thin wrapper over the OpenRouter chat-completions endpoint."""

    def __init__(self, settings: Optional[LLMSettings] = None):
        self.settings = settings or LLMSettings.from_env()
        self.usage = LLMUsage(model=self.settings.model)
        self._usage_lock = threading.Lock()
        self._client = None

        if not self.settings.api_key:
            logging.info(
                "OPENROUTER_API_KEY is not set; interpretive commentary will use "
                "the deterministic fallback."
            )
            return

        try:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
                timeout=self.settings.timeout_seconds,
                max_retries=self.settings.max_retries,
                default_headers={
                    "HTTP-Referer": self.settings.app_url,
                    "X-Title": self.settings.app_title,
                },
            )
            self.usage.enabled = True
        except ImportError:
            logging.error(
                "The 'openai' package is required for OpenRouter access. "
                "Install it with: pip install openai"
            )
        except Exception as exc:
            logging.error(f"Could not initialise the OpenRouter client: {exc}")

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        schema_name: str = "response",
        attempts: int = 2,
    ) -> Optional[dict]:
        """Requests a JSON object, retrying once on a bad response.

        OpenRouter fans a model out across many providers and picks one per
        request, so an unusable response is often a property of the provider
        that happened to serve it rather than of the request. Re-issuing is
        usually enough to land somewhere that answers correctly, and losing a
        whole batch of commentary to one unlucky route is not worth avoiding a
        second call that costs a fraction of a cent.
        """
        for attempt in range(attempts):
            result = self._attempt_json(
                system_prompt, user_prompt, schema, schema_name
            )
            if result is not None:
                return result
            if attempt + 1 < attempts:
                logging.info(f"Retrying {schema_name} request ({attempt + 2}/{attempts})")
        return None

    def _attempt_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        schema_name: str = "response",
    ) -> Optional[dict]:
        """Requests a JSON object matching `schema`.

        Returns None on any failure -- an unavailable client, a transport error,
        or an unparseable response -- so callers can fall back without having to
        distinguish the cases. The reason is recorded on `usage.errors` and
        surfaced in the report.
        """
        if not self.is_available:
            return None

        started = time.monotonic()
        try:
            response = self._create(system_prompt, user_prompt, schema, schema_name)
        except Exception as exc:
            # Some models (Qwen 3.8 Max among them) mandate reasoning and
            # reject a request that switches it off. Rather than making the
            # caller know which models those are, drop the override and retry
            # once; the model then applies its own default.
            if _is_mandatory_reasoning_error(exc):
                logging.info(
                    f"{self.settings.model} requires reasoning; retrying without "
                    "the override."
                )
                try:
                    response = self._create(
                        system_prompt, user_prompt, schema, schema_name,
                        omit_reasoning=True,
                    )
                except Exception as retry_exc:
                    exc = retry_exc
                else:
                    exc = None
            if exc is not None:
                message = f"{type(exc).__name__}: {exc}"
                logging.error(f"OpenRouter request failed: {message}")
                with self._usage_lock:
                    self.usage.errors.append(message)
                    self.usage.elapsed_seconds += time.monotonic() - started
                return None

        return self._parse(response, started)

    def _create(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        schema_name: str,
        omit_reasoning: bool = False,
    ):
        extra_body = {
            # OpenRouter load-balances a model across every provider serving
            # it, and they do not all honour the same parameters. Six of the 27
            # behind the default model ignore structured outputs, so without
            # this flag roughly one request in four landed on a provider that
            # free-formed its JSON -- dropping required fields and running long
            # enough to truncate. This restricts routing to providers that
            # support every parameter actually sent.
            "provider": {"require_parameters": True},
        }
        if not omit_reasoning:
            extra_body["reasoning"] = self.settings.reasoning_body()

        return self._client.chat.completions.create(
            model=self.settings.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=self.settings.max_output_tokens,
            # Structured outputs: the provider constrains generation to the
            # schema, which removes the "model returned prose instead of
            # JSON" failure mode rather than handling it after the fact.
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            extra_body=extra_body,
        )

    def _parse(self, response, started: float) -> Optional[dict]:
        """Records usage and turns the response into a validated object."""
        # The interpretive agents run concurrently, so usage is updated from
        # more than one thread.
        usage = getattr(response, "usage", None)
        with self._usage_lock:
            self.usage.calls += 1
            self.usage.elapsed_seconds += time.monotonic() - started
            if usage:
                self.usage.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.usage.completion_tokens += (
                    getattr(usage, "completion_tokens", 0) or 0
                )

        # A response cut off at the token ceiling is not malformed JSON, it is
        # an incomplete one. Reporting it as a parse failure sends whoever
        # debugs it looking at the schema instead of at max_tokens.
        choice = response.choices[0] if response.choices else None
        if choice is not None and getattr(choice, "finish_reason", None) == "length":
            message = (
                f"response hit the {self.settings.max_output_tokens}-token output "
                "ceiling and was truncated; lower the batch size or raise "
                "OPENROUTER_MAX_OUTPUT_TOKENS"
            )
            logging.error(message)
            with self._usage_lock:
                self.usage.errors.append(message)
            return None

        try:
            parsed = _extract_json(choice.message.content if choice else None)
        except Exception as exc:
            message = f"unparseable response: {exc}"
            logging.error(message)
            with self._usage_lock:
                self.usage.errors.append(message)
            return None

        if not isinstance(parsed, dict):
            self.usage.errors.append("response was not a JSON object")
            return None
        return parsed


_client: Optional[LLMClient] = None


def get_client(refresh: bool = False) -> LLMClient:
    """Returns the process-wide client.

    Cached because constructing the transport is not free and the settings do
    not change within a run; pass refresh=True after changing the environment.
    """
    global _client
    if _client is None or refresh:
        _client = LLMClient()
    return _client
