"""Tests for the active stream-id cache used by the sidebar rebuild."""
import time
from unittest.mock import patch

import api.models as models


def test_cached_active_stream_ids_returns_fresh_set():
    """Without a cache entry, the function should compute the active set."""
    models._ACTIVE_STREAM_IDS_CACHE = (0.0, set(), -1, -1)
    with (
        patch.object(models, "STREAMS", {"stream-1": None}),
        patch.object(models._cfg, "ACTIVE_RUNS", {"run-1": None}),
    ):
        result = models._cached_active_stream_ids()
    assert result == {"stream-1", "run-1"}


def test_cached_active_stream_ids_uses_ttl():
    """Within the TTL, repeated calls should return the cached set."""
    models._ACTIVE_STREAM_IDS_CACHE = (
        time.monotonic(), {"cached"},
        len(models.STREAMS), len(models._cfg.ACTIVE_RUNS),
    )
    with patch.object(models, "_active_stream_ids", side_effect=AssertionError("should not refresh")):
        result = models._cached_active_stream_ids()
    assert result == {"cached"}


def test_cached_active_stream_ids_refreshes_after_ttl():
    """After the TTL expires the cache should refresh."""
    models._ACTIVE_STREAM_IDS_CACHE = (
        time.monotonic() - 5.0, {"stale"},
        len(models.STREAMS), len(models._cfg.ACTIVE_RUNS),
    )
    with (
        patch.object(models, "STREAMS", {"stream-2": None}),
        patch.object(models._cfg, "ACTIVE_RUNS", {}),
    ):
        result = models._cached_active_stream_ids()
    assert result == {"stream-2"}


def test_cached_active_stream_ids_refreshes_when_registry_sizes_change():
    """Even inside the TTL, a registry size change invalidates the cache."""
    models._ACTIVE_STREAM_IDS_CACHE = (
        time.monotonic(), set(),
        0, 0,
    )
    with (
        patch.object(models, "STREAMS", {"stream-3": None}),
        patch.object(models._cfg, "ACTIVE_RUNS", {}),
    ):
        result = models._cached_active_stream_ids()
    assert result == {"stream-3"}
