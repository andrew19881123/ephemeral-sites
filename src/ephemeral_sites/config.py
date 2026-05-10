"""Runtime configuration for the ephemeral-sites API process.

See ``docs/steps/step-8-api-put-upsert.md`` §2.2 for the contract. The
class intentionally stays slim: one field per Helm value from master
spec §9, all with defaults matching production.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ephemeral_sites.server.headers import DEFAULT_CSP

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Process-wide config.

    Populated from environment variables (prefix ``EPHEMERAL_``) and/or a
    ``.env`` file at startup. Unknown env vars are ignored (``extra="ignore"``)
    so stale environment never crashes the pod.
    """

    model_config = SettingsConfigDict(
        env_prefix="EPHEMERAL_",
        env_file=".env",
        extra="ignore",
    )

    # --- auth ---
    api_keys: str = Field(
        default="",
        description="Raw API_KEYS value; parsed at startup via auth.parse_api_keys_env.",
    )

    # --- paths ---
    db_path: str = Field(default="/data/db/ephemeral-sites.db")
    sites_root: str = Field(default="/data/sites")
    lock_dir: str = Field(default="/data/sites/.lock")

    # --- limits (master spec §9 defaults) ---
    max_zip_size: int = Field(default=500 * 1024 * 1024)  # 500 MiB
    max_files_per_site: int = Field(default=5000)
    max_total_storage_bytes: int = Field(default=40 * 1024**3)  # 40 GiB
    max_decompression_ratio: int = Field(default=100)
    default_ttl_seconds: int = Field(default=86400)  # 1 day
    max_ttl_seconds: int = Field(default=31536000)  # 1 year
    min_ttl_seconds: int = Field(default=60)
    allow_permanent: bool = Field(default=True)

    # --- misc ---
    base_domain: str = Field(default="preview.example.test")
    bcrypt_rounds: int = Field(default=12)
    csp: str = Field(
        default=DEFAULT_CSP,
        description=(
            "Content-Security-Policy header value applied by the static "
            "server to every 200 response. Override via EPHEMERAL_CSP / "
            "Helm value app.csp for per-deployment customization."
        ),
    )

    # --- validator allowed extensions (master spec §9) ---
    allowed_extensions: tuple[str, ...] = Field(
        default=(
            ".html",
            ".htm",
            ".css",
            ".js",
            ".mjs",
            ".json",
            ".map",
            ".xml",
            ".txt",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".webp",
            ".avif",
            ".ico",
            ".woff",
            ".woff2",
            ".ttf",
            ".otf",
            ".eot",
            ".pdf",
            ".mp4",
            ".webm",
            ".mp3",
            ".wav",
            ".wasm",
        )
    )


def get_settings() -> Settings:
    """Return a fresh :class:`Settings` instance.

    In production this is called once at startup and cached by the FastAPI
    dependency layer; tests use ``app.dependency_overrides`` to swap in a
    tmp-path settings object.
    """
    return Settings()
