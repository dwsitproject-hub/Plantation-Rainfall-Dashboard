"""In-memory response cache: rebuilt only when the data signature changes."""

from __future__ import annotations

import gzip
import json
import threading
import time

from backend.serving_cache import ResponseCache


def test_entry_is_reused_until_the_signature_changes():
    cache, builds = ResponseCache(), []

    def build():
        builds.append(1)
        return {"n": len(builds)}

    body, compressed = cache.get("doc", lambda: "v1", build)
    assert json.loads(body) == {"n": 1} and gzip.decompress(compressed) == body
    assert cache.get("doc", lambda: "v1", build)[0] == body and len(builds) == 1
    # Data changed: the previous version is served while the new one builds in the background.
    assert json.loads(cache.get("doc", lambda: "v2", build)[0]) == {"n": 1}
    deadline = time.monotonic() + 5
    while json.loads(cache.get("doc", lambda: "v2", build)[0]) != {"n": 2}:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert len(builds) == 2


def test_failed_background_rebuild_keeps_serving_the_previous_version():
    errors = []
    cache = ResponseCache(on_error=lambda key, exc: errors.append(key))
    cache.get("doc", lambda: "v1", lambda: {"ok": 1})

    def broken():
        raise RuntimeError("database down")

    assert cache.get("doc", lambda: "v2", broken)[0] == b'{"ok":1}'
    deadline = time.monotonic() + 5
    while not errors:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert errors == ["doc"] and cache.get("doc", lambda: "v2", broken)[0] == b'{"ok":1}'


def test_concurrent_misses_build_once():
    cache, builds = ResponseCache(), []

    def slow_build():
        builds.append(1)
        time.sleep(0.2)
        return {"ok": True}

    threads = [threading.Thread(target=cache.get, args=("doc", lambda: "v1", slow_build)) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(builds) == 1


def test_failed_build_is_not_cached_and_old_entries_are_evicted():
    cache = ResponseCache(max_entries=2)

    def broken():
        raise ValueError("bad")

    try:
        cache.get("doc", lambda: "v1", broken)
    except ValueError:
        pass
    assert cache.get("doc", lambda: "v1", lambda: {"ok": 1})[0] == b'{"ok":1}'
    cache.get("a", lambda: "v1", lambda: 1)
    cache.get("b", lambda: "v1", lambda: 2)
    assert cache.get("doc", lambda: "v1", lambda: {"rebuilt": 1})[0] == b'{"rebuilt":1}'


def test_refresh_stale_rebuilds_only_changed_entries():
    cache, version, builds = ResponseCache(), {"weather": "v1", "plantation": "p1"}, []

    def builder(name):
        def build():
            builds.append(name)
            return {name: version[name]}
        return build

    weather, plantation = (lambda: version["weather"]), (lambda: version["plantation"])
    cache.get("w", weather, builder("weather"))
    cache.get("p", plantation, builder("plantation"))
    version["weather"] = "v2"
    assert cache.refresh_stale() == 1 and builds == ["weather", "plantation", "weather"]
    assert cache.get("w", weather, builder("weather"))[0] == b'{"weather":"v2"}' and len(builds) == 3
