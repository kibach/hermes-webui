"""Tests for the short-lived cache around _read_state_db_sidebar_overrides.

The cache is a performance-only layer: it must return identical metadata to the
uncached function for the same state.db fingerprint and id sets, and it must
invalidate when the fingerprint changes.
"""
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import api.models as models


@pytest.fixture
def tmp_state_db(tmp_path):
    """Create a small state.db with sessions and messages tables."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, title TEXT, message_count INTEGER)")
    conn.execute("CREATE TABLE messages (session_id TEXT, timestamp REAL)")
    conn.executemany(
        "INSERT INTO sessions VALUES (?, 'webui', ?, ?)",
        [(f"sid{i}", f"Session {i}", i) for i in range(10)],
    )
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?)",
        [(f"sid{i}", float(i)) for i in range(100)],
    )
    conn.commit()
    conn.close()
    models._STATE_DB_SIDEBAR_OVERRIDES_CACHE.clear()
    yield db_path
    models._STATE_DB_SIDEBAR_OVERRIDES_CACHE.clear()


def _make_ids(count: int):
    return {f"sid{i}" for i in range(count)}


def test_cached_result_matches_uncached(tmp_state_db):
    ids = _make_ids(10)
    uncached = models._read_state_db_sidebar_overrides(
        tmp_state_db, ids, count_session_ids=ids,
    )
    cached = models._cached_read_state_db_sidebar_overrides(
        tmp_state_db, ids, count_session_ids=ids,
    )
    assert cached == uncached
    assert len(cached) == 10


def test_cache_hit_avoids_second_query(tmp_state_db):
    ids = _make_ids(10)
    calls = []
    real_fn = models._read_state_db_sidebar_overrides

    def counting_read(db_path, session_ids, count_session_ids=None):
        calls.append((set(session_ids), set(count_session_ids) if count_session_ids else None))
        return real_fn(db_path, session_ids, count_session_ids=count_session_ids)

    with patch.object(models, "_read_state_db_sidebar_overrides", side_effect=counting_read):
        first = models._cached_read_state_db_sidebar_overrides(
            tmp_state_db, ids, count_session_ids=ids,
        )
        second = models._cached_read_state_db_sidebar_overrides(
            tmp_state_db, ids, count_session_ids=ids,
        )

    assert first == second
    assert len(calls) == 1, "second lookup should be served from cache"


def test_cache_invalidates_on_fingerprint_change(tmp_state_db):
    ids = _make_ids(10)
    first = models._cached_read_state_db_sidebar_overrides(
        tmp_state_db, ids, count_session_ids=ids,
    )

    # Insert a new message so the fingerprint advances.
    conn = sqlite3.connect(str(tmp_state_db))
    conn.execute("INSERT INTO messages VALUES ('sid0', ?)", (time.time(),))
    conn.commit()
    conn.close()

    second = models._cached_read_state_db_sidebar_overrides(
        tmp_state_db, ids, count_session_ids=ids,
    )
    assert second != first or len(second) == 0
    # The new message is for sid0, so its override should now reflect it.
    assert second["sid0"]["_state_db_message_count"] > first["sid0"]["_state_db_message_count"]


def test_cache_key_is_order_independent(tmp_state_db):
    ids_a = {"sid0", "sid1", "sid2"}
    ids_b = {"sid2", "sid0", "sid1"}
    key_a = models._state_db_sidebar_overrides_cache_key(
        tmp_state_db, ids_a, ids_a, (1, 2),
    )
    key_b = models._state_db_sidebar_overrides_cache_key(
        tmp_state_db, ids_b, ids_b, (1, 2),
    )
    assert key_a == key_b


def test_cache_falls_back_when_fingerprint_unavailable(tmp_state_db):
    ids = _make_ids(10)
    with patch.object(models, "_sqlite_content_fingerprint", return_value=None):
        result = models._cached_read_state_db_sidebar_overrides(
            tmp_state_db, ids, count_session_ids=ids,
        )
    assert len(result) == 10


def test_cache_is_bounded_and_cleans_expired(tmp_state_db):
    ids = _make_ids(10)
    models._cached_read_state_db_sidebar_overrides(
        tmp_state_db, ids, count_session_ids=ids,
    )
    # Force expiration and insert many new entries by advancing fingerprint.
    for i in range(150):
        with patch.object(
            models,
            "_sqlite_content_fingerprint",
            return_value=(i, i),
        ):
            models._cached_read_state_db_sidebar_overrides(
                tmp_state_db, ids, count_session_ids=ids,
            )

    assert len(models._STATE_DB_SIDEBAR_OVERRIDES_CACHE) <= 128
