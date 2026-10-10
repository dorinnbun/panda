"""Runtime configuration, read once from environment variables (or a .env file)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the app runs without python-dotenv."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


ROOT = Path(__file__).resolve().parent.parent
_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    model: str = field(default_factory=lambda: _env("PANDA_MODEL", "claude-opus-5-5"))
    # low keeps voice replies snappy; raise to medium/high for deeper reasoning.
    effort: str = field(default_factory=lambda: _env("PANDA_EFFORT", "low"))
    # Server-side refusal fallback (Claude API only). Set to "off" to disable.
    fallbacks: str = field(default_factory=lambda: _env("PANDA_FALLBACKS", "default"))
    web_search: bool = field(default_factory=lambda: _env("PANDA_WEB_SEARCH", "on") != "off")

    assistant_name: str = field(default_factory=lambda: _env("ASSISTANT_NAME", "Panda"))
    owner_name: str = field(default_factory=lambda: _env("OWNER_NAME", "User"))
    owner_title: str = field(default_factory=lambda: _env("OWNER_TITLE", "sir"))
    timezone: str = field(default_factory=lambda: _env("TZ", "UTC"))
    default_location: str = field(default_factory=lambda: _env("DEFAULT_LOCATION", ""))

    access_password: str = field(default_factory=lambda: _env("ACCESS_PASSWORD"))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY") or secrets.token_hex(32))
    cookie_secure: bool = field(default_factory=lambda: _env("COOKIE_SECURE", "true") != "false")

    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT / "data"))))

    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(default_factory=lambda: _env("ELEVENLABS_VOICE_ID"))
    ntfy_topic_url: str = field(default_factory=lambda: _env("NTFY_TOPIC_URL"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "panda.db"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
