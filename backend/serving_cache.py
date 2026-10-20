"""In-memory cache of built JSON responses (plain and gzip), keyed by data version.

A response is rebuilt only when the data it depends on changes (data_version in
PostgreSQL, bumped by every write) or, for date-dependent results, when the date
changes. While a changed entry is rebuilt in the background, visitors keep getting
the previous version, so nobody waits for a build after a data refresh. Only an
entry that was never built is built while the request waits, once for all
concurrent requests.
"""

from __future__ import annotations

import gzip
import json
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable


@dataclass
class _Entry:
    signature: str
    body: bytes
    compressed: bytes
    signature_of: Callable[[], str]
    build: Callable[[], object]


def _print_error(key: str, exc: Exception) -> None:
    print(f"Rebuilding {key} failed: {exc}", file=sys.stderr, flush=True)


class ResponseCache:
    def __init__(self, max_entries: int = 128,
                 on_error: Callable[[str, Exception], None] = _print_error):
        self.max_entries = max_entries
        self.on_error = on_error
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._build_locks: dict[str, threading.Lock] = {}

    def _entry(self, key: str) -> _Entry | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry:
                self._entries.move_to_end(key)
            return entry

    def get(self, key: str, signature_of: Callable[[], str],
            build: Callable[[], object]) -> tuple[bytes, bytes]:
        """Return (json bytes, gzip bytes) for key.

        signature_of() must be cheap (a data-version read); it runs on every call.
        """
        signature = signature_of()
        entry = self._entry(key)
        if entry and entry.signature == signature:
            return entry.body, entry.compressed
        if entry:  # data changed: serve the previous version while rebuilding
            self._rebuild_in_background(key, signature_of, build)
            return entry.body, entry.compressed
        return self.build(key, signature_of, build, signature)

    def build(self, key: str, signature_of: Callable[[], str], build: Callable[[], object],
              signature: str | None = None) -> tuple[bytes, bytes]:
        """Build key now unless it is already current; one build per key at a time."""
        with self._lock:
            build_lock = self._build_locks.setdefault(key, threading.Lock())
        with build_lock:
            signature = signature or signature_of()
            entry = self._entry(key)
            if entry and entry.signature == signature:  # built by another request meanwhile
                return entry.body, entry.compressed
            body = json.dumps(build(), separators=(",", ":"), default=str).encode()
            compressed = gzip.compress(body, compresslevel=6)
            with self._lock:
                self._entries[key] = _Entry(signature, body, compressed, signature_of, build)
                self._entries.move_to_end(key)
                while len(self._entries) > self.max_entries:
                    evicted, _ = self._entries.popitem(last=False)
                    self._build_locks.pop(evicted, None)
            return body, compressed

    def _rebuild_in_background(self, key: str, signature_of, build) -> None:
        with self._lock:
            build_lock = self._build_locks.setdefault(key, threading.Lock())
        if build_lock.locked():
            return  # already rebuilding

        def rebuild():
            try:
                self.build(key, signature_of, build)
            except Exception as exc:
                self.on_error(key, exc)

        threading.Thread(target=rebuild, name=f"rebuild {key}", daemon=True).start()

    def refresh_stale(self) -> int:
        """Rebuild (now) every cached entry whose data changed; returns how many were rebuilt."""
        with self._lock:
            entries = list(self._entries.items())
        signatures: dict[Callable[[], str], str] = {}
        rebuilt = 0
        for key, entry in entries:
            try:
                if entry.signature_of not in signatures:
                    signatures[entry.signature_of] = entry.signature_of()
                if signatures[entry.signature_of] != entry.signature:
                    self.build(key, entry.signature_of, entry.build)
                    rebuilt += 1
            except Exception as exc:
                self.on_error(key, exc)
        return rebuilt

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
