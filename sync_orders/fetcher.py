"""Fetching the raw orders from the upstream API."""
from __future__ import annotations

import time
from typing import Any, Dict, List

import requests

DEFAULT_SOURCE_URL = "https://test.ooobs.ru/api/orders"


class SourceError(RuntimeError):
    pass


def fetch_orders(
    url: str = DEFAULT_SOURCE_URL,
    timeout: float = 20.0,
    retries: int = 3,
    backoff: float = 1.5,
    session: requests.Session = None,
) -> List[Dict[str, Any]]:
    """GET the upstream JSON array of orders, with a small retry loop."""
    http = session or requests
    last_error: Exception = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            response = http.get(url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                for key in ("orders", "data", "items", "results"):
                    if isinstance(payload.get(key), list):
                        payload = payload[key]
                        break
            if not isinstance(payload, list):
                raise SourceError("unexpected payload type: %s" % type(payload).__name__)
            return payload
        except Exception as exc:  # noqa: BLE001 - retried below
            last_error = exc
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise SourceError("cannot fetch %s: %s" % (url, last_error)) from last_error