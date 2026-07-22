"""
Deep Link Node — סימולציה בלבד (קבוצה 5).

מטרת ה-Node:
  לדמות קישור עמוק (Deep Link) של AppsFlyer ולדמות לחיצה עליו במכשיר.
  ה-Node הזה **לא** בודק אם ה-SDK הוטמע נכון — זה תפקיד הצוות הבא (MCP Listener).

תהליך:
  1. ממציאים קישור OneLink דינמי (Mock)
  2. מפעילים אותו על הסימולטור / אמולטור (כאילו המשתמש לחץ עליו)
  3. שומרים את הקישור ב-state כדי שהצוות הבא יוכל לבדוק אם ה-SDK הגיב

מה הצוות הבא צריך לקרוא מה-state:
  - triggered_deep_link_url  → הקישור שהופעל
  - deep_link_status         → SUCCESS / SKIPPED / FAILED (הזרקה בלבד)
  - platform                 → ios / android
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# ---------------------------------------------------------------------------
# קבועים — פרמטרי AppsFlyer שקבוצת ה-MCP Listener מכירה
# ---------------------------------------------------------------------------
DEFAULT_PACKAGE = "com.appsflyer.automation.sandbox"
DEFAULT_MEDIA_SOURCE = "langgraph_pipeline_automation"
DEFAULT_CAMPAIGN = "post_emulator_verification_run"
DEFAULT_ONELINK_ROUTE = "testRoute"


# ---------------------------------------------------------------------------
# 1. המצאת קישור Deep Link של AppsFlyer
# ---------------------------------------------------------------------------

def _get_deeplink_policy(state: dict[str, Any]) -> dict[str, Any]:
    policy = state.get("answer_policy") or {}
    deeplink = policy.get("deeplink")
    return deeplink if isinstance(deeplink, dict) else {}


def _resolve_app_identifier(state: dict[str, Any]) -> str:
    """שולף package (Android) או bundle_id (iOS) מה-state."""
    return (
        state.get("package")
        or state.get("bundle_id")
        or state.get("app_id")
        or DEFAULT_PACKAGE
    )


def generate_mock_deep_link(state: dict[str, Any]) -> str:
    """
    ממציאה קישור OneLink דינמי של AppsFlyer על בסיס ה-package/bundle_id.

    פורמט:
        https://{package}.onelink.me/{route}?pid={media_source}&c={campaign}
    """
    policy = _get_deeplink_policy(state)
    app_id = _resolve_app_identifier(state)
    media_source = policy.get("media_source") or DEFAULT_MEDIA_SOURCE
    campaign = policy.get("campaign") or DEFAULT_CAMPAIGN
    route = policy.get("onelink_route") or DEFAULT_ONELINK_ROUTE

    return (
        f"https://{app_id}.onelink.me/{route}"
        f"?pid={media_source}&c={campaign}"
    )


def _append_appsflyer_params(url: str, media_source: str, campaign: str) -> str:
    """מוסיפה pid ו-c לקישור OneLink קיים אם חסרים."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "pid" not in query:
        query["pid"] = [media_source]
    if "c" not in query:
        query["c"] = [campaign]
    flat_query = urlencode({key: values[0] for key, values in query.items()})
    return urlunparse(parsed._replace(query=flat_query))


def extract_deep_link_url_from_audit(audit_recorder: Any) -> str | None:
    if audit_recorder is None:
        return None
    for payload in reversed(audit_recorder.mcp_tool_results()):
        if payload.get("tool") not in ("createDeepLink", "createIosDeepLink"):
            continue
        text = payload.get("result") or ""
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                for value in data.values():
                    if isinstance(value, str) and "://" in value:
                        return value
        except json.JSONDecodeError:
            pass
        match = re.search(r"https?://\S+", text)
        if match:
            return match.group(0).rstrip('",}')
    return None


def build_deep_link_url(state: dict[str, Any]) -> str:
    """
    בונה את הקישור הסופי לפי סדר עדיפויות:
      1. deep_link_url מה-agent (MCP)
      2. onelink_url מה-use case (עם pid/c של AppsFlyer)
      3. Custom URI scheme (myapp://offers)
      4. Mock OneLink דינמי (generate_mock_deep_link)
    """
    agent_url = state.get("deep_link_url")
    if isinstance(agent_url, str) and agent_url.strip():
        return agent_url.strip()

    policy = _get_deeplink_policy(state)
    media_source = policy.get("media_source") or DEFAULT_MEDIA_SOURCE
    campaign = policy.get("campaign") or DEFAULT_CAMPAIGN

    onelink_url = policy.get("onelink_url")
    if onelink_url:
        return _append_appsflyer_params(onelink_url, media_source, campaign)

    if policy.get("use_custom_uri_scheme") and policy.get("uri_scheme"):
        scheme = policy["uri_scheme"]
        path = policy.get("url_identifier", "")
        return f"{scheme}://{path}" if path else f"{scheme}://"

    return generate_mock_deep_link(state)


# ---------------------------------------------------------------------------
# 2. איסוף לוגים מהסימולטור לאחר שליחת Deep Link
# ---------------------------------------------------------------------------

