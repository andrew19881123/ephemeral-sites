"""Unit tests for Settings (Pydantic-based config loader).

Red tests for step 8; see ``docs/steps/step-8-api-put-upsert.md`` §3.1.
"""

from __future__ import annotations


def test_settings_defaults_match_spec(monkeypatch):
    """With no env set, Settings() returns master-spec §9 defaults."""
    # Clear every EPHEMERAL_* env to isolate defaults.
    for k in list(monkeypatch.__dict__) if False else []:
        pass  # noop; rely on explicit deletes below

    for key in (
        "EPHEMERAL_MAX_ZIP_SIZE",
        "EPHEMERAL_MAX_FILES_PER_SITE",
        "EPHEMERAL_MAX_TOTAL_STORAGE_BYTES",
        "EPHEMERAL_MAX_DECOMPRESSION_RATIO",
        "EPHEMERAL_DEFAULT_TTL_SECONDS",
        "EPHEMERAL_MAX_TTL_SECONDS",
        "EPHEMERAL_ALLOW_PERMANENT",
        "EPHEMERAL_API_KEYS",
    ):
        monkeypatch.delenv(key, raising=False)

    from ephemeral_sites.config import Settings

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.max_zip_size == 500 * 1024 * 1024
    assert s.max_files_per_site == 5000
    assert s.max_total_storage_bytes == 40 * 1024**3
    assert s.max_decompression_ratio == 100
    assert s.default_ttl_seconds == 86400
    assert s.max_ttl_seconds == 31536000
    assert s.allow_permanent is True


def test_settings_reads_env_prefix(monkeypatch):
    monkeypatch.setenv("EPHEMERAL_MAX_ZIP_SIZE", "1024")
    monkeypatch.setenv("EPHEMERAL_BASE_DOMAIN", "override.example")

    from ephemeral_sites.config import Settings

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.max_zip_size == 1024
    assert s.base_domain == "override.example"


def test_settings_ignores_unknown_env(monkeypatch):
    """Unknown EPHEMERAL_* env vars must not crash Settings() — extra='ignore'."""
    monkeypatch.setenv("EPHEMERAL_TOTALLY_UNKNOWN_FIELD", "xyz")

    from ephemeral_sites.config import Settings

    # Must not raise.
    Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_api_keys_pass_through():
    """The api_keys raw string is stored as-is on Settings; parsing happens
    elsewhere (auth.parse_api_keys_env)."""
    from ephemeral_sites.config import Settings

    s = Settings(api_keys="main:x,ci:y", _env_file=None)  # type: ignore[call-arg]
    assert s.api_keys == "main:x,ci:y"
