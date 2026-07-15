"""Load project-root .env into os.environ for all agents and the UI."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
_loaded = False


def _normalize_env_aliases() -> None:
    """Map alternate .env key names used in this repo to canonical env vars."""
    if not os.getenv("OPENAI_API_KEY") and os.getenv("GPT_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["GPT_API_KEY"]
    if not os.getenv("APPSFLYER_DEV_KEY") and os.getenv("DEV_KEY"):
        os.environ["APPSFLYER_DEV_KEY"] = os.environ["DEV_KEY"]


def load_project_env(*, override: bool = False) -> Path:
    """Load ``PiplineAutomatoinMCP/.env`` once (idempotent)."""
    global _loaded
    if _ENV_PATH.is_file():
        load_dotenv(_ENV_PATH, override=override)
    _normalize_env_aliases()
    _loaded = True
    return _ENV_PATH


def get_dev_key() -> str | None:
    """Return AppsFlyer dev key from ``DEV_KEY`` or ``APPSFLYER_DEV_KEY``."""
    return os.getenv("DEV_KEY") or os.getenv("APPSFLYER_DEV_KEY")


def get_app_id_for_platform(platform: str) -> str | None:
    """Return platform-specific APP_ID from env (``IOS_APP_ID`` / ``ANDROID_APP_ID``)."""
    normalized = (platform or "").strip().lower()
    if normalized == "ios":
        return os.getenv("IOS_APP_ID") or os.getenv("APP_ID")
    if normalized == "android":
        return os.getenv("ANDROID_APP_ID") or os.getenv("APP_ID")
    return os.getenv("APP_ID")


load_project_env()
