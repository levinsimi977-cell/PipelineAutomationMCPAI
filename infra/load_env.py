"""Load project-root .env into os.environ for all agents and the UI."""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

_IOS_APP_ID_RE = re.compile(r"^(?:id)?(\d+)$", re.IGNORECASE)
_IOS_APP_ID_TYPO_RE = re.compile(r"^d(\d+)$", re.IGNORECASE)
_DEFAULT_IOS_APP_ID = "id1512793879"

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


def normalize_ios_app_id(value: str | None) -> str | None:
    """Return ``id<number>`` for valid iOS Apple App IDs (fixes ``d<number>`` typos)."""
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip()
    typo = _IOS_APP_ID_TYPO_RE.match(candidate)
    if typo:
        candidate = f"id{typo.group(1)}"
    match = _IOS_APP_ID_RE.match(candidate)
    if not match:
        return None
    return f"id{match.group(1)}"


def resolve_app_id_for_platform(platform: str, app_id: str | None = None) -> str | None:
    """Platform-aware APP_ID for MCP / pipeline state."""
    normalized_platform = (platform or "").strip().lower()
    if normalized_platform == "ios":
        for candidate in (app_id, os.getenv("IOS_APP_ID"), os.getenv("APP_ID"), _DEFAULT_IOS_APP_ID):
            resolved = normalize_ios_app_id(candidate)
            if resolved:
                return resolved
        return None
    if app_id and str(app_id).strip():
        return str(app_id).strip()
    env_key = "ANDROID_APP_ID" if normalized_platform == "android" else "APP_ID"
    return os.getenv(env_key) or os.getenv("APP_ID")


def get_app_id_for_platform(platform: str) -> str | None:
    """Return platform-specific APP_ID from env (``IOS_APP_ID`` / ``ANDROID_APP_ID``)."""
    return resolve_app_id_for_platform(platform)


load_project_env()
