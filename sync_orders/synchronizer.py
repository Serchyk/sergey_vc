"""Synchronizer: pull -> normalize -> dedupe -> persist."""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from .core import SyncResult, build_result
from .fetcher import DEFAULT_SOURCE_URL, fetch_orders
from .storage import Store, payload_digest


class Synchronizer:
    def __init__(
        self,
        store: Store,
        source_url: str = DEFAULT_SOURCE_URL,
        timeout: float = 20.0,
    ):
        self.store = store
        self.source_url = source_url
        self.timeout = timeout
        self._lock = threading.Lock()

    def run(self, raw_orders: Optional[List[Dict[str, Any]]] = None) -> SyncResult:
        """Synchronize once. Serialized by a lock so concurrent callers cannot
        interleave two snapshots."""
        with self._lock:
            payload = fetch_orders(self.source_url, timeout=self.timeout) if raw_orders is None else raw_orders
            result = build_result(payload)
            self.store.replace_snapshot(payload, result, payload_digest(payload))
            return result

    def result(self) -> Optional[SyncResult]:
        return self.store.load_result()