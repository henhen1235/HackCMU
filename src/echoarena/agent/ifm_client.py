"""Async LLM client — xAI Grok via OpenAI-compatible chat completions."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env")

# Prefer XAI_*; keep IFM_* as fallbacks for older .env files
XAI_API_KEY = (
    os.environ.get("XAI_API_KEY", "").strip()
    or os.environ.get("IFM_API_KEY", "").strip()
)
XAI_BASE_URL = (
    os.environ.get("XAI_BASE_URL", "").strip()
    or os.environ.get("IFM_BASE_URL", "").strip()
    or "https://api.x.ai/v1"
).rstrip("/")
# Non-reasoning = lowest latency for the 60 FPS planner loop
XAI_MODEL = (
    os.environ.get("XAI_MODEL", "").strip()
    or os.environ.get("IFM_MODEL", "").strip()
    or "grok-4.20-0309-non-reasoning"
)

# Back-compat aliases used across the codebase
IFM_API_KEY = XAI_API_KEY
IFM_BASE_URL = XAI_BASE_URL
IFM_MODEL = XAI_MODEL

_client: httpx.AsyncClient | None = None


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# Back-compat
IFMError = LLMError


def _shared_client(timeout: float) -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    return _client


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    max_tokens: int = 300,
    temperature: float = 0.4,
    timeout: float = 8.0,
) -> str:
    """POST /chat/completions and return assistant text."""
    if not XAI_API_KEY:
        raise LLMError("XAI_API_KEY is missing — set it in .env")

    url = f"{XAI_BASE_URL}/chat/completions"
    payload: dict = {
        "model": model or XAI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # grok-4.6 defaults to heavy reasoning — force low if that model is selected
    chosen = payload["model"]
    if isinstance(chosen, str) and chosen.startswith("grok-4.6"):
        payload["reasoning_effort"] = "low"

    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    client = _shared_client(timeout)
    try:
        resp = await client.post(url, headers=headers, json=payload, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise LLMError(f"xAI timeout after {timeout:.1f}s") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"xAI transport error: {exc}") from exc

    if resp.status_code >= 400:
        raise LLMError(
            f"xAI HTTP {resp.status_code}: {resp.text[:400]}",
            status_code=resp.status_code,
        )

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            # Some gateways return content parts
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content).strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise LLMError(f"Unexpected xAI response shape: {data!r}") from exc
