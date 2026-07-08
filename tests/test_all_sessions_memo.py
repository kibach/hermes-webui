"""Tests for the all_sessions() short-lived result memoization cache."""
import time
from unittest.mock import patch

import pytest

import api.models as models
from api.models import Session


def _make_session(session_id, title="Untitled", **kwargs):
    return Session(
        session_id=session_id,
        title=title,
        messages=kwargs.pop("messages", [{"role": "user", "content": "hi"}]),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _isolate_and_reset(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    index_file = session_dir / "_index.json"
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_file)
    models.SESSIONS.clear()
    models._ALL_SESSIONS_RESULT_CACHE.clear()
    if hasattr(models, "_PERSISTED_SESSION_IDS_CACHE"):
        models._PERSISTED_SESSION_IDS_CACHE = (None, None, frozenset())
    yield
    models.SESSIONS.clear()
    models._ALL_SESSIONS_RESULT_CACHE.clear()
    if hasattr(models, "_PERSISTED_SESSION_IDS_CACHE"):
        models._PERSISTED_SESSION_IDS_CACHE = (None, None, frozenset())


def test_all_sessions_memoizes_same_inputs():
    """Back-to-back calls with identical inputs should reuse the cached result."""
    s = _make_session("memo_a", title="Memo A")
    s.save()
    models._write_session_index()

    first = models.all_sessions(include_lineage_metadata=False)
    call_count = {"n": 0}

    # Patch the expensive state.db override application to detect re-execution.
    original_apply = models._apply_sidebar_state_db_overrides

    def counting_apply(sessions):
        call_count["n"] += 1
        return original_apply(sessions)

    with patch.object(models, "_apply_sidebar_state_db_overrides", side_effect=counting_apply):
        second = models.all_sessions(include_lineage_metadata=False)

    assert len(first) == len(second) == 1
    assert first[0]["session_id"] == second[0]["session_id"] == "memo_a"
    # The memo path should NOT re-run the override pass.
    assert call_count["n"] == 0, "all_sessions() should reuse the memoized result"


def test_all_sessions_rebuilds_after_index_change():
    """A new session (index mtime change) must invalidate the memo."""
    s1 = _make_session("memo_b1", title="First")
    s1.save()
    models._write_session_index()

    first = models.all_sessions(include_lineage_metadata=False)
    assert len(first) == 1

    time.sleep(0.01)
    s2 = _make_session("memo_b2", title="Second")
    s2.save()
    models._write_session_index()

    second = models.all_sessions(include_lineage_metadata=False)
    assert len(second) == 2


def test_all_sessions_returns_independent_copies():
    """Callers mutating the returned list must not corrupt the cache."""
    s = _make_session("memo_c", title="Copy")
    s.save()
    models._write_session_index()

    first = models.all_sessions(include_lineage_metadata=False)
    first[0]["title"] = "MUTATED"

    second = models.all_sessions(include_lineage_metadata=False)
    assert second[0]["title"] == "Copy"
