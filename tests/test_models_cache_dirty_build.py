"""Regression tests for the dirty-build /api/models disk-cache acceptance."""

import api.config as config


def test_is_loadable_disk_cache_accepts_dirty_build_with_digest_suffix():
    """Dirty builds with -dirty-<hash> must reuse the disk cache across restarts."""
    cache = {
        "_schema_version": config._MODELS_CACHE_SCHEMA_VERSION,
        "_webui_version": "v0.50.293-dirty-aaaa1111",
        "_source_fingerprint": config._models_cache_source_fingerprint(),
        "active_provider": None,
        "default_model": "test-model",
        "configured_model_badges": {},
        "groups": [],
    }

    def _fake_current_version():
        return "v0.50.293-dirty-bbbb2222"

    original = config._current_webui_version
    try:
        config._current_webui_version = _fake_current_version
        assert config._is_loadable_disk_cache(cache) is True
    finally:
        config._current_webui_version = original


def test_is_loadable_disk_cache_rejects_different_base_version():
    """A dirty build from a different release base must not reuse the cache."""
    cache = {
        "_schema_version": config._MODELS_CACHE_SCHEMA_VERSION,
        "_webui_version": "v0.50.292-dirty-aaaa1111",
        "_source_fingerprint": config._models_cache_source_fingerprint(),
        "active_provider": None,
        "default_model": "test-model",
        "configured_model_badges": {},
        "groups": [],
    }

    def _fake_current_version():
        return "v0.50.293-dirty-bbbb2222"

    original = config._current_webui_version
    try:
        config._current_webui_version = _fake_current_version
        assert config._is_loadable_disk_cache(cache) is False
    finally:
        config._current_webui_version = original


def test_is_loadable_disk_cache_accepts_clean_release_match():
    """A clean release version must still match exactly."""
    cache = {
        "_schema_version": config._MODELS_CACHE_SCHEMA_VERSION,
        "_webui_version": "v0.50.293",
        "_source_fingerprint": config._models_cache_source_fingerprint(),
        "active_provider": None,
        "default_model": "test-model",
        "configured_model_badges": {},
        "groups": [],
    }

    def _fake_current_version():
        return "v0.50.293"

    original = config._current_webui_version
    try:
        config._current_webui_version = _fake_current_version
        assert config._is_loadable_disk_cache(cache) is True
    finally:
        config._current_webui_version = original


def test_is_loadable_disk_cache_rejects_clean_mismatch():
    """Different clean release versions must not reuse the cache."""
    cache = {
        "_schema_version": config._MODELS_CACHE_SCHEMA_VERSION,
        "_webui_version": "v0.50.292",
        "_source_fingerprint": config._models_cache_source_fingerprint(),
        "active_provider": None,
        "default_model": "test-model",
        "configured_model_badges": {},
        "groups": [],
    }

    def _fake_current_version():
        return "v0.50.293"

    original = config._current_webui_version
    try:
        config._current_webui_version = _fake_current_version
        assert config._is_loadable_disk_cache(cache) is False
    finally:
        config._current_webui_version = original
