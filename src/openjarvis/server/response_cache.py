"""In-memory LRU response cache with TTL for deterministic chat completions."""

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openjarvis.server.cache")

_DEFAULT_TTL = 300  # 5 minutes
_DEFAULT_MAX = 256
_MAX_CACHEABLE_TEMP = 0.1


@dataclass(frozen=True, slots=True)
class _Entry:
    data: Dict[str, Any]
    created: float
    ttl: float

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > self.ttl


class ResponseCache:
    def __init__(self, max_entries: int = _DEFAULT_MAX, ttl: float = _DEFAULT_TTL):
        self._max = max_entries
        self._ttl = ttl
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def make_key(model: str, messages: list, temperature: float, max_tokens: int) -> str:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": m.get("role", ""), "content": m.get("content", "")} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def is_cacheable(temperature: float, stream: bool, has_tools: bool) -> bool:
        return not stream and not has_tools and temperature <= _MAX_CACHEABLE_TEMP

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.expired:
                del self._store[key]
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            logger.debug("Response cache hit (key=%s…)", key[:8])
            return entry.data

    def put(self, key: str, data: Dict[str, Any]) -> None:
        with self._lock:
            if key in self._store:
                del self._store[key]
            self._store[key] = _Entry(data=data, created=time.monotonic(), ttl=self._ttl)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    @property
    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._store)}
