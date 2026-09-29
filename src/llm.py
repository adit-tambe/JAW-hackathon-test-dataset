"""
llm.py — a small client for the OpenAI-compatible endpoint the harness provides.

That endpoint is the only generative model we are permitted to use, so this is
the single place that talks to it. Three details in the brief are easy to get
wrong and each one fails silently:

  * it is a reasoning model and spends budget on a hidden trace before it
    answers, so a small `max_tokens` returns finish_reason "length" with a null
    `content` — which reads like an outage and is not one;
  * the trace comes back in `reasoning`, not `reasoning_content`;
  * generation is constrained when `response_format` is a json_schema, so
    anything we need to read back reliably should ask for one.

Everything here degrades rather than raises. If the endpoint is unreachable the
caller gets None and the deterministic engine answers alone, because a pipeline
that dies when a shared service hiccups is worse than one that falls back.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

import requests

from src.config import LLM_BASE_URL, LLM_CONCURRENCY, LLM_MAX_TOKENS, LLM_MODEL

_TIMEOUT = (10, 180)          # (connect, read) seconds
_RETRIES = 3
_session = requests.Session()
_lock = threading.Lock()
_stats = {"calls": 0, "retries": 0, "failures": 0, "empty": 0}


_resolved: dict[str, str] = {}


def _candidate_endpoints() -> list[str]:
    """The documented form first, then the obvious near-misses.

    The brief shows `$LLM_BASE_URL/chat/completions`, so that is what we try.
    But whether the exported base already carries the `/v1` prefix is not
    something to bet the run on: if it does not and we assume it does, every
    call 404s, the liveness probe reports the endpoint down, and the model
    layer silently disables itself for the whole run.
    """
    base = LLM_BASE_URL.rstrip("/")
    urls = [f"{base}/chat/completions"]
    if base.endswith("/v1"):
        urls.append(f"{base[:-3].rstrip('/')}/chat/completions")
    else:
        urls.append(f"{base}/v1/chat/completions")
    seen, out = set(), []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def endpoint() -> str:
    return _resolved.get("url") or _candidate_endpoints()[0]


def model_name() -> str:
    return _resolved.get("model") or LLM_MODEL


def _discover_model(chat_url: str) -> None:
    """Ask the server what it actually serves.

    The model id is documented, but a wrong one is rejected on every single
    call, and the symptom — nothing ever answers — looks exactly like an outage.
    Asking costs one request. We keep the configured name whenever the server
    confirms it, and only substitute when it plainly serves something else.
    """
    models_url = chat_url.rsplit("/chat/completions", 1)[0] + "/models"
    try:
        response = _session.get(models_url, timeout=(10, 30))
        served = [m.get("id") for m in (response.json().get("data") or [])
                  if m.get("id")]
    except Exception:
        return
    if served and LLM_MODEL not in served:
        _resolved["model"] = served[0]
        print(f"  note: endpoint serves {served[0]!r}, not {LLM_MODEL!r} — using it")


def resolve_endpoint() -> str | None:
    """Find which candidate URL actually answers, and remember it."""
    if "url" in _resolved:
        return _resolved["url"]
    probe = {"model": LLM_MODEL,
             "messages": [{"role": "user", "content": "Reply with the digit 1 only."}],
             "max_tokens": LLM_MAX_TOKENS, "temperature": 0}
    for url in _candidate_endpoints():
        try:
            response = _session.post(url, json=probe, timeout=(10, 120))
        except Exception:
            continue
        if response.status_code == 404:
            continue
        if response.status_code < 500:
            _resolved["url"] = url
            _discover_model(url)
            return url
    return None


def stats() -> dict:
    with _lock:
        return dict(_stats)


def _bump(key: str, n: int = 1) -> None:
    with _lock:
        _stats[key] = _stats.get(key, 0) + n


def available() -> bool:
    """Liveness probe, used once at startup to decide the run mode.

    Deliberately generous with max_tokens. This is a reasoning model: it spends
    budget on a hidden trace before answering, so a probe with a small cap comes
    back with finish_reason "length" and null content — indistinguishable from
    an outage. A stingy probe would quietly disable the whole LLM path against a
    perfectly healthy endpoint.
    """
    try:
        if resolve_endpoint() is None:
            return False
        reply = chat([{"role": "user", "content": "Reply with the digit 1 only."}],
                     max_tokens=LLM_MAX_TOKENS, retries=2)
        return reply is not None
    except Exception:
        return False


def chat(messages: list[dict], *, schema: dict | None = None,
         max_tokens: int | None = None, temperature: float = 0.0,
         retries: int = _RETRIES) -> str | None:
    """One chat completion. Returns assistant content, or None on failure."""
    payload: dict[str, Any] = {
        "model": model_name(),
        "messages": messages,
        # Generous by default: too small and a reasoning model burns the whole
        # budget on its trace and returns nothing.
        "max_tokens": max_tokens or LLM_MAX_TOKENS,
        "temperature": temperature,
    }
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "strict": True, "schema": schema},
        }

    delay = 1.0
    for attempt in range(retries):
        try:
            _bump("calls")
            response = _session.post(endpoint(), json=payload, timeout=_TIMEOUT)
            if response.status_code >= 500 or response.status_code == 429:
                raise requests.HTTPError(f"HTTP {response.status_code}")
            response.raise_for_status()
            choice = (response.json().get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content")
            if content:
                return content
            # finish_reason "length" means the trace ate the budget. Retrying
            # with more room is the documented fix, not a reason to give up.
            _bump("empty")
            if choice.get("finish_reason") == "length":
                payload["max_tokens"] = min(int(payload["max_tokens"] * 2), 32768)
        except Exception:
            _bump("retries")
        if attempt < retries - 1:
            time.sleep(delay)
            delay *= 2
    _bump("failures")
    return None


def chat_json(messages: list[dict], schema: dict, **kwargs) -> dict | None:
    """Constrained-decoding call whose result is parsed for you."""
    raw = chat(messages, schema=schema, **kwargs)
    if not raw:
        return None
    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Constrained decoding should make this unreachable; belt and braces.
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                parsed = None
    # Valid JSON is not necessarily an object: "1" parses fine and would then
    # blow up on the first .get() at the call site.
    return parsed if isinstance(parsed, dict) else None


def map_concurrent(fn: Callable, items: Iterable, workers: int | None = None) -> list:
    """Run `fn` over `items` with a modest worker pool.

    The endpoint is shared across finalists during the window, and the brief is
    explicit that a client opening hundreds of parallel connections gets
    throttled and loses more time than it gains.
    """
    items = list(items)
    if not items:
        return []
    workers = max(1, min(workers or LLM_CONCURRENCY, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))
