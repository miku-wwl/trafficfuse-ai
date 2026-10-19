"""Local Ollama client for incident diagnosis (loopback only, one attempt).

The endpoint must be an ``http`` loopback URL with an explicit port; no proxy
or environment trust, no redirects, no retries, bounded response size. The
model is asked for structured JSON (Ollama ``format``), but the result is
still treated as untrusted text and validated by
:func:`trafficfuse.domain.diagnosis.parse_diagnosis`.
"""

from __future__ import annotations

import ipaddress
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from trafficfuse.domain.diagnosis import OUTPUT_SCHEMA, PROMPT_VERSION, SYSTEM_PROMPT

DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_MODEL = "phi4-mini"
MAX_RESPONSE_BYTES = 256 * 1024


class ModelUnavailable(RuntimeError):
    """The local model could not produce a response (no fabricated fallback)."""


def validate_endpoint(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    try:
        loopback = parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if (
        parts.scheme != "http"
        or not loopback
        or parts.port is None
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError("the Ollama endpoint must be a loopback http URL with a port")
    return endpoint.rstrip("/")


@dataclass(frozen=True)
class ModelResponse:
    content: str
    model: str
    model_digest: str | None
    prompt_version: str
    duration_seconds: float
    prompt_tokens: int | None
    output_tokens: int | None
    done_reason: str | None

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": "ollama (local)",
            "model": self.model,
            "model_digest": self.model_digest,
            "prompt_version": self.prompt_version,
            "duration_seconds": round(self.duration_seconds, 2),
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "done_reason": self.done_reason,
        }


class OllamaDiagnosisClient:
    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        *,
        timeout_seconds: float = 240.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = validate_endpoint(endpoint)
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport or httpx.AsyncHTTPTransport(retries=0),
            timeout=httpx.Timeout(self.timeout_seconds, connect=3.0),
            follow_redirects=False,
            trust_env=False,
        )

    async def model_digest(self) -> str | None:
        try:
            async with await self._client() as client:
                response = await client.get(self.endpoint + "/api/tags")
                for entry in response.json().get("models", []):
                    if entry.get("name") in (self.model, f"{self.model}:latest"):
                        return entry.get("digest")
        except Exception:
            return None
        return None

    async def available(self) -> bool:
        return (await self.model_digest()) is not None

    async def diagnose(self, prompt: str) -> ModelResponse:
        payload = {
            "model": self.model,
            "stream": False,
            "format": OUTPUT_SCHEMA,
            "options": {"temperature": 0, "num_predict": 700, "num_ctx": 4096, "seed": 7},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        started = time.monotonic()
        try:
            async with await self._client() as client:
                response = await client.post(self.endpoint + "/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ModelUnavailable("local model timed out") from exc
        except httpx.TransportError as exc:
            raise ModelUnavailable("local model endpoint unreachable") from exc
        if response.status_code != 200:
            raise ModelUnavailable(f"local model returned HTTP {response.status_code}")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ModelUnavailable("local model response exceeded the size limit")
        try:
            envelope = response.json()
            content = envelope["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
        except (ValueError, KeyError, TypeError) as exc:
            raise ModelUnavailable("local model returned an invalid envelope") from exc
        digest = await self.model_digest()
        return ModelResponse(
            content=content,
            model=str(envelope.get("model", self.model)),
            model_digest=digest,
            prompt_version=PROMPT_VERSION,
            duration_seconds=time.monotonic() - started,
            prompt_tokens=envelope.get("prompt_eval_count"),
            output_tokens=envelope.get("eval_count"),
            done_reason=envelope.get("done_reason"),
        )


def fixture_response(content: str) -> ModelResponse:
    """Deterministic *fixture* response for offline tests; never shown as real."""
    return ModelResponse(
        content=content,
        model="TEST_FIXTURE (not a model)",
        model_digest=None,
        prompt_version=PROMPT_VERSION,
        duration_seconds=0.0,
        prompt_tokens=None,
        output_tokens=None,
        done_reason="fixture",
    )


def describe_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)