def _collect_ios_deeplink_logs(sandbox_path: str, wait_seconds: float = 5.0) -> str:
    """Wait for the AppsFlyer SDK callback to fire, then collect AppsFlyer-related
    lines from the iOS simulator system log.

    Writes the output to ios-deeplink-logs.txt inside `sandbox_path` so that
    the SDK agent can call verifyIosDeepLink(action="verify", logFilePath=...,
    confirmLogFileReady=True) without any manual log-pasting step.

    Returns the absolute path to the written file.
    """
    time.sleep(wait_seconds)  # let the SDK deep-link callback fire

    result = subprocess.run(
        [
            "xcrun", "simctl", "spawn", "booted",
            "log", "show",
            "--predicate", 'subsystem CONTAINS "appsflyer" OR process CONTAINS "AppsFlyer"',
            "--last", "30s",
            "--style", "compact",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    log_file = Path(sandbox_path) / "ios-deeplink-logs.txt"
    log_file.write_text(result.stdout or "", encoding="utf-8")
    return str(log_file)


# ---------------------------------------------------------------------------
# 2. הפעלת Deep Link — iOS
# ---------------------------------------------------------------------------

class IOSDeepLinkAdapter:
    """מדמה לחיצה על Deep Link ב-iOS Simulator (xcrun simctl openurl)."""

    def __init__(self, device_id: str = "booted") -> None:
        self.device_id = device_id

    def trigger_deep_link(self, url: str) -> None:
        subprocess.run(
            ["xcrun", "simctl", "openurl", self.device_id, url],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"[iOS] Deep link triggered: {url}")


# ---------------------------------------------------------------------------
# 3. הפעלת Deep Link — Android
# ---------------------------------------------------------------------------

class AndroidDeepLinkAdapter:
    """מדמה לחיצה על Deep Link ב-Android Emulator (adb intent)."""

    def __init__(self, device_id: str | None = None) -> None:
        self.device_id = device_id

    def trigger_deep_link(self, url: str) -> None:
        cmd = ["adb"]
        if self.device_id:
            cmd.extend(["-s", self.device_id])
        cmd.extend(
            [
                "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", url,
            ]
        )
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"[Android] Deep link triggered: {url}")


# ---------------------------------------------------------------------------
# 4. בחירת Adapter לפי פלטפורמה
# ---------------------------------------------------------------------------

def _resolve_device_id(platform: str, state: dict[str, Any]) -> str | None:
    if platform == "ios":
        return state.get("device_id") or "booted"

    if platform == "android":
        android_policy = (state.get("answer_policy") or {}).get("android") or {}
        return android_policy.get("device_id") or state.get("device_id")

    return None


def _get_adapter(
    platform: str, state: dict[str, Any]
) -> IOSDeepLinkAdapter | AndroidDeepLinkAdapter:
    device_id = _resolve_device_id(platform, state)

    if platform == "ios":
        return IOSDeepLinkAdapter(device_id or "booted")

    if platform == "android":
        return AndroidDeepLinkAdapter(device_id=device_id)

    raise ValueError(f"Unsupported platform: {platform!r}. Must be 'ios' or 'android'.")


# ---------------------------------------------------------------------------
# 5. הרצה מלאה — בניית URL + הפעלה על המכשיר
# ---------------------------------------------------------------------------

def _should_skip(state: dict[str, Any]) -> tuple[bool, str]:
    if not state.get("compilation_passed", False):
        return True, "Deep link skipped: compilation failed."

    policy = _get_deeplink_policy(state)
    if policy.get("use_deep_linking") is False:
        return True, "Deep link disabled in use case policy."

    return False, ""


def simulate_deep_link_click(state: dict[str, Any]) -> dict[str, Any]:
    """
    מדמה את כל תהליך הלחיצה על Deep Link:
      1. בונה קישור AppsFlyer (Mock)
      2. מפעיל אותו על המכשיר לפי הפלטפורמה

    מחזירה רק סטטוס הזרקה — לא תוצאת אימות SDK.
    הצוות הבא (MCP Listener) בודק אם ה-SDK הגיב לקישור.
    """
    skip, reason = _should_skip(state)
    if skip:
        return {"deep_link_status": "SKIPPED", "nodes_logs": reason}

    platform = (state.get("platform") or "").lower()
    if platform not in {"ios", "android"}:
        return {
            "deep_link_status": "FAILED",
            "error_reason": f"Unsupported or missing platform: {platform!r}",
            "nodes_logs": f"Deep link simulation failed: unsupported platform {platform!r}",
        }

    url = build_deep_link_url(state)

    try:
        adapter = _get_adapter(platform, state)
        adapter.trigger_deep_link(url)

        extra: dict[str, Any] = {}
        if platform == "ios":
            sandbox_path = state.get("sandbox_path") or state.get("app_path") or ""
            if sandbox_path:
                log_file = _collect_ios_deeplink_logs(sandbox_path)
                extra["ios_deeplink_log_file"] = log_file

        return {
            "triggered_deep_link_url": url,
            "deep_link_status": "SUCCESS",
            "nodes_logs": f"Simulated deep link click on {platform}: {url}",
            **extra,
        }
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or str(exc)).strip()
        return {
            "deep_link_status": "FAILED",
            "triggered_deep_link_url": url,
            "error_reason": stderr,
            "nodes_logs": f"Failed to simulate click on {platform}: {stderr}",
        }
    except Exception as exc:
        return {
            "deep_link_status": "FAILED",
            "triggered_deep_link_url": url,
            "error_reason": str(exc),
            "nodes_logs": (
                f"Failed to simulate deep link click on {platform}: {exc}\n"
                f"{traceback.format_exc()}"
            ),
        }


# שם ישן — לתאימות לאחור
trigger_deep_link = simulate_deep_link_click


# ---------------------------------------------------------------------------
# 6. Node 9 — Deep Link (LangGraph pipeline)
# ---------------------------------------------------------------------------

def deep_link_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node 9 — סימולציית Deep Link בלבד.

    תפקידנו: לדמות קישור + לדמות לחיצה.
    לא בודקים כאן אם ה-SDK הגיב — זה ב-node הבא (MCP Listener / SDK Agent Final).
    """
    print("[DeepLink Node] Simulating deep link click...")
    result = simulate_deep_link_click(state)
    print(f"[DeepLink Node] Injection status: {result.get('deep_link_status')}")
    return {**state, **result}
