"""Runtime settings (.env) and the user list (users.yaml)."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, SecretStr, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from noty.comments import is_safe_segment


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jotty_url: str = Field(description="Jotty base URL reachable from this container, e.g. http://jotty:3000")
    app_url: str | None = Field(default=None, description="Public Jotty URL used for links in messages")
    telegram_bot_token: SecretStr
    poll_interval: int = Field(default=60, ge=5)
    request_timeout: float = Field(default=15.0, gt=0)
    users_file: Path = Path("/app/users.yaml")
    state_dir: Path = Path("/app/state")
    jotty_data_dir: Path | None = Field(
        default=None, description="Read-only mount of Jotty's data directory, needed for comments"
    )
    board_retention_days: int = Field(
        default=30, ge=1, description="How long to remember a board that disappeared (archived, unshared)"
    )
    log_level: str = "INFO"

    @field_validator("jotty_data_dir", "app_url", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # `JOTTY_DATA_DIR=` would otherwise become Path("."), silently pointing at the working directory.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        level = value.upper()
        if not isinstance(logging.getLevelName(level), int):
            raise ValueError(f"unknown log level {value!r}")
        return level


class UserConfig(BaseModel):
    username: str
    api_key: SecretStr
    # Telegram accepts a numeric chat id or an @channel username.
    chat_id: int | str

    @field_validator("username")
    @classmethod
    def _safe_username(cls, value: str) -> str:
        # The username also names the state file, so it must be a safe path segment.
        if not is_safe_segment(value):
            raise ValueError("username must be non-empty and contain no '/', '\\' or '..'")
        return value


_users_adapter = TypeAdapter(list[UserConfig])


class ConfigError(Exception):
    pass


def load_users(path: Path) -> list[UserConfig]:
    """Load and validate users.yaml: a top-level list of {username, api_key, chat_id}."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read users file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        # Report only the position: the offending line may contain an api_key.
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise ConfigError(f"Invalid YAML in {path}{where}") from None

    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{path} must contain a non-empty list of users")

    users = _users_adapter.validate_python(raw)

    seen: set[str] = set()
    for user in users:
        if user.username in seen:
            raise ConfigError(f"Duplicate username in {path}: {user.username}")
        seen.add(user.username)
    return users
