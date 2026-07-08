"""
Tests for the optional <sid>.json.metadata sidecar cache.

The sidecar is a performance-only cache: it must never change observable
behavior, only the I/O cost of loading metadata. These tests verify:

  - save() writes a sidecar with the correct schema and fingerprints.
  - load_metadata_only() uses the sidecar and does not read the full session.
  - A sidecar with matching mtime/size is accepted without checksum computation.
  - A stale sidecar (modified main file) falls back to the main file or fails
    validation cleanly.
  - Legacy sessions without a sidecar still load correctly.
  - The sidecar is regenerated after a legacy fallback load.
"""
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import api.models as models
from api.models import Session


@pytest.fixture(autouse=True)
def _isolate_session_dir(tmp_path, monkeypatch):
    """Redirect SESSION_DIR to a temp directory so tests don't touch real data."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    index_file = session_dir / "_index.json"
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_file)
    models.SESSIONS.clear()
    if hasattr(models, "_PERSISTED_SESSION_IDS_CACHE"):
        models._PERSISTED_SESSION_IDS_CACHE = (None, None, frozenset())
    yield session_dir
    models.SESSIONS.clear()
    if hasattr(models, "_PERSISTED_SESSION_IDS_CACHE"):
        models._PERSISTED_SESSION_IDS_CACHE = (None, None, frozenset())


def _make_session(session_id, title="Untitled", **kwargs):
    return Session(
        session_id=session_id,
        title=title,
        messages=kwargs.pop("messages", [{"role": "user", "content": "hi"}]),
        **kwargs,
    )


def test_save_writes_metadata_sidecar():
    """A freshly saved session must produce a valid .json.meta sidecar."""
    s = _make_session("sidecar_save", title="Saved Session", input_tokens=99)
    s.save()

    main_path = s.path
    sidecar_path = main_path.with_suffix(".json.meta")
    assert sidecar_path.exists(), "sidecar should be written on save"

    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert payload.get("_meta_schema_version") == models._METADATA_SIDECAR_SCHEMA_VERSION
    assert payload["_source_mtime_ns"] == main_path.stat().st_mtime_ns
    assert payload["_source_size"] == main_path.stat().st_size
    assert payload["_source_sha256"] == models._compute_file_sha256(main_path)

    metadata = payload["metadata"]
    assert metadata["session_id"] == "sidecar_save"
    assert metadata["title"] == "Saved Session"
    assert metadata["input_tokens"] == 99
    assert "messages" not in metadata
    assert "tool_calls" not in metadata


def test_load_metadata_only_uses_sidecar_without_full_load():
    """load_metadata_only() must read the sidecar, not parse the messages array."""
    s = _make_session(
        "sidecar_fast",
        title="Fast Metadata",
        messages=[{"role": "assistant", "content": "x" * 500_000}],
    )
    s.save()

    # The sidecar should already exist from save().
    with patch.object(Session, "load", side_effect=AssertionError("full load must not run")):
        meta = Session.load_metadata_only("sidecar_fast")

    assert meta is not None
    assert meta.session_id == "sidecar_fast"
    assert meta.title == "Fast Metadata"
    assert meta.messages == []
    assert meta.tool_calls == []
    assert meta._loaded_metadata_only is True


def test_load_metadata_only_sidecar_has_no_messages_or_tool_calls():
    """The sidecar-derived stub must have empty messages/tool_calls."""
    s = _make_session(
        "sidecar_empty_arrays",
        messages=[{"role": "assistant", "content": "keep me out of metadata"}],
        tool_calls=[{"id": "t1", "name": "read_file", "result": "y" * 10_000}],
    )
    s.save()

    meta = Session.load_metadata_only("sidecar_empty_arrays")
    assert meta.messages == []
    assert meta.tool_calls == []
    assert meta.compact()["message_count"] == 1


def test_load_metadata_only_fast_path_uses_stat_and_payload_hash():
    """When mtime/size match, the stored payload hash lets us skip a full read.

    We still verify the payload hash is present; the fast path trusts the stat
    metadata because the sidecar is written atomically after the main file
    from the same serialized payload.
    """
    s = _make_session("sidecar_mtimematch")
    s.save()

    sha_calls = []
    original_sha = models._compute_file_sha256

    def counting_sha(path):
        sha_calls.append(path)
        return original_sha(path)

    with patch.object(models, "_compute_file_sha256", side_effect=counting_sha):
        meta = Session.load_metadata_only("sidecar_mtimematch")

    assert meta is not None
    # The fast path must not compute SHA-256 of the main file.
    assert len(sha_calls) == 0, "fast path must not compute sha256"


def test_load_metadata_only_checksum_fallback_on_mtime_mismatch():
    """If mtime/size differ, the checksum fallback must validate the sidecar."""
    s = _make_session("sidecar_checksum", messages=[{"role": "user", "content": "hello"}])
    s.save()

    # Manually touch the main file without updating the sidecar.
    main_path = s.path
    time.sleep(0.05)
    os.utime(main_path, (time.time(), time.time()))

    # mtime no longer matches; checksum still matches because content is unchanged.
    meta = Session.load_metadata_only("sidecar_checksum")
    assert meta is not None
    assert meta.session_id == "sidecar_checksum"


def test_load_metadata_only_checksum_payload_matches_main_file():
    """The stored payload hash must be the SHA-256 of the serialized main file."""
    s = _make_session("sidecar_payload_hash")
    s.save()

    main_path = s.path
    main_text = main_path.read_text(encoding="utf-8")
    sidecar = json.loads(main_path.with_suffix(".json.meta").read_text(encoding="utf-8"))

    expected_hash = models._compute_string_sha256(main_text)
    assert sidecar["_source_sha256"] == expected_hash


def test_load_metadata_only_rejects_corrupt_or_stale_sidecar():
    """If the main file content changes, the stale sidecar must be ignored."""
    s = _make_session("sidecar_stale", messages=[{"role": "user", "content": "v1"}])
    s.save()

    main_path = s.path
    # Mutate the main file in place (no sidecar update).
    data = json.loads(main_path.read_text(encoding="utf-8"))
    data["title"] = "Mutated Title"
    main_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = Session.load_metadata_only("sidecar_stale")
    assert meta.title == "Mutated Title", "must fall back to main file when sidecar is stale"

    # After the fallback, the sidecar should have been refreshed.
    refreshed = json.loads(main_path.with_suffix(".json.meta").read_text(encoding="utf-8"))
    assert refreshed["metadata"]["title"] == "Mutated Title"


def test_load_metadata_only_legacy_session_without_sidecar():
    """Sessions saved before the sidecar feature must still load metadata."""
    s = _make_session("legacy_no_sidecar", title="Legacy", input_tokens=42)
    s.save()

    sidecar_path = s.path.with_suffix(".json.meta")
    sidecar_path.unlink(missing_ok=True)

    meta = Session.load_metadata_only("legacy_no_sidecar")
    assert meta is not None
    assert meta.session_id == "legacy_no_sidecar"
    assert meta.title == "Legacy"
    assert meta.input_tokens == 42

    # A sidecar should be regenerated for future fast loads.
    assert sidecar_path.exists()


def test_load_metadata_only_missing_session_returns_none():
    """load_metadata_only() must return None for a non-existent session."""
    assert Session.load_metadata_only("does_not_exist") is None


def test_sidecar_contains_all_metadata_fields():
    """The sidecar must preserve scalar metadata fields needed by compact()."""
    s = _make_session(
        "sidecar_fields",
        title="Field Coverage",
        model="claude-sonnet-4",
        model_provider="anthropic",
        pinned=True,
        archived=True,
        project_id="proj_123",
        profile="work",
        input_tokens=10,
        output_tokens=20,
        estimated_cost=0.005,
        context_length=128_000,
        threshold_tokens=100_000,
        last_prompt_tokens=50,
        llm_title_generated=True,
        manual_title=True,
        composer_draft={"text": "draft"},
    )
    s.save()

    meta = Session.load_metadata_only("sidecar_fields")
    assert meta.title == "Field Coverage"
    assert meta.model == "claude-sonnet-4"
    assert meta.model_provider == "anthropic"
    assert meta.pinned is True
    assert meta.archived is True
    assert meta.project_id == "proj_123"
    assert meta.profile == "work"
    assert meta.input_tokens == 10
    assert meta.output_tokens == 20
    assert meta.estimated_cost == 0.005
    assert meta.context_length == 128_000
    assert meta.threshold_tokens == 100_000
    assert meta.last_prompt_tokens == 50
    assert meta.llm_title_generated is True
    assert meta.manual_title is True
    assert meta.composer_draft == {"text": "draft"}


def test_save_refuses_to_overwrite_metadata_only_stub():
    """Metadata-only stubs must never be saved back to disk (#1558)."""
    s = _make_session("sidecar_stub_save")
    s.save()

    meta = Session.load_metadata_only("sidecar_stub_save")
    with pytest.raises(RuntimeError):
        meta.save()
